"""评审引擎：三审流水线 + 红队挑刺 + 对标头部 + 反漂移 + 高效增量调度。"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import cache
import committee
import layers
import summarizer
import telemetry
from config import (
    PROMPTS,
    RULES,
    dual_review_cfg,
    efficiency_cfg,
    get_benchmark_dimensions,
    get_platform,
    render_prompt,
    revision_policy,
    rubric_cfg,
)
from llm import get_secondary_gateway, judge

_sem = asyncio.Semaphore(efficiency_cfg().get("parallel", {}).get("llm_concurrency", 4))


async def _llm_stage(prompt_key: str, *, text: str = "", summary: str = "",
                     _stage: str | None = None, _gateway=None, **kwargs) -> dict:
    prompt = render_prompt(prompt_key, text=text, summary=summary, **kwargs)
    stage = _stage or prompt_key
    entered = time.perf_counter()
    async with _sem:
        called = time.perf_counter()
        try:
            result = await judge(prompt, gateway=_gateway)
        except Exception as exc:
            tel = telemetry.current()
            if tel is not None:
                tel.record_error(stage, str(exc))
            raise
    tel = telemetry.current()
    if tel is not None:
        meta = dict(result.get("_telemetry") or {})
        meta["queue_sec"] = round(called - entered, 4)
        tel.record_llm(stage, meta)
    return result


async def _dual_llm_stage(prompt_key: str, stage: str, **kwargs) -> dict:
    """P2b：对单一 stage 做双路评审并返回合并结果。

    返回结构保留原 stage 字段（merged）+ `_dual` 审计块；不抛异常，
    任一路失败则退化为单路并在 `_dual.degraded` 标注，绝不静默当成一致。
    """
    cfg = dual_review_cfg()
    secondary, independent = get_secondary_gateway()

    # 两路并行；第二路换模型/后端但 prompt 完全一致，判定口径不变。
    # 走 _llm_stage 保证两路都进 telemetry 账本（成本/耗时如实反映翻倍）。
    results = await asyncio.gather(
        _llm_stage(prompt_key, _stage=stage, **kwargs),
        _llm_stage(prompt_key, _stage=stage, _gateway=secondary, **kwargs),
        return_exceptions=True,
    )
    primary = results[0] if isinstance(results[0], dict) else {"_error": str(results[0])}
    if isinstance(results[1], dict):
        second = results[1]
        degraded = False
    else:
        second, degraded = {}, True

    merged = committee.merge_pair(stage, primary, second, cfg)
    out = merged["merged"]
    out["_dual"] = {
        "enabled": True,
        "independent": bool(independent),
        "degraded": degraded,
        "disagreement": merged["disagreement"],
        "reasons": merged["reasons"],
        "scores": merged["scores"],
        "verdicts": merged["verdicts"],
        "backend_pair": [primary.get("_backend"), second.get("_backend", primary.get("_backend"))],
        "model_pair": [primary.get("_model"), second.get("_model", primary.get("_model"))],
        "note": (
            "双评审一致" if not merged["disagreement"] else
            "双评审分歧，禁止自动放行，需人工复核"
        ),
    }
    return out


def _dual_extra_calls() -> int:
    """双评审开启时每章额外 LLM 调用数（红队/对标各 +1），用于修正成本估算。"""
    cfg = dual_review_cfg()
    if not cfg.get("enabled", False):
        return 0
    return len(set(cfg.get("stages", ["red_team", "benchmark"])) & {"red_team", "benchmark"})


# ---------------------------------------------------------------------------
# 单章三审：一审硬拒短路，二审内容质量，三审终审漂移/伏笔/数据
# ---------------------------------------------------------------------------
async def review_chapter(
    text: str,
    chapter_id: str = "",
    platform: str = "qimao",
    facts: dict | None = None,
    persona: str = "",
    revision_note: str = "",
) -> dict:
    """对外入口：附带真实计时/成本账本；评分与闸门口径不变。"""
    wall0 = time.perf_counter()
    child, token, parent = telemetry.begin()
    try:
        result = await _review_chapter_impl(text, chapter_id, platform, facts, persona, revision_note)
    finally:
        snapshot = telemetry.end(child, token, parent)
    result["timing"] = {
        "wall_sec": round(time.perf_counter() - wall0, 4),
        "llm_cumulative_sec": round(child.llm_sec, 4),
        "llm_queue_sec": round(child.queue_sec, 4),
        "source": "review_chapter",
    }
    result["telemetry"] = snapshot
    return result


async def _review_chapter_impl(
    text: str,
    chapter_id: str = "",
    platform: str = "qimao",
    facts: dict | None = None,
    persona: str = "",
    revision_note: str = "",
) -> dict:
    t0 = time.time()
    profile, plat_key = get_platform(platform)
    result: dict[str, Any] = {
        "chapter_id": chapter_id,
        "platform": profile.get("name", plat_key),
        "stages": {},
        "hard_reject_short_circuit": False,
        # 机器层弱信号：零成本、确定性，供 L3/人工复核，不直接参与一票否决
        "machine_signals": {
            "self_consistency": layers.self_consistency_check(text),
            "pacing": layers.pacing_signals(text),
        },
    }

    # ---- 一审：L1 形态红线 + L2 打分（前置短路，毫秒级）----
    l1 = layers.l1_check(text)
    l2 = layers.l2_check(text)
    fatal = [h for h in l1 if h.get("level") == "fatal"]
    first = {
        "stage": "first_review",
        "pass": not fatal,
        "l1_hits": l1,
        "l2_hits": l2,
    }
    if fatal:
        first["verdict"] = "reject"
        first["note"] = "L1 致命红线命中，短路：不调用任何 LLM"
        result["stages"]["first_review"] = first
        result["hard_reject_short_circuit"] = True
        result["overall"] = {"pass": False, "verdict": "禁止发布", "score": 0}
        result["elapsed_sec"] = round(time.time() - t0, 3)
        return result

    cached = cache.get(cache.make_key(chapter_id, text, plat_key, "first_review"))
    if cached is not None:
        first = cached
    else:
        l3 = await _llm_stage(
            "first_review",
            text=text[:5000],
            platform=profile.get("name", plat_key),
            profile=str({k: profile.get(k) for k in ("tone", "focus", "forbidden", "hard_window")}),
        )
        first["l3"] = l3
        first["verdict"] = l3.get("verdict", "auto")
        first["pass"] = bool(l3.get("pass", not fatal)) and not any(
            h.get("level") == "fatal" for h in l3.get("redline_hits", [])
        )
        cache.set(cache.make_key(chapter_id, text, plat_key, "first_review"), first)
    result["stages"]["first_review"] = first

    # 二审/三审/红队/对标并行（无依赖）
    # P2b：红队/对标可按配置走双评审，其余阶段始终单评审，控制成本。
    dcfg = dual_review_cfg()
    dual_enabled = bool(dcfg.get("enabled", False))
    dual_stages = set(dcfg.get("stages", ["red_team", "benchmark"]))

    def _pick(name: str, key: str, kwargs: dict):
        if dual_enabled and name in dual_stages:
            return _dual_llm_stage(key, name, **kwargs)
        return _llm_stage(key, _stage=name, **kwargs)

    tasks = [
        ("second_review", _pick("second_review", "second_review", {
            "text": text[:6000], "platform": profile.get("name", plat_key),
            "profile": str(profile.get("must_have", [])),
        })),
        ("third_review", _pick("third_review", "third_review", {
            "text": text[:6000],
            "persona": persona or "未提供人设铁律，按通用一致性判",
            "facts": str(facts or {}),
        })),
        ("red_team", _pick("red_team", "red_team", {
            "text": text[:6000], "revision_note": revision_note or "无改动记录",
        })),
        ("benchmark", _pick("benchmark", "benchmark", {
            "text": text[:6000],
            "baseline": str([{"id": d["id"], "baseline": d["baseline"]} for d in get_benchmark_dimensions(plat_key)]),
        })),
    ]
    results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
    for (name, _), val in zip(tasks, results):
        result["stages"][name] = val if isinstance(val, dict) else {"error": str(val)}

    # 聚合判定：每个维度优先取该 stage 各自的 score，不再让 third 重复计入四维。
    dims = rubric_cfg().get("dimensions", [])
    second = result["stages"].get("second_review", {})
    third = result["stages"].get("third_review", {})
    red = result["stages"].get("red_team", {})
    bench = result["stages"].get("benchmark", {})
    score_map = {
        "persona": float(third.get("score", second.get("score", 0)) or 0),
        "structure_payoff": float(second.get("score", 0) or 0),
        "continuity": float(third.get("score", 0) or 0),
        "ai_flavor_voice": float(third.get("score", 0) or 0),
    }
    total = round(sum(score_map.get(d["id"], 0) * d["weight"] for d in dims), 2)

    # 独立视角否决信号（严格档核心）：红队 blocking/reject、对标 below
    gate = RULES.get("strict_gate", {})
    vetoes = []
    if gate.get("red_team_veto", True) and (red.get("blocking") or red.get("verdict") == "reject"):
        vetoes.append("红队否决")
    if gate.get("benchmark_veto", True) and bench.get("verdict") == "below":
        vetoes.append("对标低于头部基线")
    if fatal:
        vetoes.append("L1致命红线")

    # P2b：双评审分歧单独汇总；分歧不是否决，但禁止自动放行，必须人工复核。
    dual_pairs = {name: result["stages"].get(name, {}).get("_dual")
                  for name in ("red_team", "benchmark")}
    committee_report = committee.summarize_committee(dual_pairs)
    disagreements = committee_report["disagreements"]

    if second.get("_stub") or third.get("_stub"):
        verdict = "未定（L3 桩模式）"
    elif vetoes:
        verdict = "禁止发布（" + "、".join(vetoes) + "）"
    elif disagreements:
        verdict = "修改后发布（双评审分歧待人工复核：" + "、".join(disagreements) + "）"
    elif gate.get("enabled", False) and total < gate.get("chapter_min", 85):
        verdict = "修改后发布（未达严格档单章线 %s）" % gate.get("chapter_min", 85)
    else:
        v = rubric_cfg().get("verdict", {})
        verdict = "放行" if total >= v.get("pass", 80) else ("修改后发布" if total >= v.get("revise", 70) else "重写")

    result["overall"] = {
        "pass": verdict == "放行", "verdict": verdict, "score": total,
        "vetoes": vetoes, "dimension_scores": score_map,
        "committee": committee_report,
        "red_team": {"blocking": bool(red.get("blocking")), "verdict": red.get("verdict")},
        "benchmark": {"verdict": bench.get("verdict"), "total": bench.get("total")},
    }
    result["elapsed_sec"] = round(time.time() - t0, 3)
    return result


# ---------------------------------------------------------------------------
# 影响半径：只重评改动章 + 相邻章，未改动章复用缓存
# ---------------------------------------------------------------------------
def impact_radius(chapters: list[dict], revised_ids: list[str] | None, radius: int | None = None) -> dict:
    r = radius if radius is not None else efficiency_cfg().get("impact_radius", {}).get("default", 1)
    ids = {c.get("id") for c in chapters}
    revised = [i for i in (revised_ids or []) if i in ids]
    full, summary_only, reuse = set(), set(), set()
    if not revised:
        full = set(ids)  # 未提供改动清单 → 全量首评（首次运行）
    else:
        for cid in revised:
            full.add(cid)
            idx = next(i for i, c in enumerate(chapters) if c.get("id") == cid)
            for j in range(max(0, idx - r), min(len(chapters), idx + r + 1)):
                if j != idx:
                    summary_only.add(chapters[j].get("id"))
        reuse = ids - full - summary_only
    return {"full": sorted(full, key=str), "summary_only": sorted(summary_only, key=str), "reuse": sorted(reuse, key=str)}


# ---------------------------------------------------------------------------
# 批量评审：增量 + 摘要金字塔 + 跨章反漂移
# ---------------------------------------------------------------------------
async def review_batch(
    chapters: list[dict],
    platform: str = "qimao",
    facts: dict | None = None,
    persona: str = "",
    revised_ids: list[str] | None = None,
) -> dict:
    """对外入口：批次真实计时/成本账本；闸门与评分口径不变。"""
    wall0 = time.perf_counter()
    child, token, parent = telemetry.begin()
    try:
        report = await _review_batch_impl(chapters, platform, facts, persona, revised_ids)
    finally:
        snapshot = telemetry.end(child, token, parent)

    config = report.setdefault("timing", {})
    config.update({
        "wall_sec": round(time.perf_counter() - wall0, 4),
        "llm_cumulative_sec": round(child.llm_sec, 4),
        "llm_queue_sec": round(child.queue_sec, 4),
        "source": "review_batch",
    })
    report["telemetry"] = snapshot
    efficiency = report.setdefault("efficiency", {})
    efficiency["llm_calls_actual"] = snapshot["llm_calls"]
    efficiency["llm_errors_actual"] = snapshot["llm_errors"]
    efficiency["llm_queue_sec"] = snapshot["llm_queue_sec"]
    efficiency["tokens"] = snapshot["tokens"]
    efficiency["estimate_vs_actual_note"] = (
        "llm_calls_estimate 保留旧字段兼容；actual/tokens/timing 来自真实调用账本，"
        "并发下 llm_cumulative_sec 可大于 wall_sec。"
    )
    return report


async def _review_batch_impl(
    chapters: list[dict],
    platform: str = "qimao",
    facts: dict | None = None,
    persona: str = "",
    revised_ids: list[str] | None = None,
) -> dict:
    t0 = time.time()
    cache.reset_stats()
    profile, plat_key = get_platform(platform)
    plan = impact_radius(chapters, revised_ids)
    by_id = {c.get("id"): c for c in chapters}
    phases: dict[str, float] = {}
    p0 = time.perf_counter()

    # 1) 全章 L1/L2 前置（毫秒级，任何章都不跳过红线）
    redline = {cid: {"l1": layers.l1_check(c.get("text", "")), "l2": layers.l2_check(c.get("text", ""))}
               for cid, c in by_id.items()}
    rejected = [cid for cid, r in redline.items() if layers.has_hard_reject(r["l1"])]
    phases["l1_l2_sec"] = round(time.perf_counter() - p0, 4)
    p0 = time.perf_counter()

    # 2) 只对 full 集合做三审并行；reuse 集合读缓存
    full = [cid for cid in plan["full"] if cid in by_id and cid not in rejected]
    reviews = await asyncio.gather(*[
        review_chapter(by_id[cid].get("text", ""), str(cid), plat_key, facts, persona)
        for cid in full
    ])
    phases["chapter_review_sec"] = round(time.perf_counter() - p0, 4)
    p0 = time.perf_counter()
    chapter_reports = {}
    for cid in rejected:
        chapter_reports[cid] = {"status": "reject", "hits": redline[cid]["l1"]}
    for cid, rep in zip(full, reviews):
        chapter_reports[cid] = {"status": "reviewed", **rep}
    for cid in plan["reuse"]:
        cached = cache.get(cache.make_key(str(cid), by_id[cid].get("text", ""), plat_key, "batch"))
        chapter_reports[cid] = cached or {"status": "reuse_no_cache", "note": "首次运行或缓存已失效，已由 full 覆盖"}

    # 3) 跨章：只喂摘要（摘要本身带缓存）
    summaries = await summarizer.summarize_many(
        [by_id[cid] for cid in plan["full"] + plan["summary_only"] if cid in by_id]
    )
    summary_text = summarizer.compact(summaries)
    phases["summary_sec"] = round(time.perf_counter() - p0, 4)
    p0 = time.perf_counter()
    anti_drift = await _llm_stage("anti_drift", summary=summary_text, facts=str(facts or {}), persona=persona or "未指定")
    fact_conflicts = layers.fact_check(chapters, facts)

    # 4) 复审触发器：改动 ≥ 阈值必须复审并专问"改出什么新问题"
    rev = revision_policy()
    need_re_review = len(revised_ids or []) >= rev.get("delta_threshold", 10)
    re_review = None
    if need_re_review:
        re_review = await _llm_stage(
            "red_team",
            text=summary_text,
            revision_note=f"共改动 {len(revised_ids or [])} 处，{rev.get('must_ask', '改出什么新问题')}",
        )
    phases["cross_chapter_sec"] = round(time.perf_counter() - p0, 4)
    p0 = time.perf_counter()

    # 5) 聚合
    scores = [r["overall"]["score"] for r in chapter_reports.values()
              if isinstance(r, dict) and isinstance(r.get("overall"), dict)]
    avg = round(sum(scores) / len(scores), 2) if scores else 0
    # 桩模式没有真实语义判定，不给"放行/禁止"结论，明确标注未定
    stub_mode = bool(anti_drift.get("_stub")) or any(
        isinstance(r, dict) and isinstance(r.get("stages"), dict)
        and any(isinstance(s, dict) and s.get("_stub") for s in r["stages"].values())
        for r in chapter_reports.values()
    )
    # 严格档闸门：批次均分 + 单章最低分 + 红队/对标否决 + L1硬拒 + 反漂移
    gate = RULES.get("strict_gate", {})
    gate_on = gate.get("enabled", False)
    low_chapters = []
    veto_chapters = []
    disagreement_chapters = []
    for cid, r in chapter_reports.items():
        ov = r.get("overall", {}) if isinstance(r, dict) else {}
        if ov.get("vetoes"):
            veto_chapters.append({"id": cid, "vetoes": ov["vetoes"]})
        if gate_on and isinstance(ov.get("score"), (int, float)) and ov["score"] < gate.get("chapter_min", 85):
            low_chapters.append({"id": cid, "score": ov["score"]})
        cm = ov.get("committee", {})
        if isinstance(cm, dict) and cm.get("disagreements"):
            disagreement_chapters.append({"id": cid, "stages": cm["disagreements"]})

    if stub_mode:
        verdict = "未定（L3 桩模式，未调用真实模型）"
    else:
        hard_block = bool(rejected) or bool(veto_chapters) or anti_drift.get("pass") is False
        # 双评审分歧不是否决，但同样禁止自动放行（升级人工复核）。
        hard_block = hard_block or bool(disagreement_chapters)
        if gate_on:
            hard_block = hard_block or (avg < gate.get("batch_avg_min", 90)) or bool(low_chapters)
        if disagreement_chapters and not (rejected or veto_chapters or anti_drift.get("pass") is False):
            verdict = "修改后发布（双评审分歧待人工复核）"
        elif hard_block:
            verdict = "禁止发布（未达严格档闸门）"
        else:
            verdict = "放行"

    gate_report = {
        "enabled": gate_on,
        "batch_avg": avg,
        "batch_avg_min": gate.get("batch_avg_min", 90),
        "chapter_min": gate.get("chapter_min", 85),
        "low_chapters": low_chapters,
        "veto_chapters": veto_chapters,
        "disagreement_chapters": disagreement_chapters,
        "l1_rejected": rejected,
        "anti_drift_pass": anti_drift.get("pass"),
    }
    dcfg = dual_review_cfg()
    committee_summary = {
        "enabled": bool(dcfg.get("enabled", False)),
        "stages": list(dcfg.get("stages", ["red_team", "benchmark"])),
        "disagreement_gap": dcfg.get("disagreement_gap", 10),
        "disagreement_chapters": disagreement_chapters,
    }

    for cid in full:
        cache.set(cache.make_key(str(cid), by_id[cid].get("text", ""), plat_key, "batch"),
                  chapter_reports[cid])

    report = {
        "platform": profile.get("name", plat_key),
        "summary": {
            "chapters": len(chapters), "rejected": len(rejected),
            "reviewed": len(full), "reused": len(plan["reuse"]),
            "avg_score": avg, "verdict": verdict,
        },
        "impact_plan": plan,
        "efficiency": {
            "cache_hits": cache.stats["hits"], "cache_misses": cache.stats["misses"],
            "llm_calls_estimate": len(full) * (4 + _dual_extra_calls()) + len(summaries) + 2 + (1 if re_review else 0),
            "full_text_llm_chapters": len(full),
            "summary_only_llm_chapters": len(plan["summary_only"]),
            "no_full_volume_reread": efficiency_cfg().get("budget", {}).get("no_full_volume_reread", True),
        },
        "anti_drift": {"llm": anti_drift, "fact_conflicts": fact_conflicts},
        "strict_gate": gate_report,
        "committee": committee_summary,
        "re_review_triggered": need_re_review,
        "re_review": re_review,
        "chapters": chapter_reports,
        "timing": {"phases": phases},
        "elapsed_sec": round(time.time() - t0, 2),
    }
    phases["aggregate_sec"] = round(time.perf_counter() - p0, 4)
    cache.flush()
    return report


# ---------------------------------------------------------------------------
# 原子工具
# ---------------------------------------------------------------------------
async def check_redline(text: str, chapter_id: str = "", platform: str = "qimao") -> dict:
    l1 = layers.l1_check(text)
    l2 = layers.l2_check(text)
    return {"pass": not layers.has_hard_reject(l1), "hits": l1, "soft_hits": l2}


async def check_consistency(chapters: list[dict], facts: dict | None = None) -> dict:
    summaries = await summarizer.summarize_many(chapters)
    profile_text = summarizer.compact(summaries)
    llm = await _llm_stage("anti_drift", summary=profile_text, facts=str(facts or {}), persona="")
    return {"conflicts": layers.fact_check(chapters, facts), "llm_findings": llm}


async def check_pacing(chapters: list[dict]) -> dict:
    """节奏检查：机器层信号为主（零成本），LLM 只做跨章节奏探针。

    payoff_gap/conflict_density 由 layers.pacing_signals 真实计算，不再是桩。
    """
    per_chapter = []
    for c in chapters:
        sig = layers.pacing_signals(c.get("text", ""))
        sig["chapter_id"] = c.get("id", "")
        per_chapter.append(sig)
    summaries = await summarizer.summarize_many(chapters)
    key = "pacing_probe" if "pacing_probe" in PROMPTS else "anti_drift"
    llm = await _llm_stage(key, summary=summarizer.compact(summaries), facts="", persona="")

    # 节奏荒漠检测：连续 N 章爽点密度低于阈值
    warn = RULES.get("pacing_signals", {}).get("payoff_per_2000_warn", 0.5)
    dry_n = RULES.get("pacing_signals", {}).get("dry_spell_chapters", 2)
    gaps, run = [], []
    for s in per_chapter:
        # 短文本（<2千字）密度外推会失真：1个爽点/156字 = 12.8/2千字。
        # 对短章用原始计数判定，避免把正常章误标/漏标。
        chars = s.get("chars", 0)
        if chars < 2000:
            low = s.get("payoff", {}).get("count", 0) == 0
        else:
            low = s.get("payoff", {}).get("per_2000", 0) < warn
        if low:
            run.append(s.get("chapter_id"))
        else:
            if len(run) >= dry_n:
                gaps.append(run[:])
            run = []
    if len(run) >= dry_n:
        gaps.append(run[:])

    densities = [s.get("conflict", {}).get("per_1000", 0) for s in per_chapter]
    return {
        "per_chapter": per_chapter,
        "payoff_gap": gaps,                                   # 真实：节奏荒漠章段
        "conflict_density": round(sum(densities) / len(densities), 2) if densities else 0,
        "summaries": [s.get("chapter_id") for s in summaries],
        "llm": llm,
        "note": "机器层信号为零成本确定性计算；LLM 输出仅作跨章节奏参考。",
    }


async def platform_fit(text: str, platform: str = "qimao") -> dict:
    profile, key = get_platform(platform)
    return await _llm_stage(
        "platform_fit", text=text[:6000], platform=profile.get("name", key),
        profile=str({k: profile.get(k) for k in ("tone", "focus", "forbidden", "must_have", "metrics")}),
    )


async def red_team(text: str, revision_note: str = "") -> dict:
    return await _llm_stage("red_team", text=text[:6000], revision_note=revision_note or "无改动记录")


async def benchmark(text: str, platform: str = "qimao") -> dict:
    _, key = get_platform(platform)
    return await _llm_stage(
        "benchmark", text=text[:6000],
        baseline=str([{"id": d["id"], "baseline": d["baseline"], "weight": d["weight"]}
                      for d in get_benchmark_dimensions(key)]),
    )


async def check_chapter_end(text: str) -> dict:
    return await _llm_stage("chapter_end_hook", text=text[-400:])


async def check_opening(text: str, platform: str = "qimao") -> dict:
    profile, key = get_platform(platform)
    return await _llm_stage(
        "opening", text=text[:800], platform=profile.get("name", key),
        profile=str(profile.get("hard_window", {})),
    )
