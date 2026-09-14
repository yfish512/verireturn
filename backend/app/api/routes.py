from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..auth import current_demo_user, request_id, require_internal_callback, require_legacy_fulfillment_simulator
from ..database import get_db
from ..domain.service import (
    DomainError,
    cancel_case,
    check_eligibility,
    complete_case,
    confirm_case,
    create_case,
    get_logistics,
    get_owned_case,
    get_owned_order,
    list_audit_logs,
    mark_manual_review,
    schedule_pickup,
)
from ..schemas import (
    AfterSalesCaseResponse,
    AfterSalesCreateRequest,
    AuditLogResponse,
    EligibilityRequest,
    EligibilityResponse,
    ManualReviewRequest,
    PickupRequest,
)


router = APIRouter(prefix="/tools", tags=["business-tools"])


def domain_http_error(error: DomainError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message})


def idempotency_key(value: str = Header(alias="Idempotency-Key", min_length=8, max_length=128)) -> str:
    return value


@router.get("/orders/{order_id}")
def read_order(order_id: str, user_id: str = Depends(current_demo_user), db: Session = Depends(get_db)):
    try:
        order = get_owned_order(db, user_id, order_id)
        return {
            "id": order.id,
            "item_name": order.item_name,
            "amount": order.amount,
            "status": order.status,
            "delivered_at": order.delivered_at,
            "condition": order.condition,
            "quality_issue": order.quality_issue,
        }
    except DomainError as error:
        raise domain_http_error(error) from error


@router.get("/orders/{order_id}/logistics")
def read_logistics(order_id: str, user_id: str = Depends(current_demo_user), db: Session = Depends(get_db)):
    try:
        result = get_logistics(db, user_id, order_id)
        return {"order_id": result.order_id, "status": result.status, "tracking_number": result.tracking_number}
    except DomainError as error:
        raise domain_http_error(error) from error


@router.post("/after-sales/eligibility", response_model=EligibilityResponse)
def read_eligibility(request: EligibilityRequest, user_id: str = Depends(current_demo_user), db: Session = Depends(get_db)):
    try:
        return EligibilityResponse(**check_eligibility(db, user_id, request).__dict__)
    except DomainError as error:
        raise domain_http_error(error) from error


@router.post("/after-sales/cases", response_model=AfterSalesCaseResponse, status_code=201)
def create_after_sales_case(
    request: AfterSalesCreateRequest,
    user_id: str = Depends(current_demo_user),
    key: str = Depends(idempotency_key),
    trace_id: str | None = Depends(request_id),
    db: Session = Depends(get_db),
):
    try:
        return create_case(db, user_id, request, key, trace_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@router.get("/after-sales/cases/{case_id}", response_model=AfterSalesCaseResponse)
def read_case(case_id: int, user_id: str = Depends(current_demo_user), db: Session = Depends(get_db)):
    try:
        return get_owned_case(db, user_id, case_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@router.get("/after-sales/cases/{case_id}/audit-logs", response_model=list[AuditLogResponse])
def read_case_audit_logs(case_id: int, user_id: str = Depends(current_demo_user), db: Session = Depends(get_db)):
    try:
        return list_audit_logs(db, user_id, case_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@router.post("/after-sales/cases/{case_id}/confirm", response_model=AfterSalesCaseResponse)
def confirm_after_sales_case(
    case_id: int, user_id: str = Depends(current_demo_user), key: str = Depends(idempotency_key),
    trace_id: str | None = Depends(request_id), db: Session = Depends(get_db),
):
    try:
        return confirm_case(db, user_id, case_id, key, trace_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@router.post("/after-sales/cases/{case_id}/cancel", response_model=AfterSalesCaseResponse)
def cancel_after_sales_case(
    case_id: int, user_id: str = Depends(current_demo_user), key: str = Depends(idempotency_key),
    trace_id: str | None = Depends(request_id), db: Session = Depends(get_db),
):
    try:
        return cancel_case(db, user_id, case_id, key, trace_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@router.post("/after-sales/cases/{case_id}/pickup", response_model=AfterSalesCaseResponse)
def create_pickup(
    case_id: int, request: PickupRequest, user_id: str = Depends(current_demo_user), key: str = Depends(idempotency_key),
    trace_id: str | None = Depends(request_id), db: Session = Depends(get_db),
):
    try:
        return schedule_pickup(db, user_id, case_id, request.time_slot, key, trace_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@router.post("/internal/after-sales/cases/{case_id}/complete", response_model=AfterSalesCaseResponse)
def complete_after_sales_case(
    case_id: int, _: None = Depends(require_internal_callback), __: None = Depends(require_legacy_fulfillment_simulator), key: str = Depends(idempotency_key),
    trace_id: str | None = Depends(request_id), db: Session = Depends(get_db),
):
    try:
        return complete_case(db, case_id, key, trace_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@router.post("/internal/after-sales/cases/{case_id}/manual-review", response_model=AfterSalesCaseResponse)
def create_manual_review(
    case_id: int, request: ManualReviewRequest, _: None = Depends(require_internal_callback), key: str = Depends(idempotency_key),
    trace_id: str | None = Depends(request_id), db: Session = Depends(get_db),
):
    try:
        return mark_manual_review(db, case_id, request.reason, key, trace_id)
    except DomainError as error:
        raise domain_http_error(error) from error
