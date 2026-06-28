from .config import *
from .clash import *
from . import config


# ===== 读取测试结果并呈现：记事本报告 + 弹窗摘要 =====
def load_results_payload():
    """读取 RESULT_FILE，返回完整 payload dict；失败返回 None。"""
    if not require_files(RESULT_FILE):
        print("  请先运行一次测试（test 或 all）生成结果文件。")
        return None
    try:
        with open(RESULT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"✗ 读取测试结果失败: {exc}")
        return None
    return data


def regions_in_order(by_region):
    ordered = [region for region in ALL_REGIONS if region in by_region]
    ordered.extend(region for region in by_region if region not in ordered)
    return ordered


def best_per_region(by_region):
    """各地区综合分最低（最干净）的可排名节点，返回 [(region, row), ...]。"""
    picks = []
    for region in regions_in_order(by_region):
        rankable = [row for row in by_region.get(region, []) if is_rankable_result(row)]
        if not rankable:
            continue
        best = min(rankable, key=lambda r: (ip_quality_rank(r), result_score(r)))
        picks.append((region, best))
    return picks


def build_report_text(payload):
    ranked = payload.get("all_ranked") or []
    by_region = payload.get("by_region") or {}
    tested_at = payload.get("tested_at", "未知")
    tested_regions = payload.get("tested_regions") or []
    partial = payload.get("partial")

    lines = []
    lines.append("=" * 60)
    lines.append("节点纯净度报告")
    lines.append("=" * 60)
    lines.append(f"生成时间: {now_str()}")
    lines.append(f"测试时间: {tested_at}")
    if tested_regions:
        lines.append(f"测试地区: {'、'.join(tested_regions)}")
    if partial:
        lines.append("⚠ 本结果来自一次被中断的测试（partial），数据可能不完整。")
    health = payload.get("source_health")
    if isinstance(health, dict) and health.get("total"):
        from .purity import format_source_health
        health_line = format_source_health(health.get("counts") or {}, health.get("total") or 0)
        if health_line:
            lines.append(f"数据源健康度: {health_line}")
    lines.append("")

    picks = best_per_region(by_region)
    lines.append("-" * 60)
    lines.append("各地区最优节点（综合分越低越干净）")
    lines.append("-" * 60)
    if picks:
        for region, row in picks:
            score = result_score(row)
            lines.append(
                f"  {region:6s} {row.get('node', '?'):16s} "
                f"综合分 {format_score(score):>5s}  {score_status(score):10s}  "
                f"{ip_quality_label(row):10s}  {row.get('exit_ip', '?')}"
            )
    else:
        lines.append("  无可用评分节点。")
    lines.append("")

    lines.append("-" * 60)
    lines.append("各地区 TOP 5")
    lines.append("-" * 60)
    for region in regions_in_order(by_region):
        rankable = [row for row in by_region.get(region, []) if is_rankable_result(row)][:5]
        if not rankable:
            continue
        lines.append(f"\n{region}:")
        for rank, row in enumerate(rankable, 1):
            score = result_score(row)
            lines.append(
                f"  {rank}. {row.get('node', '?'):16s} {row.get('exit_ip', '?'):16s} "
                f"综合分 {format_score(score):>5s}  {ip_quality_label(row):10s}  "
                f"{row.get('location', '?')}"
            )
    lines.append("")

    high_risk = [
        row for row in ranked
        if "error" not in row and result_score(row) is not None and not is_rankable_result(row)
    ]
    if high_risk:
        lines.append("-" * 60)
        lines.append("高风险 / 极高风险节点（仅供复核，不参与选优）")
        lines.append("-" * 60)
        for row in high_risk:
            score = result_score(row)
            lines.append(
                f"  {row.get('node', '?'):16s} {row.get('exit_ip', '?'):16s} "
                f"综合分 {format_score(score):>5s}  {score_status(score)}"
            )
        lines.append("")

    failed = [row for row in ranked if "error" in row]
    if failed:
        lines.append("-" * 60)
        lines.append("测试失败 / 无评分节点")
        lines.append("-" * 60)
        for row in failed:
            lines.append(f"  {row.get('node', '?'):16s} {row.get('error', '无评分')}")
        lines.append("")

    trend_lines = build_trend_lines(ranked)
    if trend_lines:
        lines.append("-" * 60)
        lines.append("纯净度走势（本次测过的节点，最新 ← 历史；分越低越干净）")
        lines.append("-" * 60)
        lines.extend(trend_lines)
        lines.append("")

    lines.append("=" * 60)
    lines.append("评分分级: 0-25 低风险 / 26-50 中风险 / 51-75 高风险 / 76-100 极高风险")
    lines.append("数据来源: IPPure 欺诈分 + IPing 网页风险（50/50），IPInfo 补充 ASN/地区")
    lines.append("=" * 60)
    return "\n".join(lines) + "\n"


def build_trend_lines(ranked, limit=5):
    """为本次测过、且历史里有 >=2 次记录的节点生成走势行。

    没有历史归档或所有节点都只有一次记录时返回 []（首次运行静默跳过）。
    """
    from . import history

    series = history.node_trends(limit=limit)
    if not series:
        return []

    out = []
    seen = set()
    for row in ranked:
        node = row.get("node")
        if not node or node in seen:
            continue
        seen.add(node)
        scores = series.get(node)
        if not scores or len(scores) < 2:
            continue
        out.append("  " + history.format_trend_line(node, scores))
    return out


def build_popup_summary(payload, path):
    by_region = payload.get("by_region") or {}
    picks = best_per_region(by_region)
    if not picks:
        return "本次没有可用评分节点。\n请检查 Clash Verge 连接或稍后重试。"
    lines = ["各地区最优节点（综合分越低越干净）:", ""]
    for region, row in picks:
        score = result_score(row)
        lines.append(f"{region}  {row.get('node', '?')}  {format_score(score)} {score_status(score)}")
    lines.append("")
    lines.append(f"详细报告: {path}")
    return "\n".join(lines)


def write_report_file(text, path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception as exc:
        print(f"✗ 写入报告文件失败: {exc}")
        return False


def open_report_file(path):
    """用系统默认程序打开报告文件，跨平台。打不开只记日志，不影响主流程。"""
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa: P204 - Windows 专属，用默认程序打开
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception as exc:
        LOG.debug("打开报告文件失败: %s", exc)
        return False


def show_popup(title, body):
    """Windows 下弹原生消息框；其他平台无原生弹窗，优雅降级（控制台已打印摘要）。"""
    if os.name != "nt":
        return False
    try:
        import ctypes

        # MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND
        ctypes.windll.user32.MessageBoxW(0, str(body), str(title), 0x40 | 0x10000)
        return True
    except Exception as exc:
        LOG.debug("弹窗失败: %s", exc)
        return False


def run_report(open_notepad=None, popup=None, report_format=None):
    """读取现有测试结果，生成报告文件并按设置打开 / 弹窗。

    report_format: text|html|both；None 时读 config.REPORT_FORMAT（默认 text）。
    text/html 都按 report_path() 命名（同基础名不同扩展名），每次新建、保留历史。
    """
    print_section("生成纯净度报告")
    describe_result_age()

    payload = load_results_payload()
    if payload is None:
        return False

    if open_notepad is None:
        open_notepad = REPORT_OPEN_NOTEPAD
    if popup is None:
        popup = REPORT_POPUP
    if report_format is None:
        report_format = config.REPORT_FORMAT
    report_format = str(report_format).lower()
    if report_format not in ("text", "html", "both"):
        report_format = "text"

    want_text = report_format in ("text", "both")
    want_html = report_format in ("html", "both")

    # 本次报告基准路径（.txt）；HTML 用同基础名换 .html。每次新建、保留历史。
    report_file = config.report_path()
    written = []

    if want_text:
        text = build_report_text(payload)
        if write_report_file(text, report_file):
            print(f"✓ 文本报告已生成: {report_file}")
            written.append(report_file)
        else:
            return False

    if want_html:
        from . import report_html
        html_file = os.path.splitext(report_file)[0] + ".html"
        html_text = report_html.build_report_html(payload)
        if write_report_file(html_text, html_file):
            print(f"✓ HTML 报告已生成: {html_file}")
            written.append(html_file)
        else:
            # HTML 失败不致命：若文本已出则继续，否则报错
            if not written:
                return False

    # 控制台也打印一份各地区最优摘要
    picks = best_per_region(payload.get("by_region") or {})
    if picks:
        print("\n各地区最优节点:")
        for region, row in picks:
            score = result_score(row)
            print(
                f"  {region:6s} {row.get('node', '?'):16s} "
                f"综合分 {format_score(score):>5s}  {score_status(score)}"
            )
    else:
        print("⚠ 没有可用评分节点。")

    # 打开主报告：HTML 优先（更直观），否则文本
    if open_notepad and written:
        primary = next((p for p in written if p.endswith(".html")), written[0])
        open_report_file(primary)
    if popup:
        show_popup("节点纯净度报告", build_popup_summary(payload, written[0] if written else report_file))

    return True
