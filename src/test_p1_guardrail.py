"""P1 闭环验证：契约自动生成 / 伏笔台账 / 评审回流约束块。

测试文本均为虚构片段，不含任何真实稿件。零 LLM。
运行: python test_p1_guardrail.py
"""
from __future__ import annotations

import json
from pathlib import Path

import facts
import layers

# 虚构片段：同单位多值（320单/290单）、泛指时间（一天）、伏笔句
CH_A = "店里一天三百二十单，客单价十六元。他不知道的是，箱子还没打开。"
CH_B = "一天二百九十单，成本四块。谁也没料到那个人回来。"


def main() -> dict:
    out = {}
    reg = Path(facts.REGISTRY_FILE)
    if reg.exists():
        reg.unlink()

    # 1) 契约生成：fact_check 兼容，零 LLM
    e = facts.extract_facts([{"id": "A", "text": CH_A}, {"id": "B", "text": CH_B}])
    out["契约结构兼容fact_check"] = "PASS" if all(
        isinstance(r.get("must_not"), list) and isinstance(r.get("must"), list)
        for r in e["contract"]["rules"]) else "FAIL"
    out["契约标注自动生成"] = "PASS" if e["contract"].get("_auto_generated") else "FAIL"
    out["锚点带原文证据"] = "PASS" if all(x.get("evidence") for x in e["anchors"]) else "FAIL"

    # 2) 跨章同单位多值 = suspect 候选，不冒充确认冲突
    c = [x for x in e["conflicts"] if x["unit"] == "单"]
    out["跨章同单位多值被抓"] = "PASS" if c and 320 in c[0]["values"] and 290 in c[0]["values"] else "FAIL"
    out["冲突只标suspect"] = "PASS" if all(x["level"] == "suspect" for x in e["conflicts"]) else "FAIL"
    # 闭环核心：用稳定章生成契约，验证能拦住漂移章、且不误报规范写法
    stable = facts.extract_facts([
        {"id": "S1", "text": "店里一天三百二十单，客单价十六元。"},
        {"id": "S2", "text": "一天320单，客单价16元。"},
    ])["contract"]
    cn_ok = not layers.fact_check(
        [{"id": "X", "text": "一天三百二十单，客单价十六元。"}], stable)
    ar_ok = not layers.fact_check(
        [{"id": "X", "text": "一天320单，客单价16元。"}], stable)
    drift = layers.fact_check([{"id": "X", "text": "一天290单，客单价16元。"}], stable)
    out["中文规范写法不误报"] = "PASS" if cn_ok else "FAIL"
    out["阿语规范写法不误报"] = "PASS" if ar_ok else "FAIL"
    out["契约拦住漂移章"] = "PASS" if any(h["evidence"].startswith("290") for h in drift) else "FAIL"

    # 3) 泛指时间不过滤误抽：文本里"一天"不应成为硬数据锚点
    out["泛指时间不误抽"] = "PASS" if all("一天" not in a["raw"] for a in e["anchors"]) else "FAIL"
    out["中文数字转换正确"] = "PASS" if facts._cn_to_int("三百二十") == 320 and \
        facts._cn_to_int("二万三千") == 23000 else "FAIL"

    # 4) 伏笔台账：登记去重 / 回收 / 超期
    r1 = facts.foreshadow_register("A", ["箱子还没打开", "箱子还没打开"], 0)
    out["台账登记去重"] = "PASS" if r1["added"] == 1 and r1["existing"] == 1 else "FAIL"
    out["台账落盘可读"] = "PASS" if len(facts.foreshadow_list("open")["items"]) == 1 else "FAIL"
    r2 = facts.foreshadow_close("B", ["箱子"], "他打开了箱子")
    out["台账短文本回收"] = "PASS" if len(r2["closed"]) == 1 else "FAIL"
    facts.foreshadow_register("C", ["那个人会回来"], 1)
    od = facts.foreshadow_overdue(12)
    out["超期未收可提醒"] = "PASS" if len(od["overdue"]) == 1 else "FAIL"
    out["无章序不臆断超期"] = "PASS" if not facts.foreshadow_overdue(None)["overdue"] else "FAIL"

    # 5) 评审回流：issues/attack_points/veto 转成下一章约束，含人设锚与超期伏笔
    review = {"chapters": {"A": {
        "chapter_id": "A",
        "stages": {
            "second_review": {"issues": [{"level": "mid", "evidence": "会议没决策",
                                            "suggestion": "写成有决策的戏"}]},
            "red_team": {"attack_points": [{"level": "high", "evidence": "整章无戏",
                                             "suggestion": "加主动冲突"}]},
        },
        "overall": {"verdict": "禁止发布", "vetoes": ["红队否决"]},
    }}}
    b = facts.next_chapter_brief(review, chapter_id="D", persona="克制、算账",
                                 current_chapter_index=12)
    srcs = {x["source"] for x in b["must_avoid"]}
    out["评审问题回流"] = "PASS" if {"red_team", "second_review", "veto"} <= srcs else "FAIL"
    out["人设锚回流"] = "PASS" if "克制" in "".join(b["persona_anchors"]) else "FAIL"
    out["超期伏笔进约束"] = "PASS" if b["overdue_count"] >= 1 and b["foreshadow_focus"] else "FAIL"
    out["约束块含prompt"] = "PASS" if "写作约束" in b["prompt"] and "必须规避" in b["prompt"] else "FAIL"

    # 6) 桩模式评审不伪造语义结论：约束块明确标注
    stub_review = {"chapters": {"A": {"chapter_id": "A", "stages": {
        "second_review": {"_stub": True}}, "overall": {"verdict": "未定（L3 桩模式）"}}}}
    sb = facts.next_chapter_brief(stub_review, chapter_id="E")
    out["桩模式明确标注"] = "PASS" if sb["stub_source"] and "桩模式" in sb["prompt"] else "FAIL"

    if reg.exists():
        reg.unlink()
    return out


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    fails = [k for k, v in result.items() if v == "FAIL"]
    print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")
