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


def _cn_to_int(s: str) -> int | None:
    """中文数字/阿拉伯数字 -> int，失败返回 None。"""
    s = str(s).strip()
    if s.isdigit():
        return int(s)
    digit = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
    if s in digit:
        return digit[s]
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + digit.get(s[1:], 0)
    if "十" in s:
        a, _, b = s.partition("十")
        return digit.get(a, 0) * 10 + (digit.get(b, 0) if b else 0)
    if "百" in s:
        a, _, b = s.partition("百")
        return digit.get(a, 0) * 100 + (_cn_to_int(b) or 0)
    return None


def _extract_ordinal(raw: str) -> int | None:
    """从时间锚点原文中取序数：第X天/章、X月X日、周X、X:Y。"""
    m = re.search(r"第([0-9一二三四五六七八九十百]+)", raw)
    if m:
        return _cn_to_int(m.group(1))
    m = re.search(r"[月]([0-9一二三四五六七八九十]{1,3})[日号]", raw)
    if m:
        return _cn_to_int(m.group(1))
    m = re.search(r"周([一二三四五六日天])", raw)
    if m:
        return {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}.get(m.group(1))
    m = re.search(r"([0-9]{1,2})[:：]", raw)
    return int(m.group(1)) if m else None


def pacing_signals(text: str) -> dict:
    """节奏机器层信号：爽点/冲突/钩子密度 + 章末钩子 + 场次切分。零 LLM、可解释。"""
    cfg = RULES.get("pacing_signals", {})
    if not cfg.get("enabled", True):
        return {"enabled": False}
    n = max(1, len(text))
    k = 1000 / n
    payoff = {w: text.count(w) for w in cfg.get("payoff_words", []) if text.count(w)}
    conflict = {w: text.count(w) for w in cfg.get("conflict_words", []) if text.count(w)}
    hook = {w: text.count(w) for w in cfg.get("hook_words", []) if text.count(w)}
    payoff_n, conflict_n, hook_n = sum(payoff.values()), sum(conflict.values()), sum(hook.values())
    # 章末 200 字钩子：疑问句/感叹句或悬念词收尾
    tail = text[-200:]
    end_hook = bool(re.search(cfg.get("hook_sentence_end", r"[？?！!]"), tail)) or any(
        w in tail for w in cfg.get("hook_words", [])
    )
    scenes = len(re.split(r"\n\s*\n|(?:\*\*\*|---|===)", text))
    return {
        "enabled": True,
        "chars": n,
        "payoff": {"count": payoff_n, "per_2000": round(payoff_n * 2000 / n, 2), "hits": payoff},
        "conflict": {"count": conflict_n, "per_1000": round(conflict_n * k, 2), "hits": conflict},
        "hook": {"count": hook_n, "per_1000": round(hook_n * k, 2), "hits": hook,
                 "chapter_end": end_hook},
        "scenes": scenes,
        "warnings": _pacing_warnings(cfg, payoff_n * 2000 / n, conflict_n * k, hook_n * k, end_hook),
    }


def _pacing_warnings(cfg: dict, pp2k: float, cp1k: float, hp1k: float, end_hook: bool) -> list[str]:
    w = []
    if pp2k < cfg.get("payoff_per_2000_warn", 0.5):
        w.append(f"爽点密度偏低({pp2k}/2千字 < {cfg.get('payoff_per_2000_warn', 0.5)})")
    if cp1k < cfg.get("conflict_per_1000_warn", 1.5):
        w.append(f"冲突密度偏低({cp1k}/千字 < {cfg.get('conflict_per_1000_warn', 1.5)})")
    if hp1k < cfg.get("hook_per_1000_warn", 0.8):
        w.append(f"悬念密度偏低({hp1k}/千字 < {cfg.get('hook_per_1000_warn', 0.8)})")
    if not end_hook:
        w.append("章末无钩子")
    return w


def self_consistency_check(text: str) -> list[dict]:
    """单章内部自洽弱信号：时序倒退 / 数字互斥 / 称谓变体。

    只报 suspect + 原文证据，level 恒为 suspect，不构成 reject。
    独立评审实测的四类问题（时序倒错、数字自转不拢）是本层的检测原型。
    """
    cfg = RULES.get("self_consistency", {})
    if not cfg.get("enabled", True):
        return []
    out: list[dict] = []

    # 1) 时序锚点单调性
    tc = cfg.get("time_anchor", {})
    words = tc.get("unordered_words", [])
    anchors = []
    for pat in tc.get("time_anchor", {}).get("patterns", tc.get("patterns", [])):
        for m in re.finditer(pat, text):
            anchors.append({"pos": m.start(), "raw": m.group(0), "val": _extract_ordinal(m.group(0))})
    anchors = [a for a in anchors if a["val"] is not None]
    anchors.sort(key=lambda x: x["pos"])
    backward = 0
    for i in range(1, len(anchors)):
        prev, cur = anchors[i - 1], anchors[i]
        # 显式倒叙词在两者之间 → 不报
        between = text[prev["pos"]:cur["pos"]]
        if any(w in between for w in words):
            continue
        if cur["val"] < prev["val"]:
            backward += 1
            out.append({
                "type": "time_backward", "level": "suspect",
                "evidence": f"{prev['raw']}({prev['val']}) → {cur['raw']}({cur['val']})",
                "pos": cur["pos"],
                "suggestion": "核对时序；若为倒叙请加" + "/".join(words[:3]) + "等过渡词",
            })
    if len(out) > tc.get("max_backward_jumps", 1) + 2:
        out.append({"type": "time_many_jumps", "level": "suspect",
                    "evidence": f"共 {backward} 处时序倒退", "pos": 0,
                    "suggestion": "全章时间线重排后再核"})

    # 2) 数字互斥：邻近窗口内同一量词出现不同值 + 转折/否定词
    nc = cfg.get("numeric_conflict", {})
    win = nc.get("window_chars", 60)
    nums = [(m.start(), m.group(1), m.group(2)) for m in
            re.finditer(nc.get("unit_pattern", r"([0-9一二三四五六七八九十]+)\s*(块|元|单)"), text)]
    neg = nc.get("negation_words", [])
    contra = nc.get("contradiction_words", [])
    for i, (p1, v1, u1) in enumerate(nums):
        a1 = _cn_to_int(v1)
        if a1 is None:
            continue
        for p2, v2, u2 in nums[i + 1:]:
            if p2 - p1 > win or u1 != u2:
                break
            a2 = _cn_to_int(v2)
            if a2 is None or a1 == a2:
                continue
            seg = text[p1:min(len(text), p2 + len(v2) + len(u2) + 8)]
            markers = [w for w in contra + neg if w in seg]
            if markers:
                out.append({
                    "type": "numeric_contradiction", "level": "suspect",
                    "evidence": f"{v1}{u1} … {v2}{u2}（{'/'.join(markers)[:20]}）",
                    "pos": p1,
                    "suggestion": "两个数值对同一量的判断互斥，核对口径是否不同（如成本/售价）",
                })
            break

    # 3) 称谓变体（弱信号）
    ec = cfg.get("entity_alias", {})
    hits = {}
    for pat in ec.get("patterns", []):
        for m in re.finditer(pat, text):
            hits.setdefault(m.group(0), []).append(m.start())
    variants = [k for k, v in hits.items() if len(v) >= ec.get("min_occurrences", 2)]
    if variants:
        out.append({
            "type": "entity_alias", "level": "suspect",
            "evidence": "、".join(f"{k}({len(hits[k])}次)" for k in variants[:6]),
            "pos": min(min(v) for v in hits.values()),
            "suggestion": "确认这些称谓是否指同一人；若是，首次出现时交代关系",
        })
    # 去重：同 type + evidence 只留一条
    seen, uniq = set(), []
    for o in out:
        key = (o["type"], o["evidence"])
        if key not in seen:
            seen.add(key)
            uniq.append(o)
    return uniq


def extract_foreshadow(text: str) -> list[str]:
    """通用伏笔抽取：不依赖 F# 编号，识别未解答问句/悬念提示词/未完成事件。"""
    cfg = RULES.get("foreshadow_extract", {})
    if not cfg.get("enabled", True):
        return re.findall(r"F\d{1,3}", text)[:8]
    found: list[str] = []
    # 项目编号若存在仍识别（兼容旧台账）
    found.extend(re.findall(r"F\d{1,3}", text))
    # 悬念句：按句读切分，含悬念词或未完成模式
    sents = re.split(r"(?<=[。！？!?；;])", text)
    for s in sents:
        s = s.strip()
        if not s or len(s) > 120:
            continue
        if any(w in s for w in cfg.get("suspense_words", [])) or \
           any(re.search(p, s) for p in cfg.get("unfinished_patterns", [])) or \
           (s.endswith(("？", "?")) and len(s) > 6):
            found.append(s[:60])
        if len(found) >= cfg.get("max_items", 8):
            break
    # 去重保序
    seen, out = set(), []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
        if len(out) >= cfg.get("max_items", 8):
            break
    return out


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
