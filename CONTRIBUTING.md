# Contributing

欢迎贡献规则、平台档案、测试用例与文档。

## 开发环境

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r src/requirements.txt
pip install pytest
cd src && python test_pipeline.py
```

## 修改约定

1. **规则调整改 `src/rules.yaml`**，提示词改 `src/prompts.md`，不要在引擎里硬编码阈值。
2. **评分口径**：参与加权的 `score` 一律百分制（0–100）；新增 stage 若参与聚合，必须同步更新 `rules.yaml` 的 `rubric.dimensions` 与 `prompts.md` 顶部口径说明。
3. **新增平台**在 `src/platforms.yaml` 加档案（`focus` / `must_have` / `forbidden` / `hard_window`）；无公开标准时用占位并注明，引擎不会因空配置空跑。
4. **新增配置文件**必须在 `src/config.py` 的 `BASE_DIR` 下加载，保持相对路径，不依赖 cwd。
5. **独立视角信号**（红队 blocking/reject、对标 below）如新增否决类型，必须进入 `engine.review_chapter` 的 `vetoes`，避免被批次均分稀释。

## 提交前检查

```bash
cd src
python server.py --selfcheck
python test_pipeline.py    # 9 项关键路径全 PASS
python demo.py             # 桩模式，确认判定为"未定"
```

## 隐私

本项目是通用评审工具。**请勿把任何真实稿件、内部项目路径或私有评审结果提交进来**；示例数据用 `demo.py` 中的虚构片段。评审缓存（`.review_cache/`）与报告 JSON 已在 `.gitignore` 中排除。

## 平台规则

平台档案来自公开资料整理，需注明来源与时效；投稿标准以各平台官方公告为准。
