"""獬豸（Xiezhi）· 小说评审加速 MCP Server —— 三审 + 红队 + 对标头部 + 反漂移 + 增量高效。

运行:  python server.py            (stdio，供写稿 Agent 挂载)
自测:  python server.py --selfcheck
demo:  python demo.py
"""
from __future__ import annotations

import json
import sys

import cache
import engine
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
async def cache_stats() -> dict:
    """缓存命中统计与清空能力（clear=false 只看）。"""
    return {"stats": dict(cache.stats), "rule_version": RULE_VERSION}


@mcp.tool()
async def cache_clear() -> dict:
    """清空评审缓存（规则/提示词升级后建议调用）。"""
    return {"cleared": cache.clear()}


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
    """本地自检：规则可加载、红线能命中、短路生效、结构完整、P0信号非桩。"""
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
    return checks


def main() -> None:
    """控制台入口（pyproject scripts / 直接运行共用）。"""
    if "--selfcheck" in sys.argv:
        print(json.dumps(selfcheck(), ensure_ascii=False, indent=2))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
