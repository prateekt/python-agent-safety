"""Redis-backed budget store for distributed deployments."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from . import BudgetBackend, BudgetCharge, BudgetLimits, ChargeResult, MemoryBackend


class RedisBudgetBackend:
    """Atomic budget charging via Redis Lua scripts."""

    _CHARGE_LUA = """
local idem_key = KEYS[1]
local budget_key = KEYS[2]
local rate_key = KEYS[3]
local loop_key = KEYS[4]
local lease_key = KEYS[5]

local request_id = ARGV[1]
local signature = ARGV[2]
local call_n = tonumber(ARGV[3])
local risk = tonumber(ARGV[4])
local max_calls = tonumber(ARGV[5])
local rate_limit = tonumber(ARGV[6])
local rate_window = tonumber(ARGV[7])
local max_identical = tonumber(ARGV[8])
local loop_history = tonumber(ARGV[9])
local max_concurrent = tonumber(ARGV[10])
local max_risk = tonumber(ARGV[11])
local lease_ttl = tonumber(ARGV[12])
local now = tonumber(ARGV[13])
local lease_id = ARGV[14]

local cached = redis.call('GET', idem_key)
if cached then return cached end

local calls = tonumber(redis.call('HGET', budget_key, 'calls') or '0')
if max_calls >= 0 and calls + call_n > max_calls then
  return redis.error_reply('QUOTA_EXCEEDED')
end

if rate_limit >= 0 then
  redis.call('ZREMRANGEBYSCORE', rate_key, 0, now - rate_window)
  local cnt = redis.call('ZCARD', rate_key)
  if cnt + call_n > rate_limit then
    return redis.error_reply('RATE_EXCEEDED')
  end
  for i=1,call_n do redis.call('ZADD', rate_key, now, request_id .. ':' .. i) end
end

if max_identical >= 0 then
  redis.call('LPUSH', loop_key, signature)
  redis.call('LTRIM', loop_key, 0, loop_history - 1)
  local items = redis.call('LRANGE', loop_key, 0, -1)
  local count = 0
  for _, v in ipairs(items) do if v == signature then count = count + 1 end end
  if count > max_identical then
    return redis.error_reply('LOOP_DETECTED')
  end
end

if max_concurrent >= 0 then
  redis.call('ZREMRANGEBYSCORE', lease_key, 0, now)
  local active = redis.call('ZCARD', lease_key)
  if active >= max_concurrent then
    return redis.error_reply('CONCURRENCY_EXCEEDED')
  end
  redis.call('ZADD', lease_key, now + lease_ttl, lease_id)
end

local risk_used = tonumber(redis.call('HGET', budget_key, 'risk') or '0')
if max_risk >= 0 and risk > 0 and risk_used + risk > max_risk then
  return redis.error_reply('RISK_EXCEEDED')
end

calls = calls + call_n
redis.call('HSET', budget_key, 'calls', calls)
if risk > 0 then redis.call('HINCRBY', budget_key, 'risk', risk) end

local result = cjson.encode({['lease_id']=lease_id, ['calls_used']=calls, ['cached']=false})
redis.call('SETEX', idem_key, 86400, result)
return result
"""

    def __init__(self, client: Any, *, org_id: str = "") -> None:
        self._client = client
        self._org_id = org_id
        self._script = client.register_script(self._CHARGE_LUA)

    def _prefix(self, task_id: str, suffix: str, org_id: str = "") -> str:
        oid = org_id or self._org_id
        if oid:
            return f"{oid}:{task_id}:{suffix}"
        return f"{task_id}:{suffix}"

    def charge(self, req: BudgetCharge, limits: BudgetLimits) -> ChargeResult:
        from ...core.exceptions import (
            LoopDetected,
            QuotaExceeded,
            RateLimitExceeded,
            RiskBudgetExceeded,
        )

        org = req.org_id or self._org_id
        task = req.task_id
        keys = [
            self._prefix(task, f"idem:{req.request_id}", org),
            self._prefix(task, "budget", org),
            self._prefix(task, "rate", org),
            self._prefix(task, "loop", org),
            self._prefix(task, "leases", org),
        ]
        lease_id = uuid.uuid4().hex
        now = time.time()
        args = [
            req.request_id,
            req.signature,
            str(req.call_n),
            str(req.risk),
            str(limits.max_calls if limits.max_calls is not None else -1),
            str(limits.rate_per_second if limits.rate_per_second is not None else -1),
            str(limits.rate_window),
            str(limits.max_identical if limits.max_identical is not None else -1),
            str(limits.loop_history),
            str(limits.max_concurrent if limits.max_concurrent is not None else -1),
            str(limits.max_risk if limits.max_risk is not None else -1),
            str(limits.lease_ttl),
            str(now),
            lease_id,
        ]
        try:
            raw = self._script(keys=keys, args=args)
        except Exception as exc:
            msg = str(exc)
            if "QUOTA" in msg or "CONCURRENCY" in msg:
                raise QuotaExceeded("calls", limits.max_calls or 0, 0) from exc
            if "RATE" in msg:
                raise RateLimitExceeded(
                    limits.rate_per_second or 0, limits.rate_window, 1.0
                ) from exc
            if "LOOP" in msg:
                raise LoopDetected("tool", limits.max_identical or 0, limits.max_identical or 0) from exc
            if "RISK" in msg:
                raise RiskBudgetExceeded(limits.max_risk or 0, 0) from exc
            raise

        if isinstance(raw, bytes):
            raw = raw.decode()
        data = json.loads(raw)
        return ChargeResult(
            lease_id=data.get("lease_id") or None,
            calls_used=int(data.get("calls_used", 0)),
            cached=bool(data.get("cached", False)),
        )

    def release_concurrency(self, task_id: str, lease_id: str, *, org_id: str = "") -> None:
        key = self._prefix(task_id, "leases", org_id)
        self._client.zrem(key, lease_id)


def redis_backend(client: Optional[Any] = None, *, org_id: str = "") -> BudgetBackend:
    """Return Redis backend if *client* given, else in-memory fallback."""
    if client is None:
        return MemoryBackend()
    return RedisBudgetBackend(client, org_id=org_id)
