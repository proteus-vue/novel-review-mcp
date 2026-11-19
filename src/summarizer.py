"""摘要金字塔：跨章检查只喂摘要，不喂全文。产物带缓存，避免重复生成。"""
from __future__ import annotations

import asyncio
import json
import re

import cache
import layers
import telemetry
from config import efficiency_cfg, get_benchmark_dimensions, render_prompt
from llm import judge

_sem = asyncio.Semaphore(efficiency_cfg().get("parallel", {}).get("llm_concurrency", 4))


def _fallback_summary(chapter: dict) -> dict:
    text = chapter.get("text", "")
    n = efficiency_cfg().get("summary_pyramid", {}).get("chars", 200)
    return {
        "chapter_id": chapter.get("id", ""),
        "actors": [],
        "actions": [],
        "conflicts": [],
        "payoffs": [],
        "hook": text[-80:],
        "new_foreshadow": layers.extract_foreshadow(text),
        "closed_foreshadow": [],
        "hard_data": {"time": "", "numbers": re.findall(r"\d+(?:\.\d+)?", text)[:12]},
        "persona_state": "",
        "_degraded": True,
    }


async def summarize_one(chapter: dict) -> dict:
    cid = chapter.get("id", "")
    text = chapter.get("text", "")
    if not efficiency_cfg().get("summary_pyramid", {}).get("enabled", True):
        return _fallback_summary(chapter)
    key = cache.make_key(cid, text, "_summary", "summary")
    cached = cache.get(key)
    if cached is not None:
        return cached
    prompt = render_prompt("summary", text=text[:4000])
    async with _sem:
        try:
            obj = await judge(prompt)
        except Exception as exc:
            tel = telemetry.current()
            if tel is not None:
                tel.record_error("summary", str(exc))
            raise
    tel = telemetry.current()
    if tel is not None:
        tel.record_llm("summary", obj.get("_telemetry"))
    if obj.get("_stub") or obj.get("_parse_error") or obj.get("_backend") == "stub":
        result = _fallback_summary(chapter)
        result["_stub"] = True
    else:
        result = {"chapter_id": cid, **obj}
    cache.set(key, result)
    return result


async def summarize_many(chapters: list[dict]) -> list[dict]:
    return await asyncio.gather(*[summarize_one(c) for c in chapters])


def compact(summaries: list[dict]) -> str:
    """把摘要压成紧凑文本喂给跨章评审，绝不塞全文。"""
    lines = []
    for s in summaries:
        def join(items, sep, n):
            return sep.join(str(x) for x in (items or [])[:n])
        lines.append(
            "第{}章 | 动作:{} | 冲突:{} | 爽点:{} | 钩子:{} | 新伏笔:{} | 回收:{} | 数字:{} | 人设:{}".format(
                s.get("chapter_id", "?"),
                join(s.get("actions", []), "/", 4),
                join(s.get("conflicts", []), "/", 3),
                join(s.get("payoffs", []), "/", 3),
                s.get("hook", "")[:40],
                join(s.get("new_foreshadow", []), ",", 5),
                join(s.get("closed_foreshadow", []), ",", 5),
                join(s.get("hard_data", {}).get("numbers", []), ",", 6),
                str(s.get("persona_state", ""))[:30],
            )
        )
    return "\n".join(lines)
