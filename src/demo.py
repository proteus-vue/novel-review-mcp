"""Demo：5 章样例跑完整报告（离线桩即可运行，不伪造通过率）。

运行:  python demo.py
"""
from __future__ import annotations

import asyncio
import json

import engine

CH1 = """林越签完字，把笔帽扣上。裁员的名单里，他的名字排在第十七个。
房贷还有一百八十六个月。他算了一下，够他在出租屋里躺三个月。
手机震了一下，是一条内部邮件：权限复核已通过，OP-773。
他盯着那串字符看了两秒，关掉屏幕。窗外有人在笑，他没抬头。
他知道自己该做什么。他打开电脑，把那张遗留的审批流程重新走了一遍。"""

CH2 = """第二天上午，周莉把一份表推到他面前。
"七楼的账对不上。"她说。
林越接过表，扫了一眼数字。他没有解释，只是问了一句："要多久？"
"最迟周五。"她把笔放下，"运营那边已经在问了。"
他点头，把表放到键盘旁边。屏幕上是那条审批流程，还差最后一步。"""

CH3 = """周五下午，七楼的账平了。
没有人知道是谁改的。周莉把结果发进群里，有人回了个"收到"。
林越端起保温杯，喝了一口水。杯子上印着二〇一五年年会的字样。
女儿朵朵五岁，晚上会问他今天是不是又加班。他说快了。
他关掉电脑，走出办公室。走廊的灯一层一层暗下去。"""

CH4 = """周一早上，部门群里多了一条通知：流程优化，权限回收。
有人开始抱怨，有人沉默。林越坐在工位上，看完通知，没有说话。
他把旧流程的最后一张截图存进一个文件夹，命名为一个日期。
这是一切开始的地方，他知道。"""

CH5 = """中午，周莉在电梯口拦住他。
"那份表，"她说，"是谁批的？"
林越看着电梯门合上。"系统批的。"
她点了点头，没有再问。电梯往下走，数字一层一层跳。
他走出大楼，阳光很亮。他眯了眯眼，往地铁站走。"""


FACTS = {
    "rules": [
        {
            "name": "主角编号唯一",
            "must_not": [r"赵工.{0,6}(?:是|为)005", r"001.{0,4}赵工"],
            "note": "曾潜伏 20 章的旧事故：001 被误写成赵工",
        },
        {
            "name": "保温杯年份",
            "must_not": [r"保温杯.{0,12}201[6-9]"],
            "note": "距第 40 章 39 章才被人工抓到，已机器化",
        },
        {
            "name": "朵朵年龄",
            "must_not": [r"朵朵.{0,6}[3-4]岁"],
            "note": "阿拉伯数字形态曾是盲区",
        },
    ]
}

PERSONA = "林越全程克制：不解释、不怒吼、不事后炫耀；反击走流程/邮件/日志；能力使用必留痕有代价。"


async def main():
    chapters = [
        {"id": f"{i:03d}", "text": t}
        for i, t in enumerate([CH1, CH2, CH3, CH4, CH5], 1)
    ]
    print("=== 影响半径（首次运行，无改动清单 → 全量首评）===")
    print(json.dumps(engine.impact_radius(chapters, None), ensure_ascii=False, indent=2))

    print("\n=== 批次评审报告 ===")
    report = await engine.review_batch(
        chapters,
        platform="fanqie",
        facts=FACTS,
        persona=PERSONA,
        revised_ids=None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2)[:6000])

    print("\n=== 增量复评：只改第 003 章 ===")
    print(json.dumps(engine.impact_radius(chapters, ["003"]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
