"""P1 闭环：把评审从「事后体检」变成「写稿前护栏」。

三件零 LLM、确定性的能力：
  1) extract_facts     —— 从正文抽硬数据，生成与 layers.fact_check 兼容的候选契约
  2) foreshadow_registry —— 伏笔台账：登记/回收/列表/超期，落盘到缓存目录
  3) next_chapter_brief  —— 评审结论 + 未回收伏笔 + 人设锚 → 下一章写作约束块

诚实边界：
  - extract_facts 只产出「候选契约」（confidence + evidence），不冒充人工确认的设定；
    生成的 must_not 正则覆盖有限，必须经人/LLM 复核后作为正式契约使用。
  - next_chapter_brief 是结构化转写，不新增判断；原始评审是桩模式时明确标注。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from cache import CACHE_DIR
from config import RULES
from layers import extract_foreshadow, self_consistency_check

REGISTRY_FILE = CACHE_DIR / "foreshadow_registry.json"

_DEFAULT_EXTRACT = {
    "enabled": True,
    "unit_pattern": r"([0-9一二三四五六七八九十百千万两]+\.?[0-9]?)\s*(元|块|万|单|个|人|岁|天|月|年|公里|米|斤|%)",
    "label_chars": 8,
    "min_occurrences": 2,
    "conflict_window_chars": 0,
    "max_anchors": 40,
}
_DEFAULT_BRIEF = {
    "enabled": True,
    "max_avoid": 8,
    "max_foreshadow": 8,
    "overdue_chapters": 8,
}


def _extract_cfg() -> dict:
    return {**_DEFAULT_EXTRACT, **(RULES.get("fact_extract") or {})}


def _brief_cfg() -> dict:
    return {**_DEFAULT_BRIEF, **(RULES.get("chapter_brief") or {})}


# ---------------------------------------------------------------------------
# 1) 硬数据抽取 → 候选契约
# ---------------------------------------------------------------------------
def _cn_to_int(s: str) -> float | None:
    """中文/阿拉伯数字转数值，失败返回 None。复用简单口径，避免引入依赖。"""
    s = str(s).strip()
    try:
        return float(s)
    except ValueError:
        pass
    digit = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if s in digit:
        return float(digit[s])
    if s == "十":
        return 10.0
    # 先拆分大单位再拆小单位："三百二十"含"十"，若先判十会把"三百"当首字得 0
    if "万" in s:
        a, _, b = s.partition("万")
        head = _cn_to_int(a)
        return None if head is None else head * 10000 + (_cn_to_int(b) or 0)
    if "千" in s:
        a, _, b = s.partition("千")
        head = _cn_to_int(a)
        return None if head is None else head * 1000 + (_cn_to_int(b) or 0)
    if "百" in s:
        a, _, b = s.partition("百")
        head = _cn_to_int(a)
        return None if head is None else head * 100 + (_cn_to_int(b) or 0)
    if s.startswith("十"):
        return 10.0 + (_cn_to_int(s[1:]) or 0)
    if "十" in s:
        a, _, b = s.partition("十")
        return float((_cn_to_int(a) or 0) * 10 + (_cn_to_int(b) or 0))
    return None


def _clean_label(raw: str) -> str:
    """把数字/标点从上下文标签里去掉，留下可读的量名线索。"""
    s = re.sub(r"[0-9一二三四五六七八九十百千万两\.\s，。！？：；、（）()《》\[\]]", "", raw)
    return s[-12:]


def extract_facts(chapters: list[dict]) -> dict:
    """从正文确定性抽取硬数据锚点，生成候选契约（fact_check 兼容）。

    返回：anchors / contract / conflicts / alias_suspects / note。
    零 LLM；所有结论都带 evidence，不把候选冒充确认事实。
    """
    cfg = _extract_cfg()
    if not cfg.get("enabled", True):
        return {"enabled": False, "anchors": [], "contract": {"rules": []},
                "conflicts": [], "note": "fact_extract.enabled=false"}

    pat = re.compile(cfg["unit_pattern"])
    label_chars = int(cfg.get("label_chars", 8))
    anchors: list[dict] = []
    for ch in chapters:
        cid = str(ch.get("id", ""))
        text = ch.get("text", "")
        for m in pat.finditer(text):
            raw_num, unit = m.group(1), m.group(2)
            val = _cn_to_int(raw_num)
            if val is None:
                continue
            # 过滤泛指时间："一天/一年/一月"是状语不是硬数据；"第三天/2003年"才保留
            if val == 1 and unit in ("天", "年", "月", "日") and raw_num in ("一", "1"):
                continue
            left = text[max(0, m.start() - label_chars):m.start()]
            anchors.append({
                "chapter_id": cid,
                "value": val,
                "raw": m.group(0),
                "unit": unit,
                "label": _clean_label(left),
                "evidence": text[max(0, m.start() - 24):m.end() + 16].strip(),
                "pos": m.start(),
            })

    # 按单位聚类：同单位不同值 = 漂移候选（"320单" vs "290单"必须能抓到）。
    # 单位是弱线索（同单位未必同量），故一律标 suspect/候选，交 L3 复核，绝不冒充确认冲突。
    grouped: dict[str, list[dict]] = {}
    for a in anchors:
        grouped.setdefault(a["unit"], []).append(a)
    conflicts = []
    rules = []
    for unit, items in grouped.items():
        label = items[0]["label"]
        values = sorted({a["value"] for a in items})
        canonical = min(items, key=lambda a: (a["pos"], a["chapter_id"]))
        if len(values) > 1:
            conflicts.append({
                "level": "suspect", "confidence": "suspect",
                "label": label, "unit": unit, "values": values,
                "evidence": [{"chapter_id": a["chapter_id"], "raw": a["raw"],
                              "evidence": a["evidence"]} for a in items[:6]],
                "suggestion": f"同单位「{unit}」出现多个值（{label or '量名未识别'}），"
                              f"可能是漂移也可能同单位不同量，交 L3 复核后统一口径",
            })
        # 候选契约：规范值必须出现（弱），同单位其它值不得出现（强）。标注置信度。
        occ = len(items)
        confidence = "high" if occ >= int(cfg.get("min_occurrences", 1)) and len(values) == 1 else "low"
        # 规范值的中文/阿拉伯写法都要排除，否则"三百二十单"会被当漂移。
        canon_num = re.match(r"[0-9一二三四五六七八九十百千万两]+\.?[0-9]?", canonical["raw"])
        canon_forms = {str(int(canonical["value"])) if canonical["value"].is_integer()
                       else str(canonical["value"])}
        if canon_num:
            canon_forms.add(canon_num.group(0))
        exclude = "|".join(re.escape(f) for f in sorted(canon_forms, key=len, reverse=True))
        # 用单位锚定而非 label 前缀：前文措辞一变（"店里一天"→"一天"）旧正则就失效。
        # 同单位不同量会误报，故一律 suspect 候选、需复核，绝不冒充确认结论。
        nums = r"[0-9一二三四五六七八九十百千万两]+\.?[0-9]?"
        must_not = [f"(?<![0-9一二三四五六七八九十百千万两])(?!{exclude})"
                    f"({nums})\\s*{re.escape(unit)}"] if confidence == "high" else []
        rules.append({
            "name": f"auto:{label or unit}/{unit}",
            "type": "value_set",       # 结构化数值契约：单位+允许值，中文/阿拉伯数字通吃
            "unit": unit,
            "allowed": values,
            "level": "suspect",         # 同单位未必同量，一律弱信号
            "must_not": must_not,       # 保留正则辅助（弱覆盖），供不支持 value_set 的旧消费者
            "must": [],
            "note": f"自动生成候选（{confidence}，{occ}次）；{canonical['chapter_id']}章首次出现 "
                    f"{canonical['raw']}。同单位未必同量，必须人工/LLM 复核后方可作为正式契约。",
            "_auto": True, "_confidence": confidence,
            "_evidence": canonical["evidence"],
        })

    alias_suspects = [s for s in self_consistency_check("\n".join(c.get("text", "") for c in chapters))
                      if s.get("type") == "entity_alias"]
    return {
        "anchors": anchors[:int(cfg.get("max_anchors", 40))],
        "contract": {"rules": rules, "_auto_generated": True,
                     "note": "候选契约：must_not 覆盖有限且可能误报，需复核后启用"},
        "conflicts": conflicts,
        "alias_suspects": alias_suspects,
        "note": "零 LLM 确定性抽取；候选≠确认。conflicts 是跨章同量多值，最有复核价值。",
    }


# ---------------------------------------------------------------------------
# 2) 伏笔台账：登记 / 回收 / 列表 / 超期
# ---------------------------------------------------------------------------
def _load_registry() -> dict:
    if REGISTRY_FILE.exists():
        try:
            data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
        except (ValueError, OSError):
            pass
    return {"version": 1, "items": []}


def _save_registry(data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, REGISTRY_FILE)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def foreshadow_register(chapter_id: str, items: list[str], chapter_index: int | None = None) -> dict:
    """登记伏笔。重复文本（同章或跨章）不重复建账，返回新增/已存在数量。"""
    data = _load_registry()
    added, existing = 0, 0
    for raw in items:
        text = str(raw).strip()[:80]
        if not text:
            continue
        key = _norm(text)
        if any(_norm(i.get("text")) == key or
               (len(key) >= 4 and key in _norm(i.get("text"))) for i in data["items"]):
            existing += 1
            continue
        data["items"].append({
            "id": f"FS-{len(data['items']) + 1:03d}",
            "text": text,
            "status": "open",
            "opened_at": str(chapter_id),
            "opened_index": chapter_index,
            "closed_at": None,
            "closed_evidence": None,
        })
        added += 1
    _save_registry(data)
    return {"added": added, "existing": existing, "total_open": sum(
        1 for i in data["items"] if i["status"] == "open")}


def foreshadow_close(chapter_id: str, items: list[str], evidence: str = "") -> dict:
    """回收伏笔：按文本精确/包含匹配把 open 条目标为 closed。"""
    data = _load_registry()
    closed, not_found = [], []
    for raw in items:
        key = _norm(raw)
        hit = None
        for i in data["items"]:
            norm_item = _norm(i.get("text"))
            if i["status"] == "open" and (key == norm_item or
                                          (len(key) >= 2 and key in norm_item) or
                                          (len(norm_item) >= 4 and norm_item in key)):
                hit = i
                break
        if hit:
            hit["status"] = "closed"
            hit["closed_at"] = str(chapter_id)
            hit["closed_evidence"] = evidence[:160]
            closed.append(hit["id"])
        else:
            not_found.append(str(raw)[:60])
    _save_registry(data)
    return {"closed": closed, "not_found": not_found}


def foreshadow_list(status: str = "open") -> dict:
    data = _load_registry()
    items = data["items"] if status == "all" else [i for i in data["items"] if i["status"] == status]
    return {"status": status, "items": items, "registry_file": str(REGISTRY_FILE)}


def foreshadow_overdue(current_chapter_index: int | None, max_age: int | None = None) -> dict:
    """超期未收提醒：open 条目距当前章超过 max_age 即列入。无章序时按章号字符串粗判。"""
    cfg = _brief_cfg()
    age = int(max_age if max_age is not None else cfg.get("overdue_chapters", 8))
    data = _load_registry()
    overdue = []
    for i in data["items"]:
        if i["status"] != "open":
            continue
        opened = i.get("opened_index")
        if current_chapter_index is None or opened is None:
            continue  # 无可靠章序时不臆断超期
        if current_chapter_index - int(opened) >= age:
            overdue.append({**i, "age": current_chapter_index - int(opened)})
    return {"max_age": age, "current_index": current_chapter_index,
            "overdue": overdue, "note": "缺少章序信息时不判定超期，避免误报"}


def foreshadow_sync_summary(summary: dict, chapter_index: int | None = None) -> dict:
    """从章节摘要同步台账：new_foreshadow 登记，closed_foreshadow 回收。"""
    cid = str(summary.get("chapter_id", ""))
    reg = foreshadow_register(cid, summary.get("new_foreshadow") or [], chapter_index)
    close = foreshadow_close(cid, summary.get("closed_foreshadow") or [],
                             evidence="摘要标记回收：" + str(summary.get("hook", ""))[:80])
    return {"registered": reg, "closed": close}


# ---------------------------------------------------------------------------
# 3) 评审结论 → 下一章写作约束块
# ---------------------------------------------------------------------------
def _iter_issues(review: Any) -> list[dict]:
    """从单章/批次报告里收集 issue 与红队攻击点，只取真实存在的字段。"""
    out: list[dict] = []
    if not isinstance(review, dict):
        return out
    reports = []
    if isinstance(review.get("chapters"), dict):
        reports = list(review["chapters"].values())
    elif "stages" in review:
        reports = [review]
    for rep in reports:
        if not isinstance(rep, dict):
            continue
        cid = str(rep.get("chapter_id", ""))
        stages = rep.get("stages") or {}
        ov = rep.get("overall") or {}
        for v in ov.get("vetoes", []) or []:
            out.append({"level": "high", "source": "veto", "chapter_id": cid,
                        "evidence": str(v), "instruction": f"规避否决项：{v}"})
        for stage, obj in stages.items():
            if not isinstance(obj, dict):
                continue
            for it in obj.get("issues", []) or []:
                if isinstance(it, dict):
                    out.append({"level": it.get("level", "mid"), "source": stage,
                                "chapter_id": cid, "evidence": str(it.get("evidence", ""))[:120],
                                "instruction": str(it.get("suggestion", ""))[:160] or "复核该问题"})
            for it in obj.get("attack_points", []) or []:
                if isinstance(it, dict) and it.get("level") in ("fatal", "high", "mid"):
                    out.append({"level": it.get("level"), "source": "red_team", "chapter_id": cid,
                                "evidence": str(it.get("evidence", ""))[:120],
                                "instruction": str(it.get("suggestion", ""))[:160] or "红队挑刺点，需规避"})
        for sig in (rep.get("machine_signals") or {}).get("self_consistency", []) or []:
            out.append({"level": "suspect", "source": "self_consistency", "chapter_id": cid,
                        "evidence": str(sig.get("evidence", ""))[:120],
                        "instruction": str(sig.get("suggestion", ""))[:160]})
    return out


def next_chapter_brief(review: dict, *, chapter_id: str = "", persona: str = "",
                       platform: str = "qimao", facts: dict | None = None,
                       current_chapter_index: int | None = None) -> dict:
    """把评审结论转成下一章约束块。纯结构化转写，零 LLM，不新增判断。"""
    cfg = _brief_cfg()
    if not cfg.get("enabled", True):
        return {"enabled": False}
    issues = _iter_issues(review)
    rank = {"fatal": 0, "high": 1, "mid": 2, "suspect": 3, "low": 4}
    issues.sort(key=lambda x: rank.get(str(x.get("level")), 9))
    must_avoid = issues[:int(cfg.get("max_avoid", 8))]

    # 人设锚：从人设铁律里拆句；不臆造，原文为空则不产出
    persona_anchors = [s.strip() for s in re.split(r"[；;。\n]", persona) if s.strip()][:6]

    # 硬数据锚：只取候选契约里的自动条目，标注需复核
    data_anchors = []
    for r in ((facts or {}).get("rules") or [])[:10]:
        if isinstance(r, dict):
            data_anchors.append({"name": r.get("name"), "note": r.get("note", ""),
                                 "must_not": r.get("must_not", [])})

    # 伏笔：未回收 + 超期未收
    open_items = foreshadow_list("open")["items"]
    overdue = foreshadow_overdue(current_chapter_index)["overdue"]
    focus = []
    seen = set()
    for it in overdue + open_items:
        fid = it.get("id")
        if fid in seen:
            continue
        seen.add(fid)
        focus.append({"id": fid, "text": it.get("text"), "opened_at": it.get("opened_at"),
                      "overdue": it in overdue})
        if len(focus) >= int(cfg.get("max_foreshadow", 8)):
            break

    stub = False
    if isinstance(review, dict):
        stub = bool(review.get("summary", {}).get("verdict", "").startswith("未定")) or any(
            isinstance(s, dict) and s.get("_stub")
            for rep in (review.get("chapters", {}) if isinstance(review.get("chapters"), dict) else
                        [review]).values() if isinstance(rep, dict)
            for s in (rep.get("stages") or {}).values()
        )

    lines = [f"## 第 {chapter_id or '下一'} 章写作约束（獬豸生成）", ""]
    if must_avoid:
        lines.append("### 必须规避（来自上轮评审）")
        for x in must_avoid:
            lines.append(f"- [{x['level']}] {x['source']}：{x['evidence']} → {x['instruction']}")
    if persona_anchors:
        lines.append("")
        lines.append("### 人设锚（不得漂移）")
        lines.extend(f"- {s}" for s in persona_anchors)
    if data_anchors:
        lines.append("")
        lines.append("### 硬数据锚（候选，需复核）")
        lines.extend(f"- {d['name']}：{d['note'][:80]}" for d in data_anchors)
    if focus:
        lines.append("")
        lines.append("### 伏笔推进/回收")
        for f in focus:
            tag = "【超期】" if f["overdue"] else ""
            lines.append(f"- {tag}{f['id']}（{f['opened_at']}章埋）：{f['text']}")
    if stub:
        lines.append("")
        lines.append("> 注意：上轮 L3 为桩模式，本约束块只含机器层信号，语义问题需另行评审。")
    lines.append("")
    lines.append("> 本约束块是评审结论的结构化回流，不替代写完后的重新评审。")

    return {
        "chapter_id": chapter_id, "platform": platform,
        "must_avoid": must_avoid,
        "persona_anchors": persona_anchors,
        "data_anchors": data_anchors,
        "foreshadow_focus": focus,
        "overdue_count": len(overdue),
        "stub_source": stub,
        "prompt": "\n".join(lines),
        "note": "零 LLM 结构化转写；候选契约与机器层 suspect 均需复核，不构成新判定。",
    }
