"""Isolated real-domain world with a separately committed simulator channel ledger."""
import json, sqlite3, tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from ..database import Base
from ..seed import seed_demo_data
from ..models import (Order, OrderItem, InventoryStock, PaymentTransaction, AfterSalesCase, AfterSalesItem,
    RefundIntent, RefundAttempt, ExchangeFulfillment, InventoryReservation, ReplacementOption, RecoveryExecution, AfterSalesResolution, FulfillmentIncident)
from ..schemas import AfterSalesCreateRequest
from ..domain.service import create_case, confirm_case, schedule_pickup, cancel_case
from ..domain.payments import ensure_refund_intent, submit_refund_provider, apply_refund_settlement
from ..domain.fulfillment import receive_provider_event, _create_incident
from ..domain.resolutions import resolution_options, propose_resolution, confirm_resolution


class Channel:
    def __init__(self,path):
        self.path=str(path)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS refunds (key TEXT PRIMARY KEY, ref TEXT, amount TEXT, status TEXT)")
    def request_refund(self,payload,idempotency_key):
        ref=f"demo-refund-{payload['intent_id']}"
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR IGNORE INTO refunds VALUES (?,?,?,?)",(idempotency_key,ref,payload["amount"],"submitted"))
        return {"accepted":True,"provider_refund_id":ref}
    def get(self,key):
        with sqlite3.connect(self.path) as db:
            row=db.execute("SELECT ref,amount,status FROM refunds WHERE key=?",(key,)).fetchone()
        return None if row is None else {"provider_refund_id":row[0],"amount":row[1],"status":row[2]}
    def settle(self,key):
        with sqlite3.connect(self.path) as db: db.execute("UPDATE refunds SET status='succeeded' WHERE key=?",(key,))


class World:
    def __init__(self,case,directory=None):
        self.case=case
        self.temporary=None if directory else tempfile.TemporaryDirectory(prefix="verireturn-recovery-")
        self.directory=Path(directory or self.temporary.name); self.directory.mkdir(parents=True,exist_ok=True)
        self.engine=create_engine(f"sqlite:///{self.directory/'business.db'}")
        Base.metadata.create_all(self.engine); self.factory=sessionmaker(bind=self.engine)
        self.channel=Channel(self.directory/'channel.db')
        self.actor="U001"; self.key="episode"
        with self.factory() as db:
            existing=db.scalar(select(RecoveryExecution).where(RecoveryExecution.command_key==self.key))
            if existing:
                self.execution_id=existing.id
                meta=existing.state_json["world"]
                self.case_id,self.intent_id=meta["case_id"],meta["intent_id"]
                # Domain confirmation may commit before the graph can save the linked case.
                resolution=db.scalar(select(AfterSalesResolution).where(
                    AfterSalesResolution.case_id==self.case_id, AfterSalesResolution.status=="applied",
                    AfterSalesResolution.linked_case_id.is_not(None)))
                if resolution:
                    self.case_id=resolution.linked_case_id
                    self._refund_return(db)
                return
        self._prepare()

    def _prepare(self):
        init=self.case["initial"]; now=datetime.now(timezone.utc)
        with self.factory() as db:
            seed_demo_data(db)
            order=Order(id=init["order_id"],user_id=self.actor,item_name=init["title"],amount=init["unit_amount"]*init["quantity"],
                delivered_at=now-timedelta(days=2),condition="sealed",quality_issue=True)
            db.add(order); db.flush()
            item=OrderItem(id=f"line-{init['order_id']}",order_id=order.id,sku=init["sku"],title=init["title"],quantity=init["quantity"],unit_amount=init["unit_amount"])
            db.add(item)
            db.add_all([InventoryStock(sku=init["sku"],available_quantity=init["available"],reserved_quantity=0),
                InventoryStock(sku=init["sku"]+"-ALT",available_quantity=init["quantity"]+1,reserved_quantity=0)])
            db.flush()
            db.add(ReplacementOption(id=str(uuid4()),original_sku=init["sku"],replacement_sku=init["sku"]+"-ALT"))
            db.add(PaymentTransaction(id=f"pay-{order.id}",order_id=order.id,user_id=self.actor,provider="demo_payment",provider_payment_id=f"pay-{order.id}",amount=order.amount,status="captured"))
            db.commit()
            refund=self.case["scenario_id"]=="refund_outcome_unknown"
            created=create_case(db,self.actor,AfterSalesCreateRequest(order_id=order.id,request_type="refund" if refund else "exchange",reason="质量问题",items=[{"order_item_id":item.id,"quantity":init["quantity"]}]),"create-episode")
            self.case_id,self.intent_id=created.id,None
            if refund:
                confirm_case(db,self.actor,created.id,"confirm-episode")
                schedule_pickup(db,self.actor,created.id,"明天上午","pickup-episode")
                self._events(db,created.id,["pickup.collected","return.received"])
                intent=ensure_refund_intent(db,db.get(AfterSalesCase,created.id)); db.commit()
                self.intent_id=intent.id
            self.execution_id=str(uuid4())
            db.add(RecoveryExecution(id=self.execution_id,actor_id=self.actor,case_id=self.case_id,command_key=self.key,
                state_json={"world":{"case_id":self.case_id,"intent_id":self.intent_id}})); db.commit()

    def _events(self,db,case_id,types):
        from ..models import FulfillmentEvent
        for typ in types:
            seq=int(db.scalar(select(func.max(FulfillmentEvent.sequence_no)).where(FulfillmentEvent.case_id==case_id)) or 0)+1
            receive_provider_event(db,"demo_fulfillment",{"event_id":f"recovery-{case_id}-{seq}","case_id":case_id,"event_type":typ,
                "sequence_no":seq,"occurred_at":datetime.now(timezone.utc),"payload":{"tracking_number":f"REC-{case_id}"}})

    def _deliver_exchange(self,db,case_id):
        case=db.get(AfterSalesCase,case_id)
        if case.status=="awaiting_pickup":
            schedule_pickup(db,self.actor,case_id,"明天上午",f"pickup-{case_id}")
        remaining={"pickup_scheduled":["pickup.collected","return.received","replacement.shipped","replacement.delivered"],
            "picked_up":["return.received","replacement.shipped","replacement.delivered"],
            "return_received":["replacement.shipped","replacement.delivered"],"replacement_shipped":["replacement.delivered"],"completed":[]}
        self._events(db,case_id,remaining[db.get(AfterSalesCase,case_id).status])
        return {"status":"completed","case_id":case_id}

    def _refund_return(self,db):
        """Replay only missing scripted return events following committed customer consent."""
        case=db.get(AfterSalesCase,self.case_id)
        if case.status=="awaiting_pickup":
            schedule_pickup(db,self.actor,self.case_id,"明天上午",f"pickup-{self.case_id}")
        remaining={"pickup_scheduled":["pickup.collected","return.received"],"picked_up":["return.received"]}
        self._events(db,self.case_id,remaining.get(db.get(AfterSalesCase,self.case_id).status,[]))
        intent=ensure_refund_intent(db,db.get(AfterSalesCase,self.case_id));db.commit();self.intent_id=intent.id

    def _stock(self,db):
        s=db.get(InventoryStock,self.case["initial"]["sku"])
        return {"available":s.available_quantity-s.reserved_quantity,"version":s.version}

    def primary(self,occurrence):
        variant=self.case["variant"]; fault=not self.case.get("no_fault")
        if fault and occurrence in self.case["fault_script"]["primary_failures"]:
            return {"error":"temporarily_unavailable","message":"库存读取返回503，未取得库存事实。"}
        with self.factory() as db:
            if self.intent_id:
                intent=db.get(RefundIntent,self.intent_id)
                if fault and occurrence==1 and variant=="write_before_commit":
                    return {"error":"result_unknown","message":"提交请求超时，执行结果尚未核验。"}
                if fault and occurrence==1 and variant=="write_remote_only":
                    self.channel.request_refund({"intent_id":intent.id,"amount":str(intent.amount)},intent.idempotency_key)
                    return {"error":"result_unknown","message":"提交请求超时，执行结果尚未核验。"}
                submitted=submit_refund_provider(db,intent.id,self.channel)
                if fault and occurrence==1:
                    return {"error":"result_unknown","message":"提交请求超时，执行结果尚未核验。"}
                return {"status":submitted.status,"provider_refund_id":submitted.provider_refund_id}
            if fault and self.case["scenario_id"]=="inventory_changed_during_confirmation" and occurrence==1:
                s=db.get(InventoryStock,self.case["initial"]["sku"]); s.available_quantity=s.reserved_quantity; s.version+=1; db.commit()
            if fault and variant=="read_verification" and occurrence==2:
                return {"error":"temporarily_unavailable","message":"库存查询已恢复，但确认前的库存核验503；尚未执行换货。","stage":"verification"}
            confirmed=confirm_case(db,self.actor,self.case_id,"confirm-episode")
            if confirmed.status=="fulfillment_exception":
                return {"error":"stock_changed","message":"确认时库存不足，原换货未分配库存。"}
            return self._deliver_exchange(db,self.case_id)

    def verify(self,occurrence):
        if not self.case.get("no_fault") and self.case["variant"]=="read_persistent":
            return {"error":"temporarily_unavailable","message":"库存服务持续503，核验未获得库存事实。"}
        if not self.case.get("no_fault") and occurrence in self.case["fault_script"]["verify_failures"]:
            return {"error":"temporarily_unavailable","message":"状态核验服务503，未获得新事实。"}
        with self.factory() as db:
            if self.intent_id:
                intent=db.get(RefundIntent,self.intent_id); remote=self.channel.get(intent.idempotency_key)
                if remote:
                    # Query-provider recovery projects a separately committed external fact.
                    if intent.status=="pending":
                        intent.status,intent.provider_refund_id="submitted",remote["provider_refund_id"]
                        db.get(AfterSalesCase,intent.case_id).status="refund_processing"; db.commit()
                    if intent.status=="succeeded" and remote["status"]=="succeeded":return {"status":"completed","case_id":intent.case_id}
                    return {"status":"accepted","provider_refund_id":remote["provider_refund_id"],"local_status":intent.status}
                return {"status":"not_found","authoritative":True,"message":"渠道按原幂等键核验未执行。"}
            if db.get(AfterSalesCase,self.case_id).status in {"awaiting_pickup","pickup_scheduled","picked_up","return_received","replacement_shipped","completed"}:
                return self._deliver_exchange(db,self.case_id)
            if self.case["variant"]=="read_verification":
                confirmed=confirm_case(db,self.actor,self.case_id,"confirm-episode")
                if confirmed.status=="awaiting_pickup": return self._deliver_exchange(db,self.case_id)
            return {"status":"verified",**self._stock(db)}

    def refresh(self,occurrence):
        with self.factory() as db:
            if self.case["variant"]=="stock_restored":
                s=db.get(InventoryStock,self.case["initial"]["sku"])
                s.available_quantity=max(s.available_quantity,self.case["initial"]["quantity"]+s.reserved_quantity);s.version+=1;db.commit()
            return resolution_options(db,self.actor,self.case_id)

    def offer(self):
        with self.factory() as db:
            options=resolution_options(db,self.actor,self.case_id)
            customer=self.case["customer"]
            choice=customer["choice"]
            if choice in {"no_reply",None}: return {"terminal":"awaiting_customer","message":"请选择售后方案，随后再次确认。"}
            if choice=="withdraw":
                cancel_case(db,self.actor,self.case_id,"withdraw-episode");return {"terminal":"cancelled","message":"客户撤回任务。"}
            if choice=="reject":
                row=propose_resolution(db,self.actor,self.case_id,"refund","resolution-episode")
                confirm_resolution(db,self.actor,row.id,1,False)
                return {"terminal":"awaiting_customer","message":"客户拒绝新方案，未执行。"}
            sku=None
            if choice=="exchange": sku=self.case["initial"]["sku"]+("-ALT" if customer["replacement"]=="alternative" else "")
            row=propose_resolution(db,self.actor,self.case_id,choice,"resolution-episode",sku)
            row=confirm_resolution(db,self.actor,row.id,1,True)
            if row.status=="waiting_stock": return {"terminal":"waiting_stock","message":"已登记等待补货；补货后重新核验并确认。"}
            if row.status=="human": return {"terminal":"human","message":"已创建人工处理工单。"}
            if row.linked_case_id:
                self.case_id=row.linked_case_id
                self._refund_return(db)
                submit_refund_provider(db,self.intent_id,self.channel)
                return self.poll()
            return self._deliver_exchange(db,self.case_id)

    def poll(self):
        if not self.intent_id: return {"status":"pending"}
        with self.factory() as db:
            intent=db.get(RefundIntent,self.intent_id);remote=self.channel.get(intent.idempotency_key)
            if not remote: return {"status":"not_found","authoritative":True}
            if intent.status=="succeeded" and remote["status"]=="succeeded":return {"status":"completed","case_id":intent.case_id}
            self.channel.settle(intent.idempotency_key)
            if not intent.provider_refund_id:
                intent.status,intent.provider_refund_id="submitted",remote["provider_refund_id"];db.commit()
            apply_refund_settlement(db,"demo_payment",remote["provider_refund_id"],True,f"settlement-{intent.id}",
                {"amount":remote["amount"],"currency":"CNY","occurred_at":datetime.now(timezone.utc).isoformat()})
            return {"status":"completed","case_id":self.case_id}

    def handoff(self):
        with self.factory() as db:
            _create_incident(db,self.case_id,"RECOVERY_HUMAN_REQUIRED","恢复未完成，等待人工处理。",f"recovery:{self.case_id}");db.commit()
        return {"terminal":"human"}

    def facts(self):
        with self.factory() as db:
            case=db.get(AfterSalesCase,self.case_id)
            line=db.get(OrderItem,f"line-{self.case['initial']['order_id']}")
            stocks=list(db.scalars(select(InventoryStock).where(InventoryStock.sku.like(self.case['initial']['sku']+'%'))))
            fulfillment=db.scalar(select(ExchangeFulfillment).where(ExchangeFulfillment.case_id==self.case_id))
            intent=db.get(RefundIntent,self.intent_id) if self.intent_id else None
            remote=self.channel.get(intent.idempotency_key) if intent else None
            resolutions=list(db.scalars(select(AfterSalesResolution)))
            return {"case_id":case.id,"case_status":case.status,"request_type":case.request_type,
                "quantity":line.quantity,"refunded_quantity":line.refunded_quantity,
                "refund_status":intent.status if intent else None,"refund_amount":str(intent.amount) if intent else None,
                "channel":remote,"exchange_status":fulfillment.status if fulfillment else None,
                "stocks":[{"sku":s.sku,"available":s.available_quantity,"reserved":s.reserved_quantity} for s in stocks],
                "resolutions":[{"option":r.option,"status":r.status,"linked_case_id":r.linked_case_id} for r in resolutions],
                "handoff_created":bool(db.scalar(select(FulfillmentIncident.id).where(FulfillmentIncident.incident_type=="RECOVERY_HUMAN_REQUIRED"))),
                "refund_attempts":int(db.scalar(select(func.count()).select_from(RefundAttempt).where(RefundAttempt.refund_intent_id==self.intent_id)) or 0)}

    def close(self):
        self.engine.dispose()
        if self.temporary: self.temporary.cleanup()
