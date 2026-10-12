"""Atomic shared API-request budget; an uncertain request stays charged."""
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from ..models import RecoveryModelBudget

class BudgetExhausted(RuntimeError): pass

class ModelBudget:
    def __init__(self, session_factory, key="recovery-v2-implementation", limit=3000):
        if not 1 <= limit <= 3000: raise ValueError("budget must be within 1..3000")
        self.factory, self.key, self.limit = session_factory, key, limit
        with session_factory() as db:
            insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
            db.execute(insert(RecoveryModelBudget).values(id=key,max_calls=limit,used_calls=0).on_conflict_do_nothing(index_elements=["id"]))
            db.commit()
            row = db.get(RecoveryModelBudget,key)
            if row.max_calls != limit: raise ValueError("existing budget is immutable")

    def reserve(self):
        with self.factory() as db:
            result = db.execute(update(RecoveryModelBudget).where(RecoveryModelBudget.id==self.key,
                RecoveryModelBudget.used_calls < RecoveryModelBudget.max_calls).values(used_calls=RecoveryModelBudget.used_calls+1))
            db.commit()
            if result.rowcount != 1: raise BudgetExhausted("GLOBAL_MODEL_BUDGET_EXHAUSTED")

    def snapshot(self):
        with self.factory() as db:
            r=db.get(RecoveryModelBudget,self.key)
            return {"id":r.id,"used_calls":r.used_calls,"max_calls":r.max_calls}
