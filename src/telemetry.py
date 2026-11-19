"""P2a 观测层：真实 LLM 调用、耗时、token 账本。

只采集事实，不参与评分/闸门；并发安全由 contextvars 保证。
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _empty_stage() -> dict[str, Any]:
    return {
        "calls": 0,
        "errors": 0,
        "llm_sec": 0.0,
        "queue_sec": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


@dataclass
class Telemetry:
    llm_calls: int = 0
    llm_errors: int = 0
    llm_sec: float = 0.0
    queue_sec: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    usage_available: bool = False
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)

    def _stage(self, stage: str) -> dict[str, Any]:
        return self.stages.setdefault(stage or "unknown", _empty_stage())

    def record_llm(self, stage: str, meta: dict | None = None) -> None:
        meta = meta or {}
        usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
        prompt_tokens = _as_int(usage.get("prompt_tokens"))
        completion_tokens = _as_int(usage.get("completion_tokens"))
        total_tokens = _as_int(usage.get("total_tokens")) or (prompt_tokens + completion_tokens)
        latency = float(meta.get("latency_sec") or 0.0)
        queue = float(meta.get("queue_sec") or 0.0)

        self.llm_calls += 1
        self.llm_sec += latency
        self.queue_sec += queue
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += total_tokens
        if usage or meta.get("usage_available"):
            self.usage_available = True

        slot = self._stage(stage)
        slot["calls"] += 1
        slot["llm_sec"] += latency
        slot["queue_sec"] += queue
        slot["prompt_tokens"] += prompt_tokens
        slot["completion_tokens"] += completion_tokens
        slot["total_tokens"] += total_tokens

    def record_error(self, stage: str, error: str = "") -> None:
        self.llm_errors += 1
        slot = self._stage(stage)
        slot["errors"] += 1
        slot["last_error"] = str(error)[:300]

    def merge(self, other: "Telemetry") -> None:
        self.llm_calls += other.llm_calls
        self.llm_errors += other.llm_errors
        self.llm_sec += other.llm_sec
        self.queue_sec += other.queue_sec
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.usage_available = self.usage_available or other.usage_available
        for stage, data in other.stages.items():
            slot = self._stage(stage)
            for key, value in data.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    slot[key] = slot.get(key, 0) + value
                else:
                    slot[key] = value

    def snapshot(self) -> dict[str, Any]:
        stages = {}
        for name, data in self.stages.items():
            stages[name] = {
                key: (round(value, 4) if isinstance(value, float) else value)
                for key, value in data.items()
            }
        return {
            "llm_calls": self.llm_calls,
            "llm_errors": self.llm_errors,
            "llm_cumulative_sec": round(self.llm_sec, 4),
            "llm_queue_sec": round(self.queue_sec, 4),
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "total": self.total_tokens,
                "available": self.usage_available,
            },
            "stages": stages,
        }


_current: ContextVar[Telemetry | None] = ContextVar("xiezhi_telemetry", default=None)


def current() -> Telemetry | None:
    return _current.get()


def begin() -> tuple[Telemetry, Any, Telemetry | None]:
    """开启子账本；子章/子批次结束时自动并入父账本。"""
    parent = _current.get()
    child = Telemetry()
    token = _current.set(child)
    return child, token, parent


def end(child: Telemetry, token: Any, parent: Telemetry | None) -> dict[str, Any]:
    _current.reset(token)
    if parent is not None:
        parent.merge(child)
    return child.snapshot()


def elapsed_since(start: float) -> float:
    return round(time.perf_counter() - start, 4)
