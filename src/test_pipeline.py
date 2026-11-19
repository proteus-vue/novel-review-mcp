"""验证测试：注入返回真实 JSON 的网关，覆盖三审/红队/对标/复审/增量/短路关键路径。

运行: python test_pipeline.py
"""
from __future__ import annotations

import asyncio
import json

import cache
import engine
import llm
import server
from demo import CH1, CH2, CH3, CH4, CH5, FACTS, PERSONA


class FakeGateway:
    """模拟真实模型：按 prompt 里的 stage 关键词返回合规 JSON。"""
    backend = "fake"
    model = "fake-judge"

    async def chat(self, prompt: str) -> str:
        def has(s): return s in prompt
        if "你是网文资深编辑" in prompt:
            return json.dumps({"hook_ok": True, "elements_present": ["主角", "冲突"], "score": 8,
                               "issues": []}, ensure_ascii=False)
        if "一审编辑" in prompt:
            return json.dumps({"stage": "first_review", "pass": True, "verdict": "pass",
                               "redline_hits": [], "fit": {"score": 8, "gaps": []}, "issues": []}, ensure_ascii=False)
        if "二审编辑" in prompt:
            return json.dumps({"stage": "second_review", "score": 82, "hook_density": 2.0,
                               "payoff_per_2000": 1.2, "agency": True, "logic_holes": [],
                               "issues": []}, ensure_ascii=False)
        if "三审（终审）编辑" in prompt or "三审" in prompt:
            return json.dumps({"stage": "third_review", "score": 85,
                               "persona_drift": {"found": False, "evidence": []},
                               "foreshadow_issues": [], "facts_conflicts": [],
                               "ai_flavor": {"concentration": "低", "evidence": []}, "issues": []},
                              ensure_ascii=False)
        if "红队挑刺员" in prompt:
            return json.dumps({"stage": "red_team", "blocking": False,
                               "attack_points": [], "verdict": "accept"}, ensure_ascii=False)
        if "头部作品分析师" in prompt:
            return json.dumps({"stage": "benchmark",
                               "scores": {k: 8 for k in ["opening_hook", "payoff_density", "agency",
                                                         "memory_anchor", "chapter_end_hook", "logic_closure",
                                                         "voice_freshness", "emotion_curve"]},
                               "total": 8.0, "gaps": [], "verdict": "near"}, ensure_ascii=False)
        if "连续性审校员" in prompt:
            return json.dumps({"stage": "anti_drift", "conflicts": [], "drift_signals": [],
                               "pass": True}, ensure_ascii=False)
        if "把本章压成" in prompt:
            return json.dumps({"chapter_id": "?", "actors": ["林越"], "actions": ["签字"],
                               "conflicts": ["裁员"], "payoffs": ["权限复核"], "hook": "他知道自己该做什么",
                               "new_foreshadow": [], "closed_foreshadow": [],
                               "hard_data": {"time": "", "numbers": ["186"]}, "persona_state": "克制"},
                              ensure_ascii=False)
        return json.dumps({"pass": True, "score": 9, "issues": []}, ensure_ascii=False)


def check(cond: bool, name: str, out: dict):
    out[name] = "PASS" if cond else "FAIL"


async def main():
    cache.clear()
    llm.set_gateway(FakeGateway())
    results = {}

    out = {}
    for k, v in server.selfcheck().items():
        out[f"selfcheck:{k}"] = v
    results["selfcheck"] = out

    # 1) 单章三审：三审齐全 + 真实分数（非 stub）
    rep = await engine.review_chapter(CH2, "002", "qimao", FACTS, PERSONA)
    stages = set(rep["stages"])
    check({"first_review", "second_review", "third_review", "red_team", "benchmark"} <= stages,
          "三审+红队+对标齐全", results)
    check(not rep["hard_reject_short_circuit"], "正常章不短路", results)
    check(rep["overall"]["score"] > 0 and "桩" not in rep["overall"]["verdict"],
          "真实网关产出有效分数", results)

    # 2) 硬拒短路：致命红线命中，不调 LLM（stage 里只有 first_review）
    bad = CH1 + "\n他执行了 rm -rf /var/log/archive 命令。"
    rep2 = await engine.review_chapter(bad, "BAD", "qimao")
    check(rep2["hard_reject_short_circuit"] and set(rep2["stages"]) == {"first_review"},
          "L1致命红线短路不调LLM", results)

    # 3) 批次全量 + 增量：首次全量，改 003 后只 003 全文、002/004 摘要、其余复用
    chapters = [{"id": f"{i:03d}", "text": t} for i, t in enumerate([CH1, CH2, CH3, CH4, CH5], 1)]
    r1 = await engine.review_batch(chapters, "fanqie", FACTS, PERSONA)
    check(r1["summary"]["reviewed"] == 5 and r1["summary"]["rejected"] == 0,
          "首次全量5章评审", results)
    check(r1["anti_drift"]["fact_conflicts"] == [] and r1["anti_drift"]["llm"]["pass"] is True,
          "跨章反漂移通过", results)
    plan = engine.impact_radius(chapters, ["003"])
    check(plan["full"] == ["003"] and set(plan["summary_only"]) == {"002", "004"}
          and plan["reuse"] == ["001", "005"], "影响半径增量计算", results)

    # 4) 复审铁律：改动 >=10 处触发复审
    r2 = await engine.review_batch(chapters, "fanqie", FACTS, PERSONA, revised_ids=[f"{i:03d}" for i in range(1, 11)])
    check(r2["re_review_triggered"] is True, "改动>=10处触发复审", results)

    # 5) 缓存命中：第二次全量命中缓存
    r3 = await engine.review_batch(chapters, "fanqie", FACTS, PERSONA)
    check(r3["efficiency"]["cache_hits"] > 0, "hash缓存命中复用", results)

    print(json.dumps(results, ensure_ascii=False, indent=2))
    fails = [k for v in results.values() for k, val in (v.items() if isinstance(v, dict) else [])
             if val == "FAIL"]
    fails += [k for k, val in results.items() if val == "FAIL"]
    print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")


if __name__ == "__main__":
    asyncio.run(main())
