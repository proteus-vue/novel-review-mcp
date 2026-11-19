# 獬豸 · 小说投稿评审 MCP（Xiezhi）

> **名字由来**：獬豸（xiè zhì）是中国古代神话中的独角神兽，能辨是非曲直，见人相争便触其无理者。它象征"明辨、刚正、一票否决"——正是这套工具要做的事：三审辨优劣、红队挑硬伤、闸门定放行。技术标识仍保留 `novel-review-mcp`，便于检索与兼容。

给 AI 生成的小说做投稿前严格评审的 MCP Server：三审流水线 + 红队挑刺 + 头部对标 + 反漂移，并用分层拦截与增量机制避免"每次评审都把一卷重读一遍"。

目标平台：七猫、番茄、起点、晋江、美团（占位模板），未知平台自动回退通用档。

> 平台档案是公开投稿/签约资料的整理，时效性强，**实际投稿以各平台官方最新公告为准**。美团档案为占位模板，填入 `focus/must_have/forbidden` 后启用。

## 它解决什么

| 诉求 | 机制 |
|---|---|
| 多平台过稿 | `platforms.yaml` 平台档案 + 黄金三章窗口（300/800/2000 字）+ 三审流程 |
| 后续不漂移 | 事实契约硬校验 + 摘要金字塔跨章检查 + 改动 ≥10 处自动复审 |
| 严格评审 | 合规定位（一票否决）→ 内容质量 → 批次终审，加独立红队与 8 维头部对标 |
| 评审不空跑 | L1/L2 毫秒级前置短路 + 影响半径增量 + hash 缓存 + 并行限流 |

**设计原则**：形态/结构判据优先于枚举黑名单；可复现技术细节用结构识别；词表扫不出叙述声音与逻辑断链，这类必须进 L3 语义评审。

## 安装

需要 Python 3.10+。

```bash
git clone <your-repo-url> novel-review-mcp
cd novel-review-mcp
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r src/requirements.txt
```

也可打包安装：`pip install .`，之后直接运行 `xiezhi-review --selfcheck`（兼容旧命令 `novel-review-mcp`）。wheel 安装的配置文件位于 `<venv>/xiezhi_review/`，缓存默认写到 `~/.xiezhi_review/`，可用 `XIEZHI_CACHE_DIR` / `XIEZHI_CACHE_ROOT` 覆盖。

## 接入 LLM（重要）

L3 语义评审必须由模型判断。未配置模型时 Server 使用**离线桩**，明确输出 `stub_no_judgement` / "未定"，**不会伪造通过率**。

```bash
export NOVEL_REVIEW_LLM_URL="https://your-endpoint/v1/chat/completions"
export NOVEL_REVIEW_LLM_KEY="your-api-key"
export NOVEL_REVIEW_LLM_MODEL="your-model"
```

网关是 OpenAI Chat Completions 兼容格式；也可在代码里 `llm.set_gateway(your_gateway)` 注入自定义实现（任意带 `async chat(prompt)->str` 的对象）。鉴权与端点细节见 `src/llm.py`。

## 运行

```bash
cd src
python server.py --selfcheck   # 自检：规则加载/红线命中/短路/平台回退
python demo.py                 # 5 章虚构样例跑完整报告（桩模式）
python test_pipeline.py        # 关键路径验证（假网关，9 项）
python server.py               # stdio 模式，供写稿 Agent 挂载
```

在客户端配置 stdio MCP server 时，command 指向 `python`，args 为 `[/绝对路径/src/server.py]`，cwd 设为 `src/`。

## MCP 工具（13 个）

| 工具 | 作用 |
|---|---|
| `check_redline` | L1 形态红线 + L2 关键词打分，毫秒级；致命项直接禁止发布 |
| `review_chapter` | 单章三审：一审合规定位（硬拒短路）→二审质量→三审终审→红队→对标 |
| `review_batch` | 批次评审：影响半径增量 + 摘要金字塔反漂移 + 复审铁律 |
| `check_consistency` | 跨章一致性：事实契约硬校验 + 摘要层 LLM 冲突发现 |
| `red_team` | 红队挑刺：逻辑漏洞/动机断裂/硬巧合/读者弃书点 |
| `benchmark` | 按结构密度 8 维对标头部，输出 gap |
| `platform_fit` | 平台收稿偏好适配度评分 |
| `check_opening` | 黄金开篇：前 300 字主角/冲突/悬念 + 800 字金手指 |
| `check_chapter_end` | 章末钩子判定与强度 |
| `impact_plan` | 查看影响半径（零成本，不调 LLM） |
| `cache_stats` / `cache_clear` | 缓存命中统计 / 清空 |
| `list_profiles` | 列出平台档案、规则版本、提示词维度 |

## 评分口径

`second_review.score` 与 `third_review.score` 参与加权，统一用**百分制 0–100**；与 `rules.yaml` 的 `rubric.verdict`（pass 80 / revise 70）和 `strict_gate`（单章 85 / 批次 90）同尺度。其余 stage 的 0–10 项为独立展示分，不参与聚合；`benchmark.total` 为 0–10 展示分，其 `verdict` 参与否决。详见 `src/prompts.md` 顶部说明。

## 配置

| 文件 | 作用 |
|---|---|
| `src/rules.yaml` | 红线/L2 打分/三审流水线/严格闸门/复审铁律/效率参数 |
| `src/platforms.yaml` | 平台档案（focus / must_have / forbidden / hard_window） |
| `src/benchmarks.yaml` | 8 维头部对标基线与阈值 |
| `src/prompts.md` | L3 各阶段提示词模板，按 `## key` 拆分，输出严格 JSON |

缓存 key 含规则版本；升级规则或提示词后建议调 `cache_clear`。缓存落盘在 `src/.review_cache/`，已加入 `.gitignore`。

## 事实契约（facts）

跨章硬数据约束由调用方以 `facts` 参数传入，结构：

```json
{"rules": [{"name": "规则名", "must_not": ["正则1", "正则2"]}]}
```

引擎做确定性正则校验。**注意边界**：契约只检查跨章"契约"，不检查单章内部自洽；内部逻辑矛盾（如时序倒错、数字自转不拢）只能靠 L3 语义层发现。

## 项目结构

```
novel-review-mcp/
├── src/
│   ├── server.py        # MCP 入口，13 个工具 + selfcheck
│   ├── engine.py        # 三审流水线 / 聚合判定 / 影响半径
│   ├── layers.py        # L1 形态红线 + L2 关键词打分
│   ├── summarizer.py    # 摘要金字塔
│   ├── llm.py           # 可插拔 LLM 网关
│   ├── cache.py         # hash 增量缓存
│   ├── config.py        # 配置加载（路径相对本文件，不依赖 cwd）
│   ├── demo.py / test_pipeline.py
│   └── *.yaml / prompts.md / requirements.txt
├── LICENSE / CHANGELOG.md / CONTRIBUTING.md
```

## 已知边界

1. 未接模型时 L3 明确"未定"，不伪造通过率。
2. 平台规则为公开资料整理，需自行核对官方最新口径。
3. `facts.json` 类契约只覆盖跨章硬数据，单章内部逻辑靠语义层。
4. 红队与对标的"否决"是策略判断，阈值可在 `rules.yaml` 调整。

## License

MIT — 见 `LICENSE`。
