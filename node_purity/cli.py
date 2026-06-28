import argparse
import time

from .config import *
from .autodetect import autodetect
from .purity import run_test
from .report import run_report


def run_all(regions, open_notepad=None, popup=None, report_format=None, assume_yes=False):
    print_section("节点纯净度检测")
    print(f"本次测试地区: {', '.join(regions)}")

    print_step("阶段 1/2：纯净度测试")
    if not run_test(regions, assume_yes=True):
        print("\n✗ 测试未完成，为避免使用旧结果，已跳过出报告步骤。")
        return False

    print_step("阶段 2/2：生成纯净度报告")
    return run_report(open_notepad=open_notepad, popup=popup, report_format=report_format)


def run_menu(regions, open_notepad=None, popup=None, report_format=None):
    print_section("节点纯净度检测菜单")
    print("请选择运行模式：")
    print_menu_table([
        ("1", "正常运行（纯净度测试 + 生成报告）"),
        ("2", "仅测试节点（只写入测试结果文件，不出报告）"),
        ("3", "仅生成报告（用现有测试结果，不重新测试）"),
    ])
    try:
        choice = input("\n请输入 1 / 2 / 3 后按回车: ").strip()
    except EOFError:
        print("\n✗ 没有读取到菜单输入，已退出。")
        return False

    if choice == "1":
        return run_all(regions, open_notepad=open_notepad, popup=popup, report_format=report_format, assume_yes=True)
    if choice == "2":
        return run_test(regions, assume_yes=True)
    if choice == "3":
        return run_report(open_notepad=open_notepad, popup=popup, report_format=report_format)

    print(f"\n✗ 无效选择: {choice or '（空）'}。请输入 1、2 或 3。")
    return False


def build_parser():
    parser = argparse.ArgumentParser(
        description="节点纯净度检测工具",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=("menu", "all", "test", "report"),
        help=(
            "menu    显示 1/2/3 菜单（bat 默认入口）\n"
            "all     测试后生成报告（默认）\n"
            "test    只测试节点并写入测试结果文件\n"
            "report  只根据现有测试结果生成报告"
        ),
    )
    parser.add_argument(
        "--regions",
        help=(
            "只测试指定地区（仅 menu/all/test 有效），逗号分隔，如: --regions 台湾,日本\n"
            f"可选: {'、'.join(ALL_REGIONS)}；默认: {'、'.join(TARGET_REGIONS)}\n"
            "定向重测结果会合并进现有测试结果文件，其他地区数据保留"
        ),
    )
    parser.add_argument(
        "--no-detect",
        action="store_true",
        help="跳过 Clash Verge 自动探测，只用 local_config.json / 默认值",
    )
    parser.add_argument(
        "--report",
        choices=("notepad", "popup", "both", "none"),
        help="报告呈现方式：notepad=只开记事本/默认程序，popup=只弹窗，both=两者，none=都不（默认读配置，两者都开）",
    )
    parser.add_argument(
        "--format",
        dest="report_format",
        choices=("text", "html", "both"),
        help="报告格式：text=纯文本，html=自包含网页，both=两者（默认读配置，缺省 text）",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="无人值守模式：跳过所有询问（报告仍照常生成）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="显示更详细的请求与重试日志",
    )
    parser.add_argument(
        "--log-file",
        help="把详细日志写入指定文件；默认不生成日志文件",
    )
    return parser


def resolve_report_flags(value):
    """把 --report 选项翻成 (open_notepad, popup)；None 表示用配置默认。"""
    if value is None:
        return None, None
    if value == "notepad":
        return True, False
    if value == "popup":
        return False, True
    if value == "both":
        return True, True
    return False, False  # none


def main(argv=None):
    started_at = time.time()
    args = build_parser().parse_args(argv)
    setup_logging(verbose=args.verbose, log_file=args.log_file)
    try:
        if not args.no_detect:
            autodetect(verbose=True)

        regions = parse_regions(args.regions)
        if regions is None:
            finish_with_summary(args.command, False, started_at)
            return 2
        if args.regions and args.command == "report":
            print("ℹ --regions 只影响 menu/all/test 的测试范围，对当前命令无效。")

        open_notepad, popup = resolve_report_flags(args.report)
        report_format = args.report_format

        ok = True
        if args.command == "all":
            ok = run_all(regions, open_notepad=open_notepad, popup=popup, report_format=report_format, assume_yes=args.yes)
        elif args.command == "menu":
            ok = run_menu(regions, open_notepad=open_notepad, popup=popup, report_format=report_format)
        elif args.command == "test":
            ok = run_test(regions, assume_yes=args.yes)
        elif args.command == "report":
            ok = run_report(open_notepad=open_notepad, popup=popup, report_format=report_format)
        finish_with_summary(args.command, ok, started_at)
        return 0 if ok else 1
    except KeyboardInterrupt:
        finish_with_summary(args.command, False, started_at, interrupted=True)
        return 130
