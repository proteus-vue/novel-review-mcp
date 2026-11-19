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
| 效率可核对 | 真实调用数/耗时/token 账本（`timing` / `telemetry`），估算字段保留兼容 |
| 评审员偏差 | P2b 双评审（红队+对标）+ P2c 独立盲评，分歧升级人工、不自动放行 |

**设计原则**：形态/结构判据优先于枚举黑名单；可复现技术细节用结构识别；词表扫不出叙述声音与逻辑断链，这类必须进 L3 语义评审。

## 安装

需要 Python 3.10+。代码里没有平台专属分支，路径全部走 `pathlib`，Windows / macOS / Linux 同一套源码。

```bash
git clone git@github.com:proteus-vue/novel-review-mcp.git
cd novel-review-mcp
```

创建虚拟环境：

```bash
# macOS / Linux
python3 -m venv .venv && source .venv/bin/activate

# Windows PowerShell
py -3 -m venv .venv && .\.venv\Scripts\Activate.ps1

# Windows CMD
py -3 -m venv .venv && .venv\Scripts\activate.bat
```

安装依赖：

```bash
pip install -r src/requirements.txt
# 或打包安装（推荐给非 Python 客户端）
pip install .
```

`pip install .` 后会生成 `xiezhi-review` 命令（Windows 为 `xiezhi-review.exe`），旧命令 `novel-review-mcp` 保留兼容。wheel 安装的配置文件位于 `<venv>/xiezhi_review/`，缓存默认写到 `~/.xiezhi_review/`，可用 `XIEZHI_CACHE_DIR` / `XIEZHI_CACHE_ROOT` 覆盖。

## 多语言

**调用侧不限语言**：Server 走 MCP stdio（JSON-RPC），Node.js / Go / Rust / Java 等任何能启动子进程的客户端都能挂载；返回是纯 JSON，与客户端语言无关。

**评审内容目前是中文网文规则**：`rules.yaml` / `prompts.md` / `platforms.yaml` 面向七猫 / 番茄 / 起点 / 晋江。要评审英文等其它语种，不需要改代码——另备一套同名配置文件（保持 stage key 与 JSON schema 一致），指向它即可：

```bash
# macOS / Linux
export XIEZHI_CONFIG_DIR=/path/to/my-language-pack

# Windows PowerShell
$env:XIEZHI_CONFIG_DIR = "C:\path\to\my-language-pack"
```

该目录需包含 `rules.yaml`、`platforms.yaml`、`benchmarks.yaml`、`prompts.md`；配置整体切换，缓存也落在语言包目录。缺文件会明确报错，**不会静默回退到中文规则**。

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
python test_p2_telemetry.py    # P2a 真实调用/耗时/token 账本验证（12 项）
python test_p2c_blind.py       # P2c 盲评隔离/校验/回注/对照验证（14 项）
python test_p2b_dual.py        # P2b 双评审合并/分歧/一票否决验证（15 项）
python server.py               # stdio 模式，供写稿 Agent 挂载
```

在客户端配置 stdio MCP server 时，command 指向 `python`，args 为 `[/绝对路径/src/server.py]`，cwd 设为 `src/`。Windows 下 command 用 `python.exe` 绝对路径，args 形如 `["C:\\...\\novel-review-mcp\\src\\server.py"]`；若已 `pip install .`，也可直接用 `xiezhi-review` 命令，无需 cwd。环境变量用 `set`（CMD）或 `$env:NAME = "值"`（PowerShell）设置。

## MCP 工具（25 个）

| 工具 | 作用 |
|---|---|
| `check_redline` | L1 形态红线 + L2 关键词打分，毫秒级；致命项直接禁止发布 |
| `review_chapter` | 单章三审：一审合规定位（硬拒短路）→二审质量→三审终审→红队→对标 |
| `review_batch` | 批次评审：影响半径增量 + 摘要金字塔反漂移 + 复审铁律 |
| `check_consistency` | 跨章一致性：事实契约硬校验 + 摘要层 LLM 冲突发现 |
| `check_self_consistency` | **单章内部自洽**：时序倒退 / 数字互斥 / 称谓变体，零成本弱信号，只报 suspect + 原文证据 |
| `check_pacing` | **节奏检查**：爽点/冲突/钩子密度 + 节奏荒漠检测，机器层为主、LLM 仅跨章探针 |
| `red_team` | 红队挑刺：逻辑漏洞/动机断裂/硬巧合/读者弃书点 |
| `benchmark` | 按结构密度 8 维对标头部，输出 gap |
| `platform_fit` | 平台收稿偏好适配度评分 |
| `check_opening` | 黄金开篇：前 300 字主角/冲突/悬念 + 800 字金手指 |
| `check_chapter_end` | 章末钩子判定与强度 |
| `impact_plan` | 查看影响半径（零成本，不调 LLM） |
| `extract_facts` | **P1 闭环**：从正文抽硬数据生成候选事实契约（`fact_check` 兼容），含跨章同单位多值 suspect；零 LLM，候选需复核 |
| `foreshadow_register` / `foreshadow_close` | 伏笔台账登记（去重）/ 回收，持久化落盘 |
| `foreshadow_list` / `foreshadow_overdue` | 查看台账 / 超期未收提醒（无章序不臆断） |
| `next_chapter_brief` | **评审回流**：把上轮评审结论 + 未收伏笔 + 人设锚转成下一章写作约束块，含可直接喂写稿 Agent 的 `prompt` |
| `cache_stats` / `cache_clear` | 缓存命中统计 / 清空 |
| `blind_review_package` / `blind_validate_judgements` | **P2c 盲评**：生成隔离输入包 / 校验外部 judgements |
| `blind_review` / `blind_compare` | 回注独立盲评跑完整流水线 / 事后对照分数、判定、否决差异 |
| `list_profiles` | 列出平台档案、规则版本、提示词维度 |

## AI 写小说闭环（P1）

评审不该只是事后体检，而要在写下一章前变成护栏。推荐这样串：

```
写完 N 章 → review_batch（三审+红队+对标）→ 评审报告
         → extract_facts(N章) 得候选契约（人工/LLM 复核后固化）
         → foreshadow_register/close 维护伏笔台账
         → next_chapter_brief(报告, 契约, 人设) 得第 N+1 章约束块
         → 把约束块的 prompt 喂给写稿 Agent → 写完再评审（闭环）
```

- `extract_facts` 用结构化 `value_set`（单位 + 允许数值集合），中文/阿拉伯数字通吃；同单位未必同量，所以一律标 `suspect` 候选，**复核后才能当正式契约**，不冒充确认结论。
- `next_chapter_brief` 是评审结论的确定性转写，不新增判断；上轮是桩模式时会在 prompt 里明确标注，避免拿空判定当约束。
- 伏笔台账落盘到缓存根（wheel 只读环境安全），`XIEZHI_CACHE_DIR` 可覆盖。

## 效率观测（P2a）

`review_chapter` / `review_batch` 的返回报告新增 `timing` 与 `telemetry`，把"评审快"从估算变成可核对的事实：

| 字段 | 含义 |
|---|---|
| `timing.wall_sec` | 本次调用真实墙钟耗时 |
| `timing.phases` | 批次各阶段墙钟：L1/L2、单章评审、摘要、跨章、聚合 |
| `telemetry.llm_calls` / `efficiency.llm_calls_actual` | 真实 LLM 调用次数，非估算 |
| `telemetry.llm_cumulative_sec` / `llm_queue_sec` | 模型累计耗时 / 限流排队耗时 |
| `telemetry.tokens` | `prompt` / `completion` / `total` / `available` |
| `telemetry.stages` | 按 stage 拆分的 calls / 耗时 / token |

- `efficiency.llm_calls_estimate` 保留旧字段兼容；对外结论以 `llm_calls_actual` 为准。
- 并发限流下 `llm_cumulative_sec` 可能大于 `wall_sec`（多次调用并行），这不是错误；`timing.phases` 才是墙钟拆分。
- 自定义网关只实现 `async chat(prompt)` 时仍可用，但没有 usage；此时 `tokens.available=false`，不会填 0 冒充真实消耗。OpenAI 兼容端点返回标准 `usage` 会自动入账。
- 硬拒短路、缓存复用都会如实反映为更少的 `llm_calls_actual`；评分与闸门完全不受这些新字段影响。

## 双评审（P2b，默认关闭）

单评审员可能被上下文锚定。P2b 对**红队 + 对标**两路各跑一次评审再合并，其余阶段仍单评审以控制成本。默认关闭，需显式开启：

```yaml
# src/rules.yaml
dual_review:
  enabled: true
  stages: [red_team, benchmark]
  disagreement_gap: 10
```

第二评审员默认复用主网关（同一模型换视角），报告会标 `committee.independent=false`——**这不是独立投票**。想要真正的独立判断，必须配第二后端：

```bash
export NOVEL_REVIEW_LLM2_URL="https://second-endpoint/v1/chat/completions"
export NOVEL_REVIEW_LLM2_KEY="your-api-key"
export NOVEL_REVIEW_LLM2_MODEL="your-second-model"
# 或在代码里 llm.set_secondary_gateway(your_gateway)
```

合并规则（见 `overall.committee` 与 `strict_gate.disagreement_chapters`）：

| 规则 | 行为 |
|---|---|
| 一票否决 | 任一评审 blocking / reject / below 即成立，**不可被多数票推翻** |
| 分数 | 取两路中位数，不算出虚假的"共识分" |
| 分歧 | 判定语义冲突或归一百分制分差 ≥ `disagreement_gap` → `disagreement=true` |
| 自动放行 | 有分歧时禁止自动放行，单章/批次都标"双评审分歧待人工复核" |
| 成本 | 开启后红队+对标各多 1 次调用（单章 5→7），真实反映在 `telemetry.llm_calls` |

- 默认关闭时行为、判定口径、调用次数与旧版**完全一致**，升级无感。
- 同模型双开只提供"多视角"，不能替代不同模型的独立判断；要更彻底地解决锚定请配合 P2c 盲评。

## 盲评回归（P2c）

独立盲评解决的是"评审员被既有报告锚定"：把判定交给外部评审员时，只给正文和提示词，不给任何旧分数、报告或人工结论。

推荐流程：

1. `blind_review_package(chapters, platform, persona?, facts?)` → 得到只含正文 + 提示词的隔离包，先确认 `isolation.safe=true`。
2. 把隔离包交给独立评审员 / 子代理，按 `prompts.md` 输出 `judgements`。
3. `blind_validate_judgements(chapters, judgements)` → 先校验结构；`valid=false` 不跑引擎。
4. `blind_review(chapters, judgements, ...)` → 回注完整三审流水线，附 `blind.validation` 与 `blind.audit`；运行前后清缓存并恢复网关，避免盲评结果污染普通评审。
5. `blind_compare(original_report, blind_report)` → 输出分数差、判定翻转、否决增减；**只呈现差异，不自动改写任何报告**。

`judgements` 最小结构：

```json
{
  "<chapter_id>": {
    "first_review": {"pass": true, "verdict": "pass", "redline_hits": [], "issues": []},
    "second_review": {"score": 85, "issues": []},
    "third_review": {"score": 86, "issues": []},
    "red_team": {"blocking": false, "attack_points": [], "verdict": "accept"},
    "benchmark": {"total": 8.5, "gaps": [], "verdict": "near"}
  },
  "summaries": {
    "<chapter_id>": {"chapter_id": "<chapter_id>", "actors": [], "actions": [], "conflicts": [],
      "payoffs": [], "hook": "", "new_foreshadow": [], "closed_foreshadow": [],
      "hard_data": {"time": "", "numbers": []}, "persona_state": ""}
  },
  "anti_drift": {"conflicts": [], "drift_signals": [], "pass": true}
}
```

- `second_review.score` / `third_review.score` 必须是百分制 0–100。传 0–10 时校验只告警 `score_scale_suspect`，**不自动换算**。
- 回注网关按正文片段定位章节；定位不到或有歧义会记入 `blind.audit`，绝不静默套用别的章。
- 盲评不是"自动采信"，而是"独立判断 + 事后对照"。冲突项必须回正文逐字核对；P2a 的 `telemetry` 在盲评报告中同样可用。

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

引擎做确定性正则校验。

**单章内部自洽**（P0 新增）：`check_self_consistency` 提供确定性弱信号——时序倒退、数字互斥、称谓变体，只报 `suspect` + 原文证据。**注意边界**：这是弱信号，不判 fatal/reject，最终仍需 L3 语义层或人工复核；`facts` 契约仍只管跨章硬数据。

## 项目结构

```
novel-review-mcp/
├── src/
│   ├── server.py        # MCP 入口，25 个工具 + selfcheck
│   ├── engine.py        # 三审流水线 / 聚合判定 / 影响半径
│   ├── layers.py        # L1 红线 + L2 打分 + P0 机器层信号 + value_set 契约校验
│   ├── facts.py         # P1 闭环：契约抽取 / 伏笔台账 / 评审回流约束块
│   ├── summarizer.py    # 摘要金字塔（伏笔通用抽取）
│   ├── llm.py           # 可插拔 LLM 网关 + latency/usage 采集
│   ├── telemetry.py     # P2a 真实调用/耗时/token 账本（contextvars 并发安全）
│   ├── committee.py     # P2b 双评审合并 / 分歧判定（纯函数）
│   ├── blind.py         # P2c 盲评隔离包 / judgements 校验回注 / 事后对照
│   ├── cache.py         # hash 增量缓存
│   ├── config.py        # 配置加载（路径相对本文件，不依赖 cwd）
│   ├── demo.py / test_pipeline.py / test_p0_signals.py / test_p1_guardrail.py / test_p2_telemetry.py / test_p2b_dual.py / test_p2c_blind.py
│   └── *.yaml / prompts.md / requirements.txt
├── LICENSE / CHANGELOG.md / CONTRIBUTING.md
```

## 已知边界

1. 未接模型时 L3 明确"未定"，不伪造通过率。
2. 平台规则为公开资料整理，需自行核对官方最新口径。
3. `facts.json` 类契约只覆盖跨章硬数据，单章内部逻辑靠语义层。
4. 红队与对标的"否决"是策略判断，阈值可在 `rules.yaml` 调整。
5. 成本账本依赖网关返回 usage；自定义网关未提供时只标 `tokens.available=false`，不猜 token 数。
6. 盲评包只能剔除工具可见的评审字段，无法约束外部评审员进程是否偷偷读了别的文件；真正隔离需由调用方保证。
7. P2b 同网关双开只是多视角，不是独立投票；独立判断需配第二模型后端，且即便如此也只是"双评审"而非真实编辑部。

## License

MIT — 见 `LICENSE`。
