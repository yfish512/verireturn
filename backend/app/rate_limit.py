"""Small process-local limiter for the demo adapter; production deploys it behind a shared gateway."""
from __future__ import annotations
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
limiter=SlidingWindowLimiter()
def route_limit(path:str)->int:
    if "/webhooks/" in path: return 120
    if path.startswith("/agent/"): return 30
    if path.startswith("/ops/"): return 60
    if path.startswith("/tools/") and path not in {"/tools/after-sales/eligibility"}: return 45
    return 120
