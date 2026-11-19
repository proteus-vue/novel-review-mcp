"""P2a 观测验证：真实调用数 / 耗时 / token 账本，且判定口径不变。

运行: python test_p2_telemetry.py
"""
from __future__ import annotations

import asyncio
import json

import cache
import engine
import llm

CHAPTER = "他推开门，看到桌上放着一份合同。对方点头，他签下名字。谁料这只是开始。"


class MeteredGateway:
    """带 usage 的假网关：验证账本采集，不依赖真实端点。"""

    backend = "metered"
    model = "meter-1"

    def __init__(self) -> None:
        self.usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    async def chat_detailed(self, prompt: str) -> tuple[str, dict]:
        await asyncio.sleep(0.002)
        if "一审编辑" in prompt:
            payload = {"stage": "first_review", "pass": True, "verdict": "pass",
                       "redline_hits": [], "issues": []}
        elif "二审编辑" in prompt:
            payload = {"stage": "second_review", "score": 88, "issues": []}
        elif "三审" in prompt:
            payload = {"stage": "third_review", "score": 88, "issues": []}
        elif "红队挑刺员" in prompt:
            payload = {"stage": "red_team", "blocking": False,
                       "attack_points": [], "verdict": "accept"}
        elif "头部作品分析师" in prompt:
            payload = {"stage": "benchmark", "total": 8.8, "gaps": [], "verdict": "near"}
        elif "连续性审校员" in prompt:
            payload = {"stage": "anti_drift", "conflicts": [], "pass": True}
        elif "把本章压成" in prompt:
            payload = {"chapter_id": "?", "actors": ["他"], "actions": ["签字"],
                       "conflicts": ["对峙"], "payoffs": ["签约"], "hook": "只是开始",
                       "new_foreshadow": [], "closed_foreshadow": [],
                       "hard_data": {"time": "", "numbers": []}, "persona_state": ""}
        else:
            payload = {"pass": True, "score": 88, "issues": []}
        return json.dumps(payload, ensure_ascii=False), dict(self.usage)


class LegacyGateway:
    """只实现 chat 的旧式自定义网关：不能伪造 token，但不能崩。"""

    backend = "legacy"
    model = "legacy-1"

    async def chat(self, prompt: str) -> str:
        return json.dumps({"stage": "legacy", "pass": True, "score": 88}, ensure_ascii=False)


async def main() -> None:
    out: dict[str, str] = {}
    llm.set_gateway(MeteredGateway())
    cache.clear()

    # 1) 网关层：真实 latency + usage 透传
    judged = await llm.judge("测试")
    tel = judged.get("_telemetry", {})
    out["judge带usage"] = "PASS" if (
        tel.get("usage_available") is True
        and tel.get("usage", {}).get("total_tokens") == 15
        and tel.get("latency_sec", 0) >= 0
    ) else "FAIL"

    # 2) 单章：5 次 L3 调用 + 阶段拆分 + 耗时字段
    rep = await engine.review_chapter(CHAPTER, "T1", "qimao")
    one = rep.get("telemetry", {})
    out["单章真实调用数"] = "PASS" if one.get("llm_calls") == 5 else "FAIL"
    out["单章阶段拆分"] = "PASS" if {
        "first_review", "second_review", "third_review", "red_team", "benchmark"
    } <= set(one.get("stages", {})) else "FAIL"
    out["单章耗时字段"] = "PASS" if (
        rep.get("timing", {}).get("wall_sec", 0) >= 0
        and rep.get("timing", {}).get("llm_cumulative_sec", 0) >= 0
    ) else "FAIL"

    # 3) 硬拒短路：零 LLM 调用也必须如实记 0
    bad = CHAPTER + "\n他执行了 rm -rf /var/log/archive 命令。"
    rep_bad = await engine.review_chapter(bad, "BAD", "qimao")
    out["硬拒短路零调用"] = "PASS" if rep_bad.get("telemetry", {}).get("llm_calls") == 0 else "FAIL"

    # 4) 批次：actual 调用数 / token 累计 / 阶段墙钟
    chapters = [{"id": f"{i:03d}", "text": f"{CHAPTER} 第{i}段。"} for i in range(1, 6)]
    cache.clear()
    batch1 = await engine.review_batch(chapters, "qimao")
    eff1 = batch1["efficiency"]
    out["批次actual调用"] = "PASS" if eff1.get("llm_calls_actual", 0) > 0 else "FAIL"
    out["批次token累计"] = "PASS" if (
        eff1.get("tokens", {}).get("available") is True
        and eff1["tokens"].get("total") == eff1["llm_calls_actual"] * 15
    ) else "FAIL"
    out["批次阶段墙钟"] = "PASS" if {
        "l1_l2_sec", "chapter_review_sec", "summary_sec", "cross_chapter_sec", "aggregate_sec"
    } <= set(batch1.get("timing", {}).get("phases", {})) else "FAIL"
    out["批次汇总账本"] = "PASS" if "summary" in batch1["telemetry"].get("stages", {}) else "FAIL"

    # 5) 增量复用：actual 调用数必须真实下降
    batch2 = await engine.review_batch(chapters, "qimao", revised_ids=["003"])
    eff2 = batch2["efficiency"]
    out["增量复用降调用"] = "PASS" if (
        eff2["llm_calls_actual"] < eff1["llm_calls_actual"]
        and eff2["cache_hits"] > eff1["cache_hits"]
    ) else "FAIL"

    # 6) 旧网关兼容：不提供 usage 就不伪造 token
    llm.set_gateway(LegacyGateway())
    legacy = await llm.judge("旧网关")
    out["旧网关不伪造token"] = "PASS" if (
        legacy["_telemetry"]["usage_available"] is False
        and legacy["_telemetry"]["usage"] == {}
    ) else "FAIL"

    # 7) 桩模式判定口径不变
    llm.set_gateway(llm.StubGateway())
    stub = await engine.review_chapter(CHAPTER, "STUB", "qimao")
    out["桩模式判定不变"] = "PASS" if "桩" in stub["overall"]["verdict"] else "FAIL"

    print(json.dumps(out, ensure_ascii=False, indent=2))
    fails = [k for k, v in out.items() if v == "FAIL"]
    print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")


if __name__ == "__main__":
    asyncio.run(main())
