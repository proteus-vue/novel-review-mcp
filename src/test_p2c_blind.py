"""P2c 盲评回归验证：隔离输入包 / judgements 校验 / 回注 / 事后对照。

测试文本全为虚构片段，不含任何真实稿件或既有评审结论。
运行: python test_p2c_blind.py
"""
from __future__ import annotations

import asyncio
import json

import blind
import cache
import llm

CHAPTERS = [
    {"id": "001", "text": "他推开门，看到桌上放着一份合同。对方点头，他签下名字。谁料这只是开始。"},
]


class SentinelGateway:
    backend = "sentinel"
    model = "sentinel-1"

    async def chat(self, prompt: str) -> str:
        return json.dumps({"pass": True, "score": 88}, ensure_ascii=False)


def make_judgements(cid: str = "001", score: float = 88.0, blocking: bool = False) -> dict:
    return {
        cid: {
            "first_review": {"stage": "first_review", "pass": True, "verdict": "pass",
                             "redline_hits": [], "issues": []},
            "second_review": {"stage": "second_review", "score": score,
                              "issues": []},
            "third_review": {"stage": "third_review", "score": score,
                             "issues": []},
            "red_team": {"stage": "red_team", "blocking": blocking,
                         "attack_points": [],
                         "verdict": "reject" if blocking else "accept"},
            "benchmark": {"stage": "benchmark", "total": 8.5, "gaps": [], "verdict": "near"},
        },
        "summaries": {
            cid: {"chapter_id": cid, "actors": ["他"], "actions": ["签字"],
                  "conflicts": ["对峙"], "payoffs": ["签约"], "hook": "只是开始",
                  "new_foreshadow": [], "closed_foreshadow": [],
                  "hard_data": {"time": "", "numbers": []}, "persona_state": ""},
        },
        "anti_drift": {"stage": "anti_drift", "conflicts": [], "drift_signals": [], "pass": True},
    }


async def main() -> None:
    out: dict[str, str] = {}

    # 1) 输入包隔离：评审字段不得进入盲评材料
    package = blind.build_blind_package(
        [{"id": "001", "text": CHAPTERS[0]["text"], "score": 99, "verdict": "放行", "overall": {}}]
    )
    clean = package["chapters"][0]
    out["盲评包剔除评审字段"] = "PASS" if (
        not package["isolation"]["safe"]
        and not ({"score", "verdict", "overall"} & set(clean))
        and "prompts" in package
    ) else "FAIL"

    # 2) judgements 校验：缺 stage 判无效；0-10 分数只警告不换算
    invalid = blind.validate_judgements(CHAPTERS, {"001": {}}, strict=True)
    out["缺stage判无效"] = "PASS" if not invalid["valid"] else "FAIL"

    wrong_scale = make_judgements(score=8.5)
    scaled = blind.validate_judgements(CHAPTERS, wrong_scale, strict=True)
    out["分数口径错配告警"] = "PASS" if (
        scaled["valid"]
        and any(w["code"] == "score_scale_suspect" for w in scaled["warnings"])
    ) else "FAIL"

    valid = blind.validate_judgements(CHAPTERS, make_judgements(), strict=True)
    out["合法judgements通过"] = "PASS" if valid["valid"] else "FAIL"

    # 3) 回注网关：stage/章节命中；找不到章节时明确报错，不套用别的章
    judgements = make_judgements(blocking=True)
    gateway = blind.BlindGateway(judgements, CHAPTERS)
    prompt = "你是二审编辑，只看内容质量。文本：" + CHAPTERS[0]["text"]
    parsed = json.loads(await gateway.chat(prompt))
    out["回注命中章节与阶段"] = "PASS" if (
        parsed.get("stage") == "second_review"
        and parsed.get("_blind_chapter_id") == "001"
        and gateway.audit["hits"] == 1
    ) else "FAIL"
    unknown = json.loads(await gateway.chat("一段完全无关的提示词"))
    out["未匹配不静默套用"] = "PASS" if unknown.get("_blind_lookup_error") == "chapter_unmatched" else "FAIL"

    # 4) 完整盲评：红队否决进闸门；P2a 账本仍在；网关与缓存不污染
    cache.clear()
    previous = llm.get_gateway()
    llm.set_gateway(SentinelGateway())
    sentinel = llm._gateway
    report = await blind.blind_review(CHAPTERS, judgements, platform="qimao")
    overall = report["chapters"]["001"]["overall"]
    out["盲评否决进闸门"] = "PASS" if (
        report.get("ok") is True
        and "红队否决" in overall["vetoes"]
        and overall["verdict"].startswith("禁止发布")
    ) else "FAIL"
    out["盲评附校验与审计"] = "PASS" if (
        report["blind"]["validation"]["valid"]
        and report["blind"]["audit"]["hits"] > 0
    ) else "FAIL"
    out["盲评保留P2a账本"] = "PASS" if report["telemetry"]["llm_calls"] > 0 else "FAIL"
    out["恢复原网关"] = "PASS" if llm._gateway is sentinel else "FAIL"
    out["盲评后缓存清空"] = "PASS" if not cache._load() else "FAIL"
    llm._gateway = previous

    # 5) 事后对照：分数差、判定翻转、否决增减
    original = {
        "summary": {"verdict": "放行"},
        "chapters": {"001": {"overall": {"score": 86, "verdict": "放行", "vetoes": []}}},
    }
    blind_report = {
        "summary": {"verdict": "禁止发布（未达严格档闸门）"},
        "chapters": {"001": {"overall": {
            "score": 70, "verdict": "禁止发布（红队否决）", "vetoes": ["红队否决"],
        }}},
    }
    comparison = blind.blind_compare(original, blind_report)
    row = comparison["rows"][0]
    out["对照分数差"] = "PASS" if row["score_delta"] == -16 else "FAIL"
    out["对照判定翻转"] = "PASS" if (
        row["verdict_flip"] and comparison["summary"]["verdict_flip_chapters"] == ["001"]
    ) else "FAIL"
    out["对照否决新增"] = "PASS" if row["vetoes_added"] == ["红队否决"] else "FAIL"

    print(json.dumps(out, ensure_ascii=False, indent=2))
    fails = [k for k, v in out.items() if v == "FAIL"]
    print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")


if __name__ == "__main__":
    asyncio.run(main())
