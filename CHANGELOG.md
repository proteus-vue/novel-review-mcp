# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
