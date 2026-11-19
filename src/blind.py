"""P2c 盲评回归：隔离输入包、judgements 校验/回注、原报告对照。

设计边界：
- 盲评证据来自外部独立评审员，本模块只做校验、回注与对照，不生成评语。
- 不自动把 0-10 换算成百分制（开源版评分口径已统一），只警告错配。
- 盲评运行前后清缓存并恢复网关，避免把盲评结果污染进普通评审。
"""
from __future__ import annotations

import json
import re
from typing import Any

import cache
import engine
import llm
from config import PROMPTS, RULE_VERSION

STAGES = ("first_review", "second_review", "third_review", "red_team", "benchmark")
STAGE_MARKERS = {
    "first_review": "你是平台一审编辑",
    "second_review": "你是二审编辑",
    "third_review": "你是三审",
    "red_team": "你是红队挑刺员",
    "benchmark": "你是头部作品分析师",
    "anti_drift": "你是连续性审校员",
    "summary": "把本章压成",
}
LEAK_KEYS = (
    "review", "reviews", "judgement", "judgments", "score", "scores", "verdict",
    "overall", "report", "strict_gate", "vetoes", "veto", "gate", "feedback",
)
_ALLOWED_CHAPTER_KEYS = {"id", "chapter_id", "text", "file", "title"}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _norm_verdict(value: Any) -> str:
    return str(value or "").split("（")[0].strip()


def build_blind_package(
    chapters: list[dict],
    platform: str = "qimao",
    include_prompts: bool = True,
    persona: str = "",
    facts: dict | None = None,
) -> dict:
    """生成只含正文与提示词的盲评输入包；不携带任何既有评审结论。"""
    leaks: list[dict] = []
    clean: list[dict] = []
    for chapter in chapters:
        cid = str(chapter.get("id", chapter.get("chapter_id", "")))
        bad = sorted((set(chapter) - _ALLOWED_CHAPTER_KEYS) & set(LEAK_KEYS))
        if bad:
            leaks.append({"chapter_id": cid, "leaked_keys": bad})
        item = {"id": cid, "text": chapter.get("text", "")}
        if chapter.get("file"):
            item["file"] = chapter["file"]
        if chapter.get("title"):
            item["title"] = chapter["title"]
        clean.append(item)

    package: dict[str, Any] = {
        "rule_version": RULE_VERSION,
        "platform": platform,
        "chapters": clean,
        "instructions": [
            "只依据本输入包的正文与提示词独立判定。",
            "禁止读取任何既有评审报告、分数、verdict、veto、人工结论或历史对照。",
            "输出严格 JSON；second_review.score 与 third_review.score 使用百分制 0-100。",
            "按章返回 first_review/second_review/third_review/red_team/benchmark，另给 summaries 与 anti_drift。",
        ],
        "isolation": {
            "safe": not leaks,
            "leaks": leaks,
            "excluded_fields": list(LEAK_KEYS),
            "includes_persona": bool(persona),
            "includes_facts": bool(facts),
        },
    }
    if persona:
        package["persona"] = persona
    if facts:
        package["facts"] = facts
    if include_prompts:
        package["prompts"] = PROMPTS
    return package


def validate_judgements(chapters: list[dict], judgements: dict, *, strict: bool = True) -> dict:
    """校验盲评 JSON 结构；不改写、不补分、不自动换算。"""
    errors: list[dict] = []
    warnings: list[dict] = []
    missing: list[str] = []
    ids = [str(c.get("id", c.get("chapter_id", ""))) for c in chapters]

    if not isinstance(judgements, dict):
        return {
            "valid": False,
            "errors": [{"code": "not_object", "detail": "judgements 必须是 JSON 对象"}],
            "warnings": [], "coverage": {}, "chapter_ids": ids,
        }

    for cid in ids:
        entry = judgements.get(cid)
        if not isinstance(entry, dict):
            missing.append(cid)
            continue
        for stage in STAGES:
            value = entry.get(stage)
            if value is None:
                missing.append(f"{cid}.{stage}")
            elif not isinstance(value, dict):
                errors.append({"code": "stage_not_object", "chapter_id": cid, "stage": stage})
        for stage in ("second_review", "third_review"):
            value = entry.get(stage, {})
            score = value.get("score") if isinstance(value, dict) else None
            if isinstance(score, (int, float)) and not isinstance(score, bool) and 0 < score <= 10:
                warnings.append({
                    "code": "score_scale_suspect",
                    "chapter_id": cid,
                    "stage": stage,
                    "score": score,
                    "detail": "开源版该字段为百分制 0-100；本工具不自动换算，请盲评方按百分制重出。",
                })
        first = entry.get("first_review", {})
        if isinstance(first, dict) and "pass" in first and not isinstance(first["pass"], bool):
            errors.append({"code": "first_review_pass_not_bool", "chapter_id": cid})
        red = entry.get("red_team", {})
        if isinstance(red, dict) and "blocking" in red and not isinstance(red["blocking"], bool):
            errors.append({"code": "red_team_blocking_not_bool", "chapter_id": cid})

    summaries = judgements.get("summaries")
    if not isinstance(summaries, dict):
        errors.append({"code": "summaries_not_object", "detail": "缺少 summaries 对象"})
        summary_missing = ids[:]
    else:
        summary_missing = [cid for cid in ids if cid not in summaries]

    anti = judgements.get("anti_drift")
    if not isinstance(anti, dict) or not isinstance(anti.get("conflicts"), list):
        errors.append({"code": "anti_drift_invalid", "detail": "anti_drift 必须是含 conflicts 数组的对象"})

    if missing:
        errors.append({"code": "missing_stage", "items": missing})
    if summary_missing:
        errors.append({"code": "missing_summary", "items": summary_missing})

    return {
        "valid": not errors and (not strict or not missing),
        "errors": errors,
        "warnings": warnings,
        "coverage": {
            "chapters": len(ids),
            "stage_entries": sum(
                1 for cid in ids if isinstance(judgements.get(cid), dict)
                for stage in STAGES if isinstance(judgements[cid].get(stage), dict)
            ),
            "expected_stage_entries": len(ids) * len(STAGES),
            "summaries": len(ids) - len(summary_missing),
        },
        "chapter_ids": ids,
    }


class BlindGateway:
    """把外部盲评 JSON 回注引擎；缺项如实报错，不静默套用别的章。"""

    backend = "blind-judgements"
    model = "external-isolated-reviewer"

    def __init__(self, judgements: dict, chapters: list[dict]) -> None:
        self.judgements = judgements
        self._tokens: list[tuple[str, str]] = []
        for chapter in chapters:
            cid = str(chapter.get("id", chapter.get("chapter_id", "")))
            token = _compact(chapter.get("text", ""))[:80]
            if cid and token:
                self._tokens.append((cid, token))
        self.audit: dict[str, Any] = {
            "calls": 0, "hits": 0, "misses": 0, "unmatched": 0, "ambiguous": 0,
            "stages": {},
        }

    def _stage(self, prompt: str) -> str | None:
        for stage, marker in STAGE_MARKERS.items():
            if marker in prompt:
                return stage
        match = re.search(r'"stage"\s*:\s*"([a-z_]+)"', prompt)
        if match and match.group(1) in STAGE_MARKERS:
            return match.group(1)
        return None

    def _chapter(self, prompt: str) -> str | None:
        compact = _compact(prompt)
        matched = [cid for cid, token in self._tokens if token in compact]
        if len(matched) > 1:
            self.audit["ambiguous"] += 1
            return None
        if matched:
            return matched[0]
        for pattern in (r"chapter_id[:：]\s*([0-9A-Za-z_\-]+)", r"第([0-9A-Za-z_\-]+)章"):
            match = re.search(pattern, prompt)
            if match:
                return match.group(1)
        return None

    async def chat(self, prompt: str) -> str:
        self.audit["calls"] += 1
        stage = self._stage(prompt)
        slot = self.audit["stages"].setdefault(stage or "unknown", {
            "calls": 0, "hits": 0, "misses": 0,
        })
        slot["calls"] += 1

        if stage == "anti_drift":
            cid = "-"
            obj = self.judgements.get("anti_drift")
        else:
            cid = self._chapter(prompt)
            if stage == "summary":
                obj = (self.judgements.get("summaries") or {}).get(cid) if cid else None
            else:
                obj = (self.judgements.get(cid) or {}).get(stage) if cid and stage else None

        if not cid and stage != "anti_drift":
            self.audit["unmatched"] += 1
            self.audit["misses"] += 1
            slot["misses"] += 1
            return json.dumps({
                "_blind_lookup_error": "chapter_unmatched",
                "stage": stage or "unknown",
            }, ensure_ascii=False)
        if not isinstance(obj, dict):
            self.audit["misses"] += 1
            slot["misses"] += 1
            return json.dumps({
                "_blind_missing": True, "stage": stage or "unknown", "chapter_id": cid,
            }, ensure_ascii=False)

        self.audit["hits"] += 1
        slot["hits"] += 1
        payload = dict(obj)
        payload.setdefault("stage", stage or "unknown")
        payload["_backend"] = self.backend
        payload["_model"] = self.model
        payload["_blind_chapter_id"] = cid
        return json.dumps(payload, ensure_ascii=False)


async def blind_review(
    chapters: list[dict],
    judgements: dict,
    platform: str = "qimao",
    facts: dict | None = None,
    persona: str = "",
    revised_ids: list[str] | None = None,
    clear_cache: bool = True,
) -> dict:
    """用外部盲评 judgements 跑完整引擎；返回报告 + 校验/命中审计。"""
    validation = validate_judgements(chapters, judgements, strict=True)
    if not validation["valid"]:
        return {"ok": False, "validation": validation, "note": "盲评 JSON 未通过校验，未运行引擎。"}

    gateway = BlindGateway(judgements, chapters)
    previous = getattr(llm, "_gateway", None)
    if clear_cache:
        cache.clear()
    try:
        llm.set_gateway(gateway)
        report = await engine.review_batch(chapters, platform, facts, persona, revised_ids)
    finally:
        llm._gateway = previous
        if clear_cache:
            cache.clear()

    report["ok"] = True
    report["blind"] = {
        "ok": True,
        "source": "external-isolated-judgements",
        "validation": validation,
        "audit": gateway.audit,
        "score_scale": "second_review/third_review 按百分制 0-100 原样回注；不自动换算",
        "cache_cleared": clear_cache,
    }
    return report


def blind_compare(original_report: dict, blind_report: dict) -> dict:
    """事后对照原评审与盲评：差异只呈现，不自动改写任何结论。"""

    def unwrap(report: Any) -> dict:
        if isinstance(report, dict) and isinstance(report.get("l3_report"), dict):
            return report["l3_report"]
        return report if isinstance(report, dict) else {}

    def overview(report: Any) -> dict:
        data = unwrap(report)
        rows = {}
        for cid, item in (data.get("chapters") or {}).items():
            if not isinstance(item, dict):
                continue
            total = item.get("overall")
            if isinstance(total, dict):
                rows[str(cid)] = {
                    "score": total.get("score"),
                    "verdict": _norm_verdict(total.get("verdict")),
                    "vetoes": sorted(str(v) for v in (total.get("vetoes") or [])),
                }
            else:
                rows[str(cid)] = {
                    "score": None,
                    "verdict": f"status:{item.get('status', 'unknown')}",
                    "vetoes": [],
                }
        return rows

    original = overview(original_report)
    blind = overview(blind_report)
    rows = []
    for cid in sorted(set(original) | set(blind), key=str):
        a = original.get(cid, {})
        b = blind.get(cid, {})
        score_a, score_b = a.get("score"), b.get("score")
        delta = None
        if isinstance(score_a, (int, float)) and isinstance(score_b, (int, float)):
            delta = round(float(score_b) - float(score_a), 2)
        veto_a = set(a.get("vetoes") or [])
        veto_b = set(b.get("vetoes") or [])
        verdict_flip = bool(a.get("verdict") and b.get("verdict") and a["verdict"] != b["verdict"])
        rows.append({
            "chapter_id": cid,
            "original_score": score_a,
            "blind_score": score_b,
            "score_delta": delta,
            "original_verdict": a.get("verdict"),
            "blind_verdict": b.get("verdict"),
            "verdict_flip": verdict_flip,
            "vetoes_added": sorted(veto_b - veto_a),
            "vetoes_removed": sorted(veto_a - veto_b),
        })

    deltas = [r["score_delta"] for r in rows if r["score_delta"] is not None]
    return {
        "original_batch_verdict": _norm_verdict(unwrap(original_report).get("summary", {}).get("verdict")),
        "blind_batch_verdict": _norm_verdict(unwrap(blind_report).get("summary", {}).get("verdict")),
        "rows": rows,
        "summary": {
            "chapter_count": len(rows),
            "avg_score_delta": round(sum(deltas) / len(deltas), 2) if deltas else None,
            "max_abs_score_delta": max((abs(d) for d in deltas), default=None),
            "verdict_flip_chapters": [r["chapter_id"] for r in rows if r["verdict_flip"]],
            "veto_added_chapters": [r["chapter_id"] for r in rows if r["vetoes_added"]],
            "veto_removed_chapters": [r["chapter_id"] for r in rows if r["vetoes_removed"]],
        },
        "note": "对照只呈现差异；原报告与盲评报告均保留，不自动改写判定。",
    }
