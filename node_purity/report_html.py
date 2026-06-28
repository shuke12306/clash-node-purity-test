from .config import *
from .clash import *
from . import config

import html as _html


# ===== 自包含 HTML 报告：CSS 全内联，无外部依赖，双击即可在浏览器打开 =====
def _esc(value):
    """把任意值转成 HTML 安全文本；None/缺失显示为占位符。"""
    if value is None:
        return "?"
    return _html.escape(str(value), quote=True)


def _score_class(score):
    """按综合分返回风险等级的 CSS 类名。"""
    s = numeric_score(score)
    if s is None:
        return "na"
    if s <= 25:
        return "low"
    if s <= 50:
        return "mid"
    if s <= 75:
        return "high"
    return "vhigh"


def _score_cell(score):
    s = numeric_score(score)
    text = _esc(format_score(score)) if s is not None else "N/A"
    return f'<span class="score {_score_class(score)}">{text}</span>'


_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", Roboto, sans-serif;
       margin: 0; padding: 24px; background: #f5f6f8; color: #1f2329; line-height: 1.5; }
.wrap { max-width: 980px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 28px 0 10px; padding-bottom: 6px; border-bottom: 2px solid #e3e6ea; }
.meta { color: #6b7280; font-size: 13px; margin-bottom: 4px; }
.warn { color: #b45309; font-weight: 600; }
table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px;
        overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
th, td { padding: 8px 12px; text-align: left; font-size: 13px; border-bottom: 1px solid #eef0f2; }
th { background: #fafbfc; color: #6b7280; font-weight: 600; white-space: nowrap; }
tr:last-child td { border-bottom: none; }
.score { display: inline-block; min-width: 42px; padding: 2px 8px; border-radius: 10px;
         text-align: center; font-weight: 600; color: #fff; }
.score.low { background: #16a34a; }
.score.mid { background: #ca8a04; }
.score.high { background: #ea580c; }
.score.vhigh { background: #dc2626; }
.score.na { background: #9ca3af; }
.region { font-weight: 600; }
.mono { font-family: "SF Mono", Consolas, monospace; color: #4b5563; }
.trend-up { color: #dc2626; font-weight: 600; }
.trend-down { color: #16a34a; font-weight: 600; }
.trend-flat { color: #6b7280; }
.health { font-size: 13px; color: #4b5563; }
footer { margin-top: 24px; color: #9ca3af; font-size: 12px; }
.empty { color: #9ca3af; font-style: italic; }
"""


def _section(title, body_html):
    return f"<h2>{_esc(title)}</h2>\n{body_html}"


def _best_table(picks):
    if not picks:
        return '<p class="empty">无可用评分节点。</p>'
    rows = []
    for region, row in picks:
        score = result_score(row)
        rows.append(
            "<tr>"
            f'<td class="region">{_esc(region)}</td>'
            f"<td>{_esc(row.get('node'))}</td>"
            f"<td>{_score_cell(score)}</td>"
            f"<td>{_esc(score_status(score))}</td>"
            f"<td>{_esc(ip_quality_label(row))}</td>"
            f'<td class="mono">{_esc(row.get("exit_ip"))}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>地区</th><th>节点</th><th>综合分</th><th>风险</th><th>类型</th><th>出口 IP</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _regions_in_order(by_region):
    ordered = [region for region in ALL_REGIONS if region in by_region]
    ordered.extend(region for region in by_region if region not in ordered)
    return ordered


def _best_per_region(by_region):
    """各地区综合分最低（最干净）的可排名节点，返回 [(region, row), ...]。"""
    picks = []
    for region in _regions_in_order(by_region):
        rankable = [row for row in by_region.get(region, []) if is_rankable_result(row)]
        if not rankable:
            continue
        best = min(rankable, key=lambda r: (ip_quality_rank(r), result_score(r)))
        picks.append((region, best))
    return picks


def _top5_table(by_region):
    blocks = []
    for region in _regions_in_order(by_region):
        rankable = [r for r in by_region.get(region, []) if is_rankable_result(r)][:5]
        if not rankable:
            continue
        rows = []
        for rank, row in enumerate(rankable, 1):
            score = result_score(row)
            rows.append(
                "<tr>"
                f"<td>{rank}</td>"
                f"<td>{_esc(row.get('node'))}</td>"
                f'<td class="mono">{_esc(row.get("exit_ip"))}</td>'
                f"<td>{_score_cell(score)}</td>"
                f"<td>{_esc(ip_quality_label(row))}</td>"
                f"<td>{_esc(row.get('location'))}</td>"
                "</tr>"
            )
        blocks.append(
            f'<h3 style="font-size:14px;margin:14px 0 6px;">{_esc(region)}</h3>'
            "<table><thead><tr>"
            "<th>#</th><th>节点</th><th>出口 IP</th><th>综合分</th><th>类型</th><th>位置</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )
    if not blocks:
        return '<p class="empty">无可用评分节点。</p>'
    return "\n".join(blocks)


def _trend_table(ranked, limit=5):
    from . import history

    series = history.node_trends(limit=limit)
    if not series:
        return ""
    rows = []
    seen = set()
    for row in ranked:
        node = row.get("node")
        if not node or node in seen:
            continue
        seen.add(node)
        scores = series.get(node)
        if not scores or len(scores) < 2:
            continue
        direction, symbol = history.trend_direction(scores)
        cls = {"改善": "trend-down", "恶化": "trend-up"}.get(direction, "trend-flat")
        chain = " ← ".join(_esc(format_score(s)) for s in reversed(scores))
        rows.append(
            "<tr>"
            f"<td>{_esc(node)}</td>"
            f'<td class="mono">{chain}</td>'
            f'<td class="{cls}">{_esc(symbol)} {_esc(direction)}</td>'
            "</tr>"
        )
    if not rows:
        return ""
    return (
        "<table><thead><tr><th>节点</th><th>走势（最新 ← 历史）</th><th>趋势</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


def _risk_table(ranked):
    high = [
        r for r in ranked
        if "error" not in r and result_score(r) is not None and not is_rankable_result(r)
    ]
    if not high:
        return ""
    rows = []
    for row in high:
        score = result_score(row)
        rows.append(
            "<tr>"
            f"<td>{_esc(row.get('node'))}</td>"
            f'<td class="mono">{_esc(row.get("exit_ip"))}</td>'
            f"<td>{_score_cell(score)}</td>"
            f"<td>{_esc(score_status(score))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>节点</th><th>出口 IP</th><th>综合分</th><th>风险</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


def _failed_table(ranked):
    failed = [r for r in ranked if "error" in r]
    if not failed:
        return ""
    rows = "".join(
        f"<tr><td>{_esc(r.get('node'))}</td><td>{_esc(r.get('error', '无评分'))}</td></tr>"
        for r in failed
    )
    return (
        "<table><thead><tr><th>节点</th><th>原因</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def build_report_html(payload):
    ranked = payload.get("all_ranked") or []
    by_region = payload.get("by_region") or {}
    tested_at = payload.get("tested_at", "未知")
    tested_regions = payload.get("tested_regions") or []
    partial = payload.get("partial")

    picks = _best_per_region(by_region)

    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>节点纯净度报告</title>",
        f"<style>{_CSS}</style></head><body><div class=\"wrap\">",
        "<h1>节点纯净度报告</h1>",
        f'<div class="meta">生成时间: {_esc(now_str())}</div>',
        f'<div class="meta">测试时间: {_esc(tested_at)}</div>',
    ]
    if tested_regions:
        parts.append(f'<div class="meta">测试地区: {_esc("、".join(tested_regions))}</div>')
    if partial:
        parts.append('<div class="meta warn">⚠ 本结果来自一次被中断的测试，数据可能不完整。</div>')

    # 数据源健康度
    health = payload.get("source_health") or {}
    counts = health.get("counts") if isinstance(health, dict) else None
    total = health.get("total") if isinstance(health, dict) else None
    if counts and total:
        from .purity import format_source_health
        line = format_source_health(counts, total)
        if line:
            parts.append(f'<div class="health">数据源健康度: {_esc(line)}</div>')

    parts.append(_section("各地区最优节点（综合分越低越干净）", _best_table(picks)))
    parts.append(_section("各地区 TOP 5", _top5_table(by_region)))

    trend = _trend_table(ranked)
    if trend:
        parts.append(_section("纯净度走势（本次测过的节点，最新 ← 历史）", trend))

    risk = _risk_table(ranked)
    if risk:
        parts.append(_section("高风险 / 极高风险节点（仅供复核，不参与选优）", risk))

    failed = _failed_table(ranked)
    if failed:
        parts.append(_section("测试失败 / 无评分节点", failed))

    parts.append(
        "<footer>评分分级: 0-25 低风险 / 26-50 中风险 / 51-75 高风险 / 76-100 极高风险<br>"
        "数据来源: IPPure 欺诈分 + IPing 网页风险（50/50），IPInfo 补充 ASN/地区</footer>"
    )
    parts.append("</div></body></html>")
    return "\n".join(parts)


def write_html_report(payload, path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_report_html(payload))
        return True
    except Exception as exc:
        print(f"✗ 写入 HTML 报告失败: {exc}")
        return False
