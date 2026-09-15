from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..auth import current_demo_user
from ..database import get_db
from ..domain.privacy import create_privacy_request
privacy_router=APIRouter(prefix="/privacy",tags=["privacy"])
@privacy_router.post("/export")
def export_data(user_id:str=Depends(current_demo_user),db:Session=Depends(get_db)):
    row=create_privacy_request(db,user_id,"export"); return {"request_id":row.id,"status":row.status,"data":row.result_json}
@privacy_router.post("/delete")
def delete_data(user_id:str=Depends(current_demo_user),db:Session=Depends(get_db)):
    row=create_privacy_request(db,user_id,"delete"); return {"request_id":row.id,"status":row.status,"result":row.result_json}
