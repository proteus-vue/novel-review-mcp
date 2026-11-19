"""配置加载：规则/平台/对标/提示词，路径全部相对本文件，避免 cwd 坑。"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent

# 配置目录优先级：外部语言包 > 源码/可编辑安装 > wheel 的 <prefix>/xiezhi_review/
# 外部语言包用 XIEZHI_CONFIG_DIR 指向，可整包替换规则/平台/对标/提示词，无需改代码。
_env_cfg = os.environ.get("XIEZHI_CONFIG_DIR", "").strip()
_EXTERNAL_CONFIG_DIR = Path(_env_cfg).expanduser() if _env_cfg else None
_PREFIX_CONFIG_DIR = Path(sys.prefix) / "xiezhi_review"

if _EXTERNAL_CONFIG_DIR and (_EXTERNAL_CONFIG_DIR / "rules.yaml").exists():
    CONFIG_DIR = _EXTERNAL_CONFIG_DIR
elif (BASE_DIR / "rules.yaml").exists():
    CONFIG_DIR = BASE_DIR
else:
    CONFIG_DIR = _PREFIX_CONFIG_DIR
if not (CONFIG_DIR / "rules.yaml").exists():
    raise FileNotFoundError(
        f"缺少配置文件目录: {CONFIG_DIR}（源码运行请保留 src/*.yaml；"
        "wheel 安装请重新安装；语言包请设置 XIEZHI_CONFIG_DIR）"
    )

# 缓存落点：外部语言包 → 语言包目录；源码 → src/.review_cache；
# wheel → 用户目录（site-packages 可能只读），均可用 XIEZHI_CACHE_DIR 覆盖
if _EXTERNAL_CONFIG_DIR and CONFIG_DIR == _EXTERNAL_CONFIG_DIR:
    CACHE_ROOT = CONFIG_DIR
elif CONFIG_DIR == BASE_DIR:
    CACHE_ROOT = BASE_DIR
else:
    CACHE_ROOT = Path(os.environ.get("XIEZHI_CACHE_ROOT", str(Path.home() / ".xiezhi_review")))


def _load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"缺少配置文件: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


RULES = _load_yaml("rules.yaml")
PLATFORMS = _load_yaml("platforms.yaml")
BENCHMARKS = _load_yaml("benchmarks.yaml")

RULE_VERSION = str(RULES.get("version", "0.0.0"))


def load_prompts() -> dict[str, str]:
    """按 `## key` 拆分 prompts.md。key 与工具调用严格一致。"""
    text = (CONFIG_DIR / "prompts.md").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^##\s+([A-Za-z0-9_]+)\s*$", text)
    # parts = [preamble, key1, body1, key2, body2, ...]
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


PROMPTS = load_prompts()


def get_platform(name: str) -> dict:
    """返回 (平台档案, 平台键)。未知平台回退到通用默认，不空跑。"""
    key = (name or "").strip().lower()
    plats = PLATFORMS.get("platforms", {})
    if key in plats:
        return plats[key], key
    return {
        "name": f"自定义平台({name or '未指定'})",
        "model": "未知",
        "tone": "未提供公开标准，按通用网文标准评审",
        "focus": [],
        "review_process": ["初审", "复审", "终审"],
        "must_have": ["开篇有钩子", "爽点/回报密度达标", "逻辑闭合"],
        "forbidden": [],
        "note": "未匹配到预设平台，使用通用档案；请补充 platforms.yaml",
        "_fallback": True,
    }, key or "custom"


def get_benchmark_dimensions(platform: str | None = None) -> list[dict]:
    dims = [dict(d) for d in BENCHMARKS.get("dimensions", [])]
    overrides = BENCHMARKS.get("platform_overrides", {}).get(platform or "", {})
    for d in dims:
        ov = overrides.get(d["id"], {})
        d.update(ov)
    return dims


def render_prompt(key: str, **kwargs) -> str:
    if key not in PROMPTS:
        raise KeyError(f"prompts.md 缺少维度 [{key}]；禁止空跑，请补齐模板")
    tpl = PROMPTS[key]
    for k, v in kwargs.items():
        tpl = tpl.replace("{" + k + "}", str(v) if v is not None else "")
    return tpl


def efficiency_cfg() -> dict:
    return RULES.get("efficiency", {})


def rubric_cfg() -> dict:
    return RULES.get("rubric", {})


def revision_policy() -> dict:
    return RULES.get("revision_policy", {})


def dual_review_cfg() -> dict:
    """P2b 双评审配置：默认关闭；开启后只对配置中的 stage 做双路评审。"""
    return RULES.get("dual_review", {})
