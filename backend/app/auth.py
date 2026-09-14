"""Identity adapters for development headers and production JWTs."""

import hmac
import os
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
import jwt
from jwt import InvalidTokenError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import Session

from .database import get_db
from .models import Actor


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    auth_mode: str = "demo"
    auth_jwt_secret: str | None = None
    auth_jwt_issuer: str | None = None
    auth_jwt_audience: str | None = None


def current_demo_user(
    x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id", min_length=1, max_length=64),
    authorization: str | None = Header(default=None),
) -> str:
    """Return a verified subject ID.

    ``X-Demo-User-Id`` exists only in demo mode. A production process refuses
    to start serving authenticated operations in that mode, and JWT roles are
    intentionally ignored: authorization always comes from the server-side
    ``actors`` table.
    """
    settings = AuthSettings()
    if settings.app_env.lower() == "production" and settings.auth_mode != "jwt":
        raise HTTPException(status_code=503, detail={"code": "PRODUCTION_JWT_REQUIRED", "message": "生产环境必须启用 JWT 身份验证。"})
    if settings.auth_mode == "demo":
        if not x_demo_user_id:
            raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "缺少开发身份。"})
        return x_demo_user_id
    if settings.auth_mode != "jwt":
        raise HTTPException(status_code=503, detail={"code": "AUTH_MODE_INVALID", "message": "身份验证模式配置无效。"})
    if not settings.auth_jwt_secret or len(settings.auth_jwt_secret) < 32:
        raise HTTPException(status_code=503, detail={"code": "JWT_SECRET_NOT_CONFIGURED", "message": "JWT 密钥未配置或长度不足。"})
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "缺少 Bearer Token。"})
    decode_options: dict = {"algorithms": ["HS256"], "options": {"require": ["exp", "sub"]}}
    if settings.auth_jwt_issuer:
        decode_options["issuer"] = settings.auth_jwt_issuer
    if settings.auth_jwt_audience:
        decode_options["audience"] = settings.auth_jwt_audience
    try:
        payload = jwt.decode(authorization.removeprefix("Bearer "), settings.auth_jwt_secret, **decode_options)
    except InvalidTokenError as error:
        raise HTTPException(status_code=401, detail={"code": "JWT_INVALID", "message": "身份 Token 无效或已过期。"}) from error
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 64:
        raise HTTPException(status_code=401, detail={"code": "JWT_SUBJECT_INVALID", "message": "Token 缺少有效主体。"})
    return subject


@dataclass(frozen=True)
class ActorContext:
    id: str
    role: str


def current_actor(
    actor_id: str = Depends(current_demo_user), db: Session = Depends(get_db)
) -> ActorContext:
    """Map an authenticated identity to a server-owned role.

    The demo header identifies a caller only; it cannot elevate the caller by
    supplying a role header or JSON field. Production swaps this adapter for a
    gateway/JWT identity dependency while the domain API remains unchanged.
    """
    actor = db.get(Actor, actor_id)
    if actor is None or not actor.active:
        raise HTTPException(status_code=403, detail={"code": "ACTOR_NOT_AUTHORIZED", "message": "当前身份无权访问运营能力。"})
    return ActorContext(id=actor.id, role=actor.role)


def require_operator(actor: ActorContext = Depends(current_actor)) -> ActorContext:
    if actor.role not in {"operator", "ops_manager"}:
        raise HTTPException(status_code=403, detail={"code": "OPS_ROLE_REQUIRED", "message": "需要运营人员权限。"})
    return actor


def require_ops_manager(actor: ActorContext = Depends(current_actor)) -> ActorContext:
    if actor.role != "ops_manager":
        raise HTTPException(status_code=403, detail={"code": "OPS_MANAGER_ROLE_REQUIRED", "message": "需要运营主管权限。"})
    return actor


def request_id(x_request_id: str | None = Header(default=None, alias="X-Request-Id", max_length=64)) -> str | None:
    return x_request_id


def require_internal_callback(
    x_internal_service_key: str = Header(alias="X-Internal-Service-Key", min_length=1),
) -> None:
    expected = os.getenv("INTERNAL_CALLBACK_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail={"code": "INTERNAL_CALLBACK_NOT_CONFIGURED", "message": "内部回调凭据未配置。"})
    if not hmac.compare_digest(x_internal_service_key, expected):
        raise HTTPException(status_code=403, detail={"code": "INTERNAL_CALLBACK_DENIED", "message": "无权调用内部回调。"})


def require_legacy_fulfillment_simulator() -> None:
    """Keep the M1 completion stub out of normal M5 deployments.

    The signed fulfillment webhook is the only normal source of a completed
    case.  This switch exists solely to replay the original M1 local demo and
    must be deliberately enabled alongside the internal service credential.
    """
    if os.getenv("LEGACY_FULFILLMENT_SIMULATOR_ENABLED", "").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=410, detail={
            "code": "LEGACY_FULFILLMENT_SIMULATOR_DISABLED",
            "message": "M5 已要求通过已验签的履约 Webhook 推进完成状态。",
        })


def verify_fulfillment_webhook(raw_body: bytes, signature: str, timestamp: str) -> None:
    """Verify raw-body HMAC before a provider event reaches the domain layer."""
    secret = os.getenv("FULFILLMENT_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail={"code": "FULFILLMENT_WEBHOOK_NOT_CONFIGURED", "message": "履约回调凭据未配置。"})
    try:
        sent_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(status_code=401, detail={"code": "FULFILLMENT_WEBHOOK_TIMESTAMP_INVALID", "message": "履约回调时间戳无效。"}) from error
    if sent_at.tzinfo is None or abs((datetime.now(timezone.utc) - sent_at.astimezone(timezone.utc)).total_seconds()) > 300:
        raise HTTPException(status_code=401, detail={"code": "FULFILLMENT_WEBHOOK_TIMESTAMP_EXPIRED", "message": "履约回调时间戳已过期。"})
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail={"code": "FULFILLMENT_WEBHOOK_SIGNATURE_INVALID", "message": "履约回调签名无效。"})
