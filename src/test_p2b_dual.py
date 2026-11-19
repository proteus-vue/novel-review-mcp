"""P2b 双评审验证：合并/分歧纯函数 + 端到端开关/一票否决/成本账本。

默认关闭，测试内显式开启，不改任何现有判定口径。
运行: python test_p2b_dual.py
"""
from __future__ import annotations

import asyncio
import json

import cache
import committee
import config
import engine
import llm

CHAPTER = "他推开门，看到桌上放着一份合同。对方点头，他签下名字。谁料这只是开始。"


class AgreeGateway:
    """主评审：一致放行。"""
    backend = "agree"
    model = "agree-1"

    async def chat(self, prompt: str) -> str:
        if "一审编辑" in prompt:
            p = {"stage": "first_review", "pass": True, "verdict": "pass", "redline_hits": [], "issues": []}
        elif "二审编辑" in prompt:
            p = {"stage": "second_review", "score": 88, "issues": []}
        elif "三审" in prompt:
            p = {"stage": "third_review", "score": 88, "issues": []}
        elif "红队挑刺员" in prompt:
            p = {"stage": "red_team", "blocking": False, "attack_points": [], "verdict": "accept"}
        elif "头部作品分析师" in prompt:
            p = {"stage": "benchmark", "total": 8.8, "gaps": [], "verdict": "near"}
        elif "连续性审校员" in prompt:
            p = {"stage": "anti_drift", "conflicts": [], "pass": True}
        elif "把本章压成" in prompt:
            p = {"chapter_id": "?", "actors": ["他"], "actions": ["签字"], "conflicts": ["对峙"],
                 "payoffs": ["签约"], "hook": "开始", "new_foreshadow": [], "closed_foreshadow": [],
                 "hard_data": {"time": "", "numbers": []}, "persona_state": ""}
        else:
            p = {"pass": True, "score": 88, "issues": []}
        return json.dumps(p, ensure_ascii=False)


class DissentGateway:
    """第二评审：红队 blocking + 对标 below + 分数显著偏低。"""
    backend = "dissent"
    model = "dissent-1"

    async def chat(self, prompt: str) -> str:
        if "红队挑刺员" in prompt:
            p = {"stage": "red_team", "blocking": True,
                 "attack_points": [{"level": "high", "evidence": "动机断裂"}], "verdict": "reject"}
        elif "头部作品分析师" in prompt:
            p = {"stage": "benchmark", "total": 6.0, "gaps": ["爽点稀薄"], "verdict": "below"}
        else:
            p = {"pass": True, "score": 88, "stage": "other"}
        return json.dumps(p, ensure_ascii=False)


def _check(out: dict, name: str, cond: bool) -> None:
    out[name] = "PASS" if cond else "FAIL"


def test_pure_functions(out: dict) -> None:
    cfg = {"disagreement_gap": 10}
    agreed = committee.merge_pair(
        "red_team",
        {"score": 88, "verdict": "accept", "blocking": False, "attack_points": []},
        {"score": 90, "verdict": "accept", "blocking": False, "attack_points": []}, cfg)
    _check(out, "一致无分歧", not agreed["disagreement"] and agreed["merged"]["score"] == 89)

    split = committee.merge_pair(
        "red_team",
        {"score": 88, "verdict": "accept", "blocking": False},
        {"score": 60, "verdict": "accept", "blocking": False}, cfg)
    _check(out, "分差触发分歧", split["disagreement"] and any("分差" in r for r in split["reasons"]))

    veto = committee.merge_pair(
        "red_team",
        {"score": 95, "verdict": "accept", "blocking": False},
        {"score": 92, "verdict": "reject", "blocking": True}, cfg)
    _check(out, "一票否决不被多数覆盖",
           veto["merged"]["blocking"] and veto["merged"]["verdict"] == "reject")

    scale = committee.merge_pair(
        "benchmark",
        {"total": 8.5, "verdict": "near"},
        {"total": 90, "verdict": "near"}, cfg)
    _check(out, "十分制百分制归一不分歧", not scale["disagreement"])

    odd = committee.merge_pair(
        "benchmark",
        {"total": 8.5, "verdict": "near"},
        {"total": 9.5, "verdict": "below"}, cfg)
    _check(out, "语义冲突即分歧", odd["disagreement"] and odd["merged"]["verdict"] == "below")

    summary = committee.summarize_committee({
        "red_team": {"disagreement": True, "reasons": ["分差"], "independent": True,
                     "backend_pair": ["a", "b"]},
        "benchmark": {"disagreement": False, "reasons": [], "independent": True,
                      "backend_pair": ["a", "b"]},
    })
    _check(out, "committee汇总", summary["disagreements"] == ["red_team"] and summary["independent"])


async def test_integration(out: dict) -> None:
    llm.set_secondary_gateway(None)
    llm.set_gateway(AgreeGateway())
    config.RULES["dual_review"]["enabled"] = False
    cache.clear()
    off = await engine.review_chapter(CHAPTER, "OFF", "qimao")
    _check(out, "默认关闭调用数不变", off["telemetry"]["llm_calls"] == 5)
    _check(out, "默认关闭无committee",
           off["overall"]["committee"]["enabled"] is False)

    config.RULES["dual_review"]["enabled"] = True
    cache.clear()
    on = await engine.review_chapter(CHAPTER, "ON", "qimao")
    cm = on["overall"]["committee"]
    _check(out, "双开调用数翻倍", on["telemetry"]["llm_calls"] == 7)
    _check(out, "同网关标注非独立", cm["enabled"] and not cm["independent"])
    _check(out, "一致仍放行", on["overall"]["verdict"] == "放行" and not cm["disagreements"])

    llm.set_secondary_gateway(DissentGateway())
    cache.clear()
    conflict = await engine.review_chapter(CHAPTER, "CFT", "qimao")
    ov = conflict["overall"]
    _check(out, "独立网关标注", ov["committee"]["independent"])
    _check(out, "分歧禁止自动放行",
           not ov["pass"] and ("分歧" in ov["verdict"] or "禁止" in ov["verdict"]))
    _check(out, "红队一票否决保留", "红队否决" in "、".join(ov["vetoes"]))

    cache.clear()
    batch = await engine.review_batch([{"id": "C1", "text": CHAPTER}], "qimao")
    _check(out, "批次分歧阻断",
           batch["strict_gate"]["disagreement_chapters"] and batch["summary"]["verdict"] != "放行")

    llm.set_secondary_gateway(None)
    config.RULES["dual_review"]["enabled"] = False


async def main() -> None:
    out: dict[str, str] = {}
    test_pure_functions(out)
    await test_integration(out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    fails = [k for k, v in out.items() if v == "FAIL"]
    print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")


if __name__ == "__main__":
    asyncio.run(main())
