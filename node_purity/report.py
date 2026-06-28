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

    lines.append("=" * 60)
    lines.append("评分分级: 0-25 低风险 / 26-50 中风险 / 51-75 高风险 / 76-100 极高风险")
    lines.append("数据来源: IPPure 欺诈分 + IPing 网页风险（50/50），IPInfo 补充 ASN/地区")
    lines.append("=" * 60)
    return "\n".join(lines) + "\n"


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


def open_in_notepad(path):
    if os.name != "nt":
        return False
    try:
        subprocess.Popen(["notepad.exe", path])
        return True
    except Exception as exc:
        LOG.debug("打开记事本失败: %s", exc)
        return False


def show_popup(title, body):
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


def run_report(open_notepad=None, popup=None):
    """读取现有测试结果，生成报告文件并按设置打开记事本 / 弹窗。"""
    print_section("生成纯净度报告")
    describe_result_age()

    payload = load_results_payload()
    if payload is None:
        return False

    if open_notepad is None:
        open_notepad = REPORT_OPEN_NOTEPAD
    if popup is None:
        popup = REPORT_POPUP

    # 本次报告路径：运行时拼出 <基础名>_<配置名>_<日期>.txt，每次新建、保留历史。
    report_file = config.report_path()

    text = build_report_text(payload)
    if not write_report_file(text, report_file):
        return False
    print(f"✓ 报告已生成: {report_file}")

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

    if open_notepad:
        open_in_notepad(report_file)
    if popup:
        show_popup("节点纯净度报告", build_popup_summary(payload, report_file))

    return True
