"""L1 形态/结构红线 + L2 关键词打分。毫秒级，前置短路。"""
from __future__ import annotations

import re

from config import RULES

_L1 = []
for item in RULES.get("hard_reject", []):
    _L1.append({
        "id": item["id"],
        "re": re.compile(item["pattern"]),
        "action": item.get("action", "flag"),
        "level": item.get("level", "minor"),
        "hint": item.get("hint", ""),
    })

_L2 = RULES.get("classifier", [])


def l1_check(text: str) -> list[dict]:
    hits = []
    for r in _L1:
        for m in r["re"].finditer(text):
            hits.append({
                "type": r["id"],
                "pos": m.start(),
                "evidence": m.group(0)[:120],
                "level": r["level"],
                "action": r["action"],
                "suggestion": r["hint"],
            })
    return hits


def _score_keywords(text: str, words: list[str]) -> tuple[float, list[str]]:
    hit = [w for w in words if w and w in text]
    return min(1.0, len(hit) / max(1, len(words)) * 1.6), hit


def has_hard_reject(hits: list[dict]) -> bool:
    return any(h.get("level") == "fatal" and h.get("action") == "reject" for h in hits)


def l2_check(text: str) -> list[dict]:
    hits = []
    for rule in _L2:
        words = rule.get("keywords", [])
        score, matched = _score_keywords(text, words)
        extra: dict = {}
        if rule["id"] == "ai_flavor":
            reds = rule.get("red_words", [])
            rs, rhit = _score_keywords(text, reds)
            counts = {w: text.count(w) for w in reds if w in text}
            over = {w: c for w, c in counts.items() if c > rule.get("red_word_limit_per_chapter", 2)}
            pats = []
            for p in rule.get("patterns", []):
                n = len(re.findall(p["pattern"], text))
                if n > p["limit"]:
                    pats.append({"type": p["id"], "count": n, "limit": p["limit"]})
            score = max(score, rs)
            extra = {"red_word_counts": counts, "red_word_over": over, "pattern_over": pats}
            matched = rhit
        if score >= rule.get("threshold", 0.6) or extra.get("red_word_over") or extra.get("pattern_over"):
            hits.append({
                "type": rule["id"],
                "score": round(score, 3),
                "level": rule.get("level", "minor"),
                "matched": matched[:10],
                **extra,
            })
    return hits


def fact_check(chapters: list[dict], facts: dict | None) -> list[dict]:
    """跨章硬数据契约：must_not 命中 / must 缺失（must 按全书联合文本判定）。"""
    if not facts:
        return []
    rules = facts.get("rules", []) if isinstance(facts, dict) else []
    corpus = {c.get("id", "?"): c.get("text", "") for c in chapters}
    all_text = "\n".join(corpus.values())
    conflicts = []
    for r in rules:
        name = r.get("name", "未命名契约")
        for pat in r.get("must_not", []) or []:
            try:
                rx = re.compile(pat)
            except re.error as exc:
                conflicts.append({"field": name, "evidence": f"正则无效: {pat}", "suggestion": str(exc)})
                continue
            for cid, text in corpus.items():
                for m in rx.finditer(text):
                    conflicts.append({
                        "chapter_id": cid,
                        "field": name,
                        "evidence": m.group(0)[:80],
                        "expected": f"不得出现 /{pat}/",
                        "suggestion": r.get("note", "对照事实契约修正"),
                    })
        for pat in r.get("must", []) or []:
            try:
                if not re.search(pat, all_text):
                    conflicts.append({
                        "chapter_id": "*",
                        "field": name,
                        "evidence": "全书未出现",
                        "expected": f"应出现 /{pat}/",
                        "suggestion": r.get("note", "对照事实契约补齐"),
                    })
            except re.error:
                pass
    return conflicts
