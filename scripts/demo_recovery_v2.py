"""Exercise actual API recovery -> new confirmation -> signed simulator settlement."""
import hashlib,hmac,json,time
from datetime import datetime,timezone
from pathlib import Path
from decimal import Decimal
from uuid import uuid4
import httpx
from sqlalchemy import select
from pydantic_settings import BaseSettings,SettingsConfigDict
from backend.app.database import SessionLocal
from backend.app.models import Order,OrderItem,InventoryStock,PaymentTransaction,ReplacementOption,RefundIntent,AfterSalesCase
from backend.app.auth import AuthSettings

class Secrets(BaseSettings):
    model_config=SettingsConfigDict(env_file='.env',extra='ignore',hide_input_in_errors=True)
    fulfillment_webhook_secret:str
    payment_webhook_secret:str


def main():
    if AuthSettings().app_env=='production':raise RuntimeError('Demo is restricted to development')
    secrets=Secrets();tag=str(int(time.time()));oid='O9'+tag;sku='RECOVERY-DEMO-'+tag
    with SessionLocal() as db:
        db.add(Order(id=oid,user_id='U001',item_name='恢复演示商品',amount=Decimal('88'),delivered_at=datetime.now(timezone.utc),condition='sealed',quality_issue=True));db.flush()
        db.add(OrderItem(id='line-'+oid,order_id=oid,sku=sku,title='恢复演示商品',quantity=1,unit_amount=Decimal('88')))
        db.add_all([InventoryStock(sku=sku,available_quantity=1,reserved_quantity=0),InventoryStock(sku=sku+'-ALT',available_quantity=1,reserved_quantity=0)]);db.flush()
        db.add(ReplacementOption(id=str(uuid4()),original_sku=sku,replacement_sku=sku+'-ALT'))
        db.add(PaymentTransaction(id='pay-'+oid,order_id=oid,user_id='U001',provider='demo_payment',provider_payment_id='pay-'+oid,amount=Decimal('88'),status='captured'));db.commit()
    client=httpx.Client(base_url='http://127.0.0.1:8000',timeout=120,headers={'X-Demo-User-Id':'U001'})
    def request(method,path,body=None,headers=None):
        r=client.request(method,path,json=body,headers=headers);r.raise_for_status();return r.json()
    def webhook(provider,path,payload,secret):
        raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        headers={'Content-Type':'application/json','X-Provider-Event-Id':payload['event_id'],
            'X-Provider-Timestamp':datetime.now(timezone.utc).isoformat(),
            'X-Provider-Signature':'sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()}
        r=client.post(path,content=raw,headers=headers);r.raise_for_status();return r.json()
    results=[]
    thread=request('POST','/agent/threads')['thread_id']
    initial=request('POST',f'/agent/threads/{thread}/messages',{'message':f'帮我换 {oid}，商品有质量问题','message_id':'recovery-demo-'+tag})
    assert initial['confirmation_id'],initial
    with SessionLocal() as db:
        stock=db.get(InventoryStock,sku);stock.available_quantity=0;stock.version+=1;db.commit()
    confirmed=request('POST',f"/agent/confirmations/{initial['confirmation_id']}",{'approved':True})
    assert confirmed['recovery_id'],confirmed
    recovery=request('GET',f"/agent/recoveries/{confirmed['recovery_id']}")
    assert recovery['options'] and recovery['case_id']==initial['case_id']
    denied=client.get(f"/agent/recoveries/{confirmed['recovery_id']}",headers={'X-Demo-User-Id':'U002'})
    assert denied.status_code==404
    proposal=request('POST',f"/tools/after-sales/cases/{initial['case_id']}/resolutions",{'option':'refund'},{'Idempotency-Key':'demo-refund-'+tag})
    applied=request('POST',f"/tools/after-sales/resolutions/{proposal['id']}/confirm",{'expected_version':proposal['version'],'approved':True})
    refund_case=applied['linked_case_id'];assert refund_case
    request('POST',f'/tools/after-sales/cases/{refund_case}/pickup',{'time_slot':'明天上午'},{'Idempotency-Key':'demo-pickup-'+tag})
    def fulfill(case_id,typ,seq):
        payload={'event_id':f'demo-{tag}-{case_id}-{seq}','case_id':case_id,'event_type':typ,'sequence_no':seq,
            'occurred_at':datetime.now(timezone.utc).isoformat(),'payload':{'tracking_number':'DEMO-'+tag}}
        return webhook('demo_fulfillment','/internal/fulfillment/webhooks/demo_fulfillment',payload,secrets.fulfillment_webhook_secret)
    fulfill(refund_case,'pickup.collected',1);fulfill(refund_case,'return.received',2)
    for _ in range(30):
        with SessionLocal() as db:
            intent=db.scalar(select(RefundIntent).where(RefundIntent.case_id==refund_case))
            if intent and intent.provider_refund_id:
                pay={'event_id':'payment-demo-'+tag,'provider_refund_id':intent.provider_refund_id,'outcome':'succeeded',
                    'amount':str(intent.amount),'currency':intent.currency,'occurred_at':datetime.now(timezone.utc).isoformat(),'payload':{}}
                break
        time.sleep(.5)
    else:raise RuntimeError('payment worker did not submit refund')
    settled=webhook('demo_payment','/internal/payments/webhooks/demo_payment',pay,secrets.payment_webhook_secret)
    assert settled['status']=='succeeded'
    assert webhook('demo_payment','/internal/payments/webhooks/demo_payment',pay,secrets.payment_webhook_secret)['status']=='succeeded'
    snapshot=request('GET',f'/agent/threads/{thread}')
    assert any(m.get('payload',{}).get('recovery_id')==confirmed['recovery_id'] for m in snapshot['messages'] if m.get('payload'))
    # Second actual API path: explicitly authorized alternative replacement.
    oid2=oid+'1';sku2=sku+'-2'
    with SessionLocal() as db:
        db.add(Order(id=oid2,user_id='U001',item_name='替换演示商品',amount=Decimal('99'),delivered_at=datetime.now(timezone.utc),condition='sealed',quality_issue=True));db.flush()
        db.add(OrderItem(id='line-'+oid2,order_id=oid2,sku=sku2,title='替换演示商品',quantity=1,unit_amount=Decimal('99')))
        db.add(InventoryStock(sku=sku2,available_quantity=0,reserved_quantity=0));db.flush()
        db.add(ReplacementOption(id=str(uuid4()),original_sku=sku2,replacement_sku=sku+'-ALT'));db.commit()
    case=request('POST','/tools/after-sales/cases',{'order_id':oid2,'request_type':'exchange','reason':'质量问题'},{'Idempotency-Key':'demo-exchange-'+tag})
    request('POST',f"/tools/after-sales/cases/{case['id']}/confirm",headers={'Idempotency-Key':'demo-confirm-'+tag})
    proposal2=request('POST',f"/tools/after-sales/cases/{case['id']}/resolutions",{'option':'exchange','replacement_sku':sku+'-ALT'},{'Idempotency-Key':'demo-alt-'+tag})
    request('POST',f"/tools/after-sales/resolutions/{proposal2['id']}/confirm",{'expected_version':1,'approved':True})
    request('POST',f"/tools/after-sales/cases/{case['id']}/pickup",{'time_slot':'明天上午'},{'Idempotency-Key':'demo-alt-pickup-'+tag})
    for seq,typ in enumerate(['pickup.collected','return.received','replacement.shipped','replacement.delivered'],1):fulfill(case['id'],typ,seq)
    exchange=request('GET',f"/tools/after-sales/cases/{case['id']}/fulfillment")
    assert exchange['status']=='completed'
    report={'thread_id':thread,'recovery_id':confirmed['recovery_id'],'refund_case_id':refund_case,'refund_status':settled['status'],
        'exchange_case_id':case['id'],'exchange_status':exchange['status'],'cross_customer_denied':True,'callback_replay_safe':True}
    target=Path('.local/recovery-v2-demo.json');target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
