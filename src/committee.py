"""P2b 双评审合并：纯函数，负责两路评审结果的合并与分歧判定。

设计铁律：
  1) 一票否决不可被多数票覆盖——红队 blocking/reject、对标 below 任一成立即成立；
  2) 分数取中位数，绝不简单平均出一个虚假的"共识分"；
  3) 分歧（语义冲突或分差超阈值）显式标记，交给上层阻断自动放行；
  4) 同网关同模型只算"多视角"，不算独立投票，必须如实标注 independent=false。
"""
from __future__ import annotations

from typing import Any

# verdict 严格度：数值越大越严。仅用于比较两路是否分歧、取更严者。
_SEVERITY = {
    "red_team": {"accept": 0, "pass": 0, "ok": 0, "reject": 2, "blocking": 2},
    "benchmark": {"above": 0, "near": 1, "par": 1, "below": 2},
}


def _norm_score(value: Any) -> float | None:
    """把 0-10 与百分制分数归一到百分制，仅用于分差比较，不改写原分数。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v <= 10:
        v *= 10
    return round(v, 2)


def _severity(stage: str, verdict: Any) -> int | None:
    table = _SEVERITY.get(stage, {})
    key = str(verdict or "").strip().lower()
    return table.get(key)


def score_median(values: list[Any]) -> float | None:
    """偶数路取两数平均（即中位数），不伪造共识精度。"""
    nums = sorted(float(v) for v in values
                  if not isinstance(v, bool) and isinstance(v, (int, float)))
    if not nums:
        return None
    n = len(nums)
    mid = n // 2
    median = nums[mid] if n % 2 else (nums[mid - 1] + nums[mid]) / 2
    return round(median, 2)


def merge_pair(stage: str, primary: dict, secondary: dict, cfg: dict | None = None) -> dict:
    """合并同一 stage 的两路评审。

    返回 {merged, disagreement, reasons, scores, verdicts}。
    merged 保持原 stage 的字段形态，下游聚合逻辑无需改动。
    """
    cfg = cfg or {}
    primary = primary if isinstance(primary, dict) else {}
    secondary = secondary if isinstance(secondary, dict) else {}
    gap = float(cfg.get("disagreement_gap", 10))

    merged = dict(primary)
    reasons: list[str] = []

    # ---- 一票否决：任一评审员的否决信号必须保留 ----
    if stage == "red_team":
        blocking = bool(primary.get("blocking")) or bool(secondary.get("blocking")) \
            or _severity(stage, primary.get("verdict")) == 2 \
            or _severity(stage, secondary.get("verdict")) == 2
        merged["blocking"] = blocking
        if blocking:
            merged["verdict"] = "reject"
        else:
            merged["verdict"] = primary.get("verdict", secondary.get("verdict", "accept"))
        merged["attack_points"] = _dedup_points(
            primary.get("attack_points"), secondary.get("attack_points"))
    elif stage == "benchmark":
        sev_p, sev_s = _severity(stage, primary.get("verdict")), _severity(stage, secondary.get("verdict"))
        if sev_p == 2 or sev_s == 2:
            merged["verdict"] = "below"
        else:
            merged["verdict"] = primary.get("verdict", secondary.get("verdict", "near"))
        merged["gaps"] = _dedup_points(primary.get("gaps"), secondary.get("gaps"))
        if isinstance(primary.get("scores"), dict) and isinstance(secondary.get("scores"), dict):
            keys = set(primary["scores"]) | set(secondary["scores"])
            merged["scores"] = {k: score_median([primary["scores"].get(k), secondary["scores"].get(k)])
                                for k in keys}

    # ---- 分数中位数 ----
    score_key = "score" if stage != "benchmark" else "total"
    raw_scores = [primary.get(score_key), secondary.get(score_key)]
    median = score_median(raw_scores)
    if median is not None:
        merged[score_key] = median

    # ---- 分歧判定 ----
    sev_p, sev_s = _severity(stage, primary.get("verdict")), _severity(stage, secondary.get("verdict"))
    if sev_p is not None and sev_s is not None and sev_p != sev_s:
        reasons.append(f"{stage} 判定不一致: {primary.get('verdict')} vs {secondary.get('verdict')}")
    if stage == "red_team" and bool(primary.get("blocking")) != bool(secondary.get("blocking")):
        reasons.append("红队 blocking 不一致")
    norm = [_norm_score(v) for v in raw_scores]
    norm = [v for v in norm if v is not None]
    if len(norm) == 2 and abs(norm[0] - norm[1]) >= gap:
        reasons.append(f"{stage} 分差 {abs(norm[0] - norm[1]):.1f} ≥ {gap:.0f}")

    return {
        "merged": merged,
        "disagreement": bool(reasons),
        "reasons": reasons,
        "scores": raw_scores,
        "verdicts": [primary.get("verdict"), secondary.get("verdict")],
        "note": score_key,
    }


def _dedup_points(a: Any, b: Any) -> list:
    """合并 attack_points/gaps，按 evidence 或字符串去重，保持顺序稳定。"""
    out: list = []
    seen: set[str] = set()
    for item in list(a or []) + list(b or []):
        key = str(item.get("evidence") or item.get("location") or item) if isinstance(item, dict) else str(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def summarize_committee(results: dict[str, dict]) -> dict:
    """把各 stage 的 dual 结果汇总为 overall.committee 结构。"""
    stages: dict[str, Any] = {}
    disagreements: list[str] = []
    independents: list[bool] = []
    for stage, dual in results.items():
        if not isinstance(dual, dict):
            continue
        stages[stage] = {
            "disagreement": bool(dual.get("disagreement")),
            "reasons": dual.get("reasons", []),
            "scores": dual.get("scores", []),
            "verdicts": dual.get("verdicts", []),
            "independent": bool(dual.get("independent")),
            "backend_pair": dual.get("backend_pair", []),
        }
        if dual.get("disagreement"):
            disagreements.append(stage)
        independents.append(bool(dual.get("independent")))
    same_models = bool(independents) and not all(independents)
    return {
        "enabled": bool(stages),
        "stages": stages,
        "disagreements": disagreements,
        "independent": all(independents) if independents else False,
        "note": (
            "同一后端/模型的多视角评审不等于独立投票"
            if same_models or (independents and not any(independents)) else
            "两路评审来自不同后端或模型"
        ),
    }
