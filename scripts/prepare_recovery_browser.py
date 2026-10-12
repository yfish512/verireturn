"""Create owned, isolated development data for an actual browser confirmation test."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import httpx
from backend.app.auth import AuthSettings
from backend.app.database import SessionLocal
from backend.app.models import Order, OrderItem, InventoryStock


def main():
    if AuthSettings().app_env.lower() == 'production':raise RuntimeError('FAULT_DEMO_DISABLED')
    tag=str(uuid4()).replace('-','')[:10];oid='O9'+str(int(datetime.now().timestamp()))+'2';sku='BROWSER-'+tag
    with SessionLocal() as db:
        db.add(Order(id=oid,user_id='U001',item_name='浏览器恢复验收商品',amount=88,delivered_at=datetime.now(timezone.utc),condition='sealed',quality_issue=True));db.flush()
        db.add(OrderItem(id='line-'+oid,order_id=oid,sku=sku,title='浏览器恢复验收商品',quantity=1,unit_amount=88))
        db.add(InventoryStock(sku=sku,available_quantity=1,reserved_quantity=0));db.commit()
    with httpx.Client(base_url='http://127.0.0.1:8000',headers={'X-Demo-User-Id':'U001'},timeout=120) as c:
        def post(path,body=None):
            r=c.post(path,json=body);r.raise_for_status();return r.json()
        thread=post('/agent/threads')['thread_id']
        created=post(f'/agent/threads/{thread}/messages',{'message':f'我要换货 {oid}，商品质量问题','message_id':'browser-'+tag})
        assert created['confirmation_id'],created
        with SessionLocal() as db:
            stock=db.get(InventoryStock,sku);stock.available_quantity=0;stock.version+=1;db.commit()
        confirmed=post(f"/agent/confirmations/{created['confirmation_id']}",{'approved':True})
        assert confirmed['recovery_id'],confirmed
    out={'thread_id':thread,'recovery_id':confirmed['recovery_id'],'case_id':created['case_id']}
    Path('.local/recovery-browser-fixture.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out))

if __name__=='__main__':main()
