"""Shared Redis sliding-window limiter with a safe local development fallback."""
from __future__ import annotations
import os
import time
from collections import defaultdict, deque
from fastapi import HTTPException

class SlidingWindowLimiter:
    def __init__(self): self.buckets=defaultdict(deque)
    def check(self,key:str,limit:int,seconds:int=60):
        now=time.monotonic(); bucket=self.buckets[key]
        while bucket and bucket[0] <= now-seconds: bucket.popleft()
        if len(bucket)>=limit: raise HTTPException(429,detail={"code":"RATE_LIMITED","message":"请求过于频繁，请稍后重试。"},headers={"Retry-After":str(seconds)})
        bucket.append(now)

class RedisSlidingWindowLimiter:
    def __init__(self, url: str):
        import redis
        self.client=redis.Redis.from_url(url, socket_connect_timeout=0.25, socket_timeout=0.25, decode_responses=True)
    def check(self,key:str,limit:int,seconds:int=60):
        # Atomic sorted-set window. Fail closed in production so a Redis outage
        # cannot turn into an unlimited write/API path.
        now=time.time(); member=f"{now}:{os.urandom(4).hex()}"; name=f"verireturn:rate:{key}"
        try:
            pipe=self.client.pipeline(); pipe.zremrangebyscore(name, 0, now-seconds); pipe.zcard(name); _, count=pipe.execute()
            if count >= limit: raise HTTPException(429,detail={"code":"RATE_LIMITED","message":"请求过于频繁，请稍后重试。"},headers={"Retry-After":str(seconds)})
            pipe=self.client.pipeline(); pipe.zadd(name,{member:now}); pipe.expire(name,seconds+1); pipe.execute()
        except HTTPException: raise
        except Exception as error:
            if os.getenv("APP_ENV", "development").lower() == "production":
                raise HTTPException(503, detail={"code":"RATE_LIMITER_UNAVAILABLE","message":"请求保护服务暂不可用。"}) from error
            _local.check(key,limit,seconds)

_local=SlidingWindowLimiter()
try: limiter=RedisSlidingWindowLimiter(os.environ["REDIS_URL"]) if os.getenv("REDIS_URL") else _local
except Exception: limiter=_local
def route_limit(path:str)->int:
    if "/webhooks/" in path: return 120
    if path.startswith("/agent/"): return 30
    if path.startswith("/ops/"): return 60
    if path.startswith("/tools/") and path not in {"/tools/after-sales/eligibility"}: return 45
    return 120
