from . import config
from .config import LOG, now_str
from .clash import numeric_score, result_score

import json
import os


# ===== 历史归档（JSONL，只追加；超量时惰性压实）=====
def _history_record(row, tested_at):
    """从一条测试结果提取用于趋势的精简记录；无 node 名则返回 None。"""
    node = row.get("node")
    if not node:
        return None
    rec = {
        "node": node,
        "tested_at": row.get("tested_at") or tested_at,
        "exit_ip": row.get("exit_ip"),
        "purity_score": result_score(row),
    }
    if "error" in row:
        rec["error"] = row.get("error")
    return rec


def append_history(results, tested_at=None, path=None, keep=None):
    """把本次结果的每节点精简记录追加进 JSONL 归档。

    best-effort：任何异常只记日志，绝不影响主流程。
    keep>0 时，当某节点记录数超过 keep 触发一次惰性压实（重写文件，每节点留最近 keep 条）。
    """
    path = path or config.HISTORY_FILE
    if tested_at is None:
        tested_at = now_str()
    if keep is None:
        keep = config.HISTORY_KEEP

    records = []
    for row in results:
        if not isinstance(row, dict):
            continue
        rec = _history_record(row, tested_at)
        if rec is not None:
            records.append(rec)
    if not records:
        return False

    try:
        with open(path, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        LOG.debug("写入历史归档失败: %s", exc)
        return False

    try:
        _maybe_compact(path, keep)
    except Exception as exc:
        LOG.debug("压实历史归档失败: %s", exc)
    return True


def load_history(path=None):
    """读 JSONL 归档，返回记录列表（按文件顺序，即时间先后）。读不到返回 []。"""
    path = path or config.HISTORY_FILE
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue  # 单行损坏不影响其余
                if isinstance(rec, dict) and rec.get("node"):
                    out.append(rec)
    except Exception as exc:
        LOG.debug("读取历史归档失败: %s", exc)
        return []
    return out


def _maybe_compact(path, keep):
    """当存在节点的记录数超过 keep 时，重写文件让每节点只保留最近 keep 条。

    keep<=0 表示不限、不压实。保持记录的原始相对顺序（时间先后）。
    """
    if not keep or keep <= 0:
        return
    records = load_history(path)
    if not records:
        return

    counts = {}
    for rec in records:
        counts[rec["node"]] = counts.get(rec["node"], 0) + 1
    if all(c <= keep for c in counts.values()):
        return  # 没有任何节点超量，无需重写

    # 每节点保留最近 keep 条：从后往前数，标记保留的索引
    seen = {}
    keep_idx = set()
    for i in range(len(records) - 1, -1, -1):
        node = records[i]["node"]
        n = seen.get(node, 0)
        if n < keep:
            keep_idx.add(i)
            seen[node] = n + 1

    compacted = [records[i] for i in range(len(records)) if i in keep_idx]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for rec in compacted:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


# ===== 趋势聚合 =====
def node_trends(path=None, limit=5):
    """按节点聚合最近 limit 次综合分序列。

    返回 {node: [oldest..newest 的分数列表]}；只取有数值分的记录。
    """
    records = load_history(path)
    series = {}
    for rec in records:
        score = numeric_score(rec.get("purity_score"))
        if score is None:
            continue
        series.setdefault(rec["node"], []).append(score)
    if limit and limit > 0:
        series = {node: scores[-limit:] for node, scores in series.items()}
    return series


def trend_direction(scores):
    """根据分数序列判断趋势方向（分越低越干净）。

    返回 ("改善"|"恶化"|"平稳"|"", symbol)；不足两点返回 ("", "")。
    比较最新值与上一次值，差异 <2 视为平稳。
    """
    if not scores or len(scores) < 2:
        return "", ""
    latest = scores[-1]
    prev = scores[-2]
    delta = latest - prev
    if delta <= -2:
        return "改善", "↓"
    if delta >= 2:
        return "恶化", "↑"
    return "平稳", "→"


def format_trend_line(node, scores, width=16):
    """把一个节点的走势格式化成一行：最新 ← 次新 ← ... + 方向标记。"""
    if not scores:
        return ""
    from .clash import format_score
    chain = " ← ".join(format_score(s) for s in reversed(scores))
    direction, symbol = trend_direction(scores)
    tag = f"  {symbol}{direction}" if direction else ""
    return f"{node:{width}s} {chain}{tag}"
