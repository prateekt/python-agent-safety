"""Observability helpers for distributed agent safety."""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional


@dataclass
class StructuredLog:
    """JSON structured log line for gateway / worker services."""

    level: str
    message: str
    task_id: Optional[str] = None
    request_id: Optional[str] = None
    org_id: Optional[str] = None
    capability: Optional[str] = None
    decision: Optional[str] = None
    latency_ms: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def emit(self) -> None:
        print(json.dumps({
            "ts": self.timestamp,
            "level": self.level,
            "msg": self.message,
            **{k: v for k, v in {
                "task_id": self.task_id,
                "request_id": self.request_id,
                "org_id": self.org_id,
                "capability": self.capability,
                "decision": self.decision,
                "latency_ms": self.latency_ms,
            }.items() if v is not None},
            **self.extra,
        }, sort_keys=True))


class PrometheusMetrics:
    """Lightweight in-process metrics (Prometheus text exposition format)."""

    def __init__(self) -> None:
        self._counters: Counter[str] = Counter()
        self._histograms: Dict[str, List[float]] = {}
        self._lock = Lock()

    def inc(self, name: str, labels: Optional[Dict[str, str]] = None, n: int = 1) -> None:
        key = _metric_key(name, labels)
        with self._lock:
            self._counters[key] += n

    def observe(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        key = _metric_key(name, labels)
        with self._lock:
            self._histograms.setdefault(key, []).append(value)

    def exposition(self) -> str:
        lines = []
        with self._lock:
            for key, val in sorted(self._counters.items()):
                lines.append(f"{key} {val}")
            for key, vals in sorted(self._histograms.items()):
                if vals:
                    lines.append(f"{key}_count {len(vals)}")
                    lines.append(f"{key}_sum {sum(vals)}")
        return "\n".join(lines) + "\n"


def _metric_key(name: str, labels: Optional[Dict[str, str]]) -> str:
    if not labels:
        return name
    parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{parts}}}"


# Global metrics registry for gateway process.
gateway_metrics = PrometheusMetrics()


class CircuitBreaker:
    """Fail-closed circuit breaker for store / gateway connectivity."""

    def __init__(self, *, failure_threshold: int = 3, open_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self._failures = 0
        self._opened_at: Optional[float] = None
        self._lock = Lock()

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()

    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if time.monotonic() - self._opened_at >= self.open_seconds:
                self._opened_at = None
                self._failures = 0
                return False
            return True
