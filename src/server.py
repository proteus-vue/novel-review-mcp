"""獬豸（Xiezhi）· 小说评审加速 MCP Server —— 三审 + 红队 + 对标头部 + 反漂移 + 增量高效。

运行:  python server.py            (stdio，供写稿 Agent 挂载)
自测:  python server.py --selfcheck
demo:  python demo.py
"""
from __future__ import annotations

import json
import sys

import blind
import cache
import committee
import engine
import facts
import layers
from config import RULE_VERSION, PLATFORMS, BENCHMARKS, PROMPTS

# 兼容 mcp 1.x (FastMCP) 与 2.x (MCPServer)
try:
    from mcp.server.fastmcp import FastMCP as _Server
except ImportError:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server

mcp = _Server("xiezhi-review")


@mcp.tool()
async def check_redline(text: str, chapter_id: str = "", platform: str = "qimao") -> dict:
    """L1 形态红线 + L2 关键词打分，毫秒级；致命项直接判禁止发布。"""
    return await engine.check_redline(text, chapter_id, platform)


@mcp.tool()
async def review_chapter(text: str, chapter_id: str = "", platform: str = "qimao",
                         facts: dict | None = None, persona: str = "", revision_note: str = "") -> dict:
    """单章三审流水线：一审合规定位（硬拒短路）→二审内容质量→三审终审漂移→红队挑刺→对标头部。"""
    return await engine.review_chapter(text, chapter_id, platform, facts, persona, revision_note)


@mcp.tool()
async def review_batch(chapters: list[dict], platform: str = "qimao", facts: dict | None = None,
                       persona: str = "", revised_ids: list[str] | None = None) -> dict:
    """批次评审：影响半径增量 + 摘要金字塔跨章反漂移 + 复审铁律触发。chapters=[{id,text}]。"""
    return await engine.review_batch(chapters, platform, facts, persona, revised_ids)


@mcp.tool()
async def check_consistency(chapters: list[dict], facts: dict | None = None) -> dict:
    """跨章一致性：事实契约硬校验 + 摘要层 LLM 冲突发现。"""
    return await engine.check_consistency(chapters, facts)


@mcp.tool()
async def red_team(text: str, revision_note: str = "") -> dict:
    """红队挑刺：专找逻辑漏洞/动机断裂/硬巧合/读者会弃书的点。"""
    return await engine.red_team(text, revision_note)


@mcp.tool()
async def benchmark(text: str, platform: str = "qimao") -> dict:
    """对标头部：按结构密度维度打分，输出与头部基线的 gap。"""
    return await engine.benchmark(text, platform)


@mcp.tool()
async def platform_fit(text: str, platform: str = "qimao") -> dict:
    """平台适配度：按目标平台收稿偏好打分并列出差距。"""
    return await engine.platform_fit(text, platform)


@mcp.tool()
async def check_opening(text: str, platform: str = "qimao") -> dict:
    """黄金开篇：前 300 字主角/冲突/悬念 + 800 字金手指。"""
    return await engine.check_opening(text, platform)


@mcp.tool()
async def check_chapter_end(text: str) -> dict:
    """章末钩子判定与强度。"""
    return await engine.check_chapter_end(text)


@mcp.tool()
async def impact_plan(chapters: list[dict], revised_ids: list[str] | None = None, radius: int | None = None) -> dict:
    """查看影响半径：哪些章全文重评、哪些只走摘要、哪些复用缓存。零成本，不调 LLM。"""
    return engine.impact_radius(chapters, revised_ids, radius)


@mcp.tool()
async def check_self_consistency(text: str) -> dict:
    """单章内部自洽弱信号：时序倒退/数字互斥/称谓变体。零成本，只报 suspect 与原文证据，不判 fatal。"""
    return {"suspects": layers.self_consistency_check(text),
            "note": "确定性弱信号，需 L3 或人工复核；不单独构成否决。"}


@mcp.tool()
async def check_pacing(chapters: list[dict]) -> dict:
    """节奏检查：爽点/冲突/钩子密度 + 节奏荒漠检测。机器层为主，LLM 仅跨章探针。"""
    return await engine.check_pacing(chapters)


@mcp.tool()
async def extract_facts(chapters: list[dict]) -> dict:
    """从正文抽硬数据生成候选事实契约（fact_check 兼容）。零 LLM，候选需复核，含跨章同单位多值 suspect。"""
    return facts.extract_facts(chapters)


@mcp.tool()
async def foreshadow_register(chapter_id: str, items: list[str], chapter_index: int | None = None) -> dict:
    """登记伏笔到持久化台账（去重）；chapter_index 用于超期计算。"""
    return facts.foreshadow_register(chapter_id, items, chapter_index)


@mcp.tool()
async def foreshadow_close(chapter_id: str, items: list[str], evidence: str = "") -> dict:
    """回收伏笔：按文本匹配把台账 open 条目标记为 closed。"""
    return facts.foreshadow_close(chapter_id, items, evidence)


@mcp.tool()
async def foreshadow_list(status: str = "open") -> dict:
    """查看伏笔台账：status=open|closed|all。"""
    return facts.foreshadow_list(status)


@mcp.tool()
async def foreshadow_overdue(current_chapter_index: int | None = None, max_age: int | None = None) -> dict:
    """超期未收伏笔提醒；无章序信息时不臆断，返回空。"""
    return facts.foreshadow_overdue(current_chapter_index, max_age)


@mcp.tool()
async def next_chapter_brief(review: dict, chapter_id: str = "", persona: str = "",
                             platform: str = "qimao", facts: dict | None = None,
                             current_chapter_index: int | None = None) -> dict:
    """评审回流：把上轮评审结论+未收伏笔+人设锚转成下一章写作约束块（含可直接喂给写稿 Agent 的 prompt）。"""
    return facts.next_chapter_brief(review, chapter_id=chapter_id, persona=persona,
                                    platform=platform, facts=facts,
                                    current_chapter_index=current_chapter_index)


@mcp.tool()
async def cache_stats() -> dict:
    """缓存命中统计与清空能力（clear=false 只看）。"""
    return {"stats": dict(cache.stats), "rule_version": RULE_VERSION}


@mcp.tool()
async def cache_clear() -> dict:
    """清空评审缓存（规则/提示词升级后建议调用）。"""
    return {"cleared": cache.clear()}


@mcp.tool()
async def blind_review_package(chapters: list[dict], platform: str = "qimao",
                               include_prompts: bool = True, persona: str = "",
                               facts: dict | None = None) -> dict:
    """P2c 盲评输入包：只含正文+提示词，显式剔除既有评审字段，供独立评审员产出 judgements。"""
    return blind.build_blind_package(chapters, platform, include_prompts, persona, facts)


@mcp.tool()
async def blind_validate_judgements(chapters: list[dict], judgements: dict) -> dict:
    """校验外部盲评 JSON 的结构与评分口径；不补分、不自动换算。"""
    return blind.validate_judgements(chapters, judgements, strict=True)


@mcp.tool()
async def blind_review(chapters: list[dict], judgements: dict, platform: str = "qimao",
                       facts: dict | None = None, persona: str = "",
                       revised_ids: list[str] | None = None, clear_cache: bool = True) -> dict:
    """P2c：用独立盲评 judgements 回注完整三审流水线，附校验与命中审计；前后清缓存防污染。"""
    return await blind.blind_review(chapters, judgements, platform, facts, persona, revised_ids, clear_cache)


@mcp.tool()
async def blind_compare(original_report: dict, blind_report: dict) -> dict:
    """事后对照原评审与盲评的分数/判定/否决差异；只呈现，不自动改写判定。"""
    return blind.blind_compare(original_report, blind_report)


@mcp.tool()
async def list_profiles() -> dict:
    """列出可用平台档案、规则版本与提示词维度。"""
    return {
        "rule_version": RULE_VERSION,
        "platforms": list(PLATFORMS.get("platforms", {})),
        "benchmark_dimensions": [d["id"] for d in BENCHMARKS.get("dimensions", [])],
        "prompt_keys": sorted(PROMPTS),
    }


def selfcheck() -> dict:
    """本地自检：规则/红线/短路/结构完整，含 P0 信号、P2c 盲评工具与判定口径。"""
    checks = {}
    checks["rules_version"] = RULE_VERSION
    checks["platforms"] = len(PLATFORMS.get("platforms", {}))
    checks["prompt_keys"] = len(PROMPTS)
    bad = "他执行了 rm -rf /var/log/archive 并 chattr -i 修改文件"
    hits = layers.l1_check(bad)
    checks["l1_catches_reproducible_command"] = any(h["type"] == "reproducible_command" for h in hits)
    checks["l1_short_circuit"] = layers.has_hard_reject(hits)
    leak = "正文里写着【本章标注】新埋伏笔：F17"
    checks["l1_catches_internal_leak"] = any(h["type"] == "internal_marker_leak" for h in layers.l1_check(leak))
    checks["unknown_platform_no_crash"] = bool(engine.get_platform("nonexistent")[0])
    # P0 新能力自检
    seq = "第7天他签下订单。第3天他又来到这里。"
    checks["self_consistency_catches_time_backward"] = any(
        s["type"] == "time_backward" for s in layers.self_consistency_check(seq))
    checks["foreshadow_without_F_id"] = bool(layers.extract_foreshadow("他不知道的是，箱子还没打开。"))
    sig = layers.pacing_signals("他成交了。对手反击。谁料还有后手？")
    checks["pacing_not_stub"] = sig.get("payoff", {}).get("count", 0) > 0 and \
        sig.get("conflict", {}).get("count", 0) > 0
    # P1 闭环自检
    fx = facts.extract_facts([
        {"id": "A", "text": "一天三百二十单。"},
        {"id": "B", "text": "一天二百九十单。"},
    ])
    checks["facts_extract_same_unit_conflict"] = any(
        c["unit"] == "单" and c["level"] == "suspect" for c in fx["conflicts"])
    checks["facts_contract_fact_check_compatible"] = all(
        isinstance(r.get("must_not"), list) and isinstance(r.get("must"), list)
        for r in fx["contract"]["rules"])
    brief = facts.next_chapter_brief(
        {"chapters": {"A": {"chapter_id": "A", "stages": {"red_team": {
            "attack_points": [{"level": "high", "evidence": "无冲突", "suggestion": "加主动决策"}]}},
            "overall": {"verdict": "修改后发布", "vetoes": []}}}},
        chapter_id="C", persona="克制、算账")
    checks["brief_reflows_review"] = bool(brief["must_avoid"]) and "写作约束" in brief["prompt"]
    # P2c 盲评回归自检
    package = blind.build_blind_package([{"id": "X", "text": "正文", "score": 99}])
    checks["blind_package_filters_leak"] = (
        not package["isolation"]["safe"] and "score" not in package["chapters"][0]
    )
    validation = blind.validate_judgements([{"id": "X", "text": "正文"}], {"X": {}}, strict=True)
    checks["blind_validation_catches_missing"] = not validation["valid"]
    comparison = blind.blind_compare(
        {"chapters": {"X": {"overall": {"score": 80, "verdict": "放行", "vetoes": []}}}},
        {"chapters": {"X": {"overall": {"score": 70, "verdict": "禁止发布", "vetoes": ["红队否决"]}}}},
    )
    checks["blind_compare_detects_flip"] = bool(
        comparison["rows"] and comparison["rows"][0]["verdict_flip"]
    )
    # P2b 双评审自检（合并逻辑，默认关闭不改判定口径）
    veto = committee.merge_pair(
        "red_team",
        {"score": 95, "verdict": "accept", "blocking": False},
        {"score": 92, "verdict": "reject", "blocking": True},
        {"disagreement_gap": 10},
    )
    checks["committee_veto_not_overridden"] = (
        veto["merged"]["blocking"] and veto["merged"]["verdict"] == "reject"
    )
    split = committee.merge_pair(
        "benchmark", {"total": 8.8, "verdict": "near"},
        {"total": 6.0, "verdict": "below"}, {"disagreement_gap": 10})
    checks["committee_disagreement_detected"] = (
        split["disagreement"] and split["merged"]["verdict"] == "below"
    )
    return checks


def main() -> None:
    """控制台入口（pyproject scripts / 直接运行共用）。"""
    if "--selfcheck" in sys.argv:
        print(json.dumps(selfcheck(), ensure_ascii=False, indent=2))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
