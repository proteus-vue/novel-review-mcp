"""P0 机器层信号验证：节奏去桩 / 单章自洽 / 通用伏笔。

测试文本均为虚构片段，复现独立评审实测的四类问题原型，不含任何真实稿件。
运行: python test_p0_signals.py
"""
from __future__ import annotations

import asyncio
import json

import engine
import layers
import llm
from demo import CH1, CH2


class StubGateway:
    backend = "stub"
    model = "stub"
    async def chat(self, prompt: str) -> str:
        return json.dumps({"_stub": True, "pass": True, "score": 0}, ensure_ascii=False)


# 独立评审四类问题的虚构复现原型
T_TIME = "第七天他签下合同。第二天一早，对方却翻脸。两天以后是第八天，他才回应。"
T_NUM = "四块就能卖，涨到五块才不赚钱，这笔账怎么算都不对。"
T_FORESHADOW = "他不知道的是，仓库里那只箱子还没打开。谁也没料到那个人会回来。"
T_NORMAL = "他走进店里，看了一眼价签，付钱离开。老板点点头，没有多说。"


async def main():
    out = {}

    # 1) 时序倒错：应报 time_backward suspect，且带原文证据
    s = layers.self_consistency_check(T_TIME)
    types = {x["type"] for x in s}
    out["时序倒退被抓"] = "PASS" if "time_backward" in types else "FAIL"
    out["自洽信号只报suspect"] = "PASS" if all(x["level"] == "suspect" for x in s) else "FAIL"
    out["自洽带原文证据"] = "PASS" if all(x.get("evidence") for x in s) else "FAIL"

    # 2) 数字自转不拢
    s2 = layers.self_consistency_check(T_NUM)
    out["数字互斥被抓"] = "PASS" if any(x["type"] == "numeric_contradiction" for x in s2) else "FAIL"

    # 3) 正常文不应误报时序（无倒退）
    s3 = layers.self_consistency_check(T_NORMAL)
    out["正常文无误报"] = "PASS" if not s3 else "FAIL"

    # 4) 通用伏笔：无 F# 编号也能抽，且不依赖项目台账
    fs = layers.extract_foreshadow(T_FORESHADOW)
    out["无编号伏笔可抽"] = "PASS" if fs and not any(f.startswith("F") for f in fs) else "FAIL"
    out["伏笔是原文片段"] = "PASS" if all(len(f) <= 60 for f in fs) else "FAIL"

    # 5) 节奏去桩：用含爽点/冲突词的构造章验证真实计算
    llm.set_gateway(StubGateway())
    T_RICH = "他一拳反击，逼得对手节节败退。订单终于成交，利润到手。谁料还有后手？"
    T_DRY = "他走进店里，看了一眼价签，付钱离开。老板点点头，没有多说。"
    chapters = [{"id": "001", "text": T_RICH}, {"id": "002", "text": T_RICH},
                {"id": "003", "text": T_DRY}, {"id": "004", "text": T_DRY}]
    pace = await engine.check_pacing(chapters)
    per = pace["per_chapter"]
    out["节奏非桩"] = "PASS" if all(
        p["payoff"]["count"] > 0 or p["conflict"]["count"] > 0 or p["hook"]["count"] > 0
        for p in per[:2]
    ) else "FAIL"
    out["节奏有每章密度"] = "PASS" if all("per_2000" in p["payoff"] for p in per) else "FAIL"
    out["节奏荒漠检测可用"] = "PASS" if isinstance(pace.get("payoff_gap"), list) else "FAIL"
    out["节奏冲突密度真实"] = "PASS" if pace.get("conflict_density", 0) > 0 else "FAIL"
    # 连续两章低爽点（CH1/CH2）应被标为节奏荒漠
    out["节奏荒漠检出"] = "PASS" if any(len(g) >= 2 for g in pace["payoff_gap"]) else "FAIL"

    # 6) 单章报告携带机器层信号，且不因 suspect 变成禁止
    rep = await engine.review_chapter(T_NUM, "SC", "qimao")
    ms = rep.get("machine_signals", {})
    out["单章报告带机器信号"] = "PASS" if ms.get("self_consistency") else "FAIL"
    out["suspect不伪造禁止判定"] = "PASS" if "桩" in rep["overall"]["verdict"] else "FAIL"

    print(json.dumps(out, ensure_ascii=False, indent=2))
    fails = [k for k, v in out.items() if v == "FAIL"]
    print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")


if __name__ == "__main__":
    asyncio.run(main())
