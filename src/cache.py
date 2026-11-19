"""增量缓存：hash 命中且规则版本/平台/阶段未变则复用，避免一卷通读。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from config import CACHE_ROOT, RULE_VERSION, efficiency_cfg

CACHE_DIR = Path(
    os.environ.get(
        "XIEZHI_CACHE_DIR",
        str(CACHE_ROOT / efficiency_cfg().get("cache", {}).get("dir", ".review_cache")),
    )
)
CACHE_FILE = CACHE_DIR / "results.json"
_MEMORY: dict[str, dict] | None = None

stats = {"hits": 0, "misses": 0, "stored": 0}


def _load() -> dict[str, dict]:
    global _MEMORY
    if _MEMORY is None:
        if CACHE_FILE.exists():
            try:
                _MEMORY = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                _MEMORY = {}
        else:
            _MEMORY = {}
    return _MEMORY


def make_key(chapter_id: str, text: str, platform: str, stage: str, extra: str = "") -> str:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    base = "|".join([RULE_VERSION, platform, stage, chapter_id, h, extra])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def get(key: str) -> dict | None:
    if not efficiency_cfg().get("cache", {}).get("enabled", True):
        stats["misses"] += 1
        return None
    val = _load().get(key)
    if val is None:
        stats["misses"] += 1
        return None
    stats["hits"] += 1
    return val


def set(key: str, value: dict) -> None:
    _load()[key] = value
    stats["stored"] += 1


def flush() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(_load(), ensure_ascii=False, indent=1), encoding="utf-8")


def clear() -> int:
    global _MEMORY
    n = len(_load())
    _MEMORY = {}
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
    return n


def reset_stats() -> None:
    stats.update({"hits": 0, "misses": 0, "stored": 0})
