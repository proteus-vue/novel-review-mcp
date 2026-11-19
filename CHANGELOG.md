# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- P1 闭环能力（评审从体检变写稿护栏，全部零 LLM）：
  - `extract_facts`：从正文抽硬数据，生成结构化 `value_set` 候选契约（单位+允许数值，中文/阿拉伯数字通吃）；跨章同单位多值标 suspect。`layers.fact_check` 支持 value_set 校验。修复 P0 遗留的中文数字转换分支顺序 bug（"三百二十"曾得 0）。
  - 伏笔台账：`foreshadow_register/close/list/overdue` 五个工具，去重登记、文本匹配回收、超期提醒；落盘到缓存根（wheel 只读安全），无章序信息不臆断超期。
  - `next_chapter_brief`：把评审 issues/红队攻击点/否决信号 + 未收伏笔 + 人设锚回流成下一章写作约束块，含可直接喂写稿 Agent 的 prompt；桩模式明确标注。
  - `test_p1_guardrail.py` 20 项测试，含"契约拦漂移且不误报规范写法"的闭环核心验证。
- P0 机器层能力（AI 写小说场景）：
  - `check_self_consistency`：单章内部自洽弱信号，检测时序倒退、数字互斥、称谓变体；只报 suspect + 原文证据，不伪造 fatal/reject。原型来自独立评审实测的四类真问题。
  - `check_pacing` 去桩：爽点/冲突/钩子密度为确定性计算，含节奏荒漠检测；短章用原始计数避免密度外推失真。
  - 伏笔抽取通用化：`_fallback_summary` 去掉 F# 编号假设，用悬念句/未完成事件识别，无编号也能抽。
  - 单章报告新增 `machine_signals` 字段，`server.selfcheck` 增加三项 P0 自检。
- 跨语言扩展点：`XIEZHI_CONFIG_DIR` 指向外部配置目录，可整包替换规则/平台/对标/提示词（语言包），不改代码；缓存随语言包落盘。
- CI 矩阵：Ubuntu / macOS / Windows × Python 3.10 / 3.12，覆盖源码自检、关键路径测试、demo 与 wheel 安装后控制台命令。

### Changed
- 品牌命名：中文名定为「獬豸」（Xiezhi），取神兽明辨是非、触退无理者之意，对应三审/红队/闸门一票否决；包名与 MCP Server 标识改为 `xiezhi-review`，旧 `novel-review-mcp` 控制台命令保留兼容。
- 修复 wheel 安装：配置文件改为同时支持源码目录与 `<prefix>/xiezhi_review/`，缓存从只读 site-packages 迁到用户目录（`XIEZHI_CACHE_DIR` 可覆盖）；`pip install .` 后控制台命令可直接运行。
- README 补 Windows / macOS / Linux 安装与 MCP 客户端配置差异。

## [0.3.0] - 2026-11-19

### Added
- 三审流水线：合规定位（一票否决）→ 内容质量 → 批次终审。
- 独立红队挑刺与 8 维头部对标，二者拥有硬否决权。
- 反漂移三层：事实契约硬校验、摘要金字塔跨章检查、改动 ≥10 处复审铁律。
- 效率机制：L1/L2 毫秒级前置短路、影响半径增量、hash 缓存、并行限流。
- 13 个 MCP 工具与 `server.py --selfcheck`。
- 多平台档案：七猫/番茄/起点/晋江/美团（占位），未知平台通用回退。

### Changed
- 统一评分口径：`second_review` / `third_review` 的 `score` 与闸门同为百分制（0–100），
  修正此前模板 0–10 与闸门 85/90 错配导致的误判。
- MCP Server 正式兼容 1.x 与 2.x，`requirements.txt` 不再钉 `<2`。

### Notes
- 未配置 LLM 时 L3 明确输出"未定"，不伪造通过率。
- 平台规则为公开资料整理，以官方最新公告为准。
