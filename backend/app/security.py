"""Boundary normalisation and safe projections; raw business records remain auditable."""
from __future__ import annotations
import re
import unicodedata
from fastapi import HTTPException
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"(?<![\w.-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_ID = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

def normalize_customer_text(value: str, *, limit: int = 2000) -> str:
    value = unicodedata.normalize("NFC", value)
    value = _CONTROL.sub("", value).strip()
    if not value or len(value) > limit:
        raise HTTPException(422, detail={"code":"INPUT_INVALID","message":"输入不能为空且不能超过允许长度。"})
    return value

def redact_text(value: str) -> str:
    value = _PHONE.sub("[手机号已脱敏]", value)
    value = _EMAIL.sub("[邮箱已脱敏]", value)
    return _ID.sub("[证件号已脱敏]", value)

def validate_idempotency_key(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", value):
        raise HTTPException(422, detail={"code":"IDEMPOTENCY_KEY_INVALID","message":"幂等键只能使用字母、数字、点、下划线、连字符和冒号。"})
    return value
