#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
节点纯净度测试工具的配置与通用工具。

本机运行配置从项目根目录的 local_config.json 读取（文件缺失时自动生成模板并
使用内置默认值）。本工具只做「批量测试节点出口 IP 纯净度」并输出报告，不写回
任何 Clash 配置、不连接任何第三方测速程序或订阅。

绝大多数运行参数（Clash 外部控制器地址、密钥、代理端口、节点来源）会在启动时
自动从本机 Clash Verge 探测（见 autodetect.py）；local_config.json 只作为
可选的「覆盖层」——填了就以它为准，没填就用自动探测值，再没有才用内置默认值。
"""

import argparse
import base64
import builtins
import copy
import html
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime

import requests
import yaml

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except Exception:
    box = None
    Console = None
    Panel = None
    Rule = None
    Table = None
    Text = None


# ===== 本地配置（项目根目录为基准，整个文件夹可整体移动）=====
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPDATA_DIR = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
LOCAL_CONFIG_FILE = os.path.join(BASE_DIR, "local_config.json")


def flag_for(code):
    """由两字母国家代码生成国旗 emoji，避免在源码里直接写 emoji 字面量。"""
    code = str(code).strip().upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)


# 地区识别别名表：参照 ACL4SSR 等被广泛使用的分组正则设计，覆盖主流机场命名习惯
# （中文名/简称、城市名、运营商别名、英文全称、两字母代码、国旗 emoji）。
# 用户可在 local_config.json 的 region_match 里增删地区或别名；默认值已能识别
# 绝大多数机场节点名，无需配置。
DEFAULT_REGION_MATCH = {
    "香港": {
        "code": "HK",
        "names": ["香港", "港"],
        "en": ["Hong Kong", "HongKong"],
    },
    "台湾": {
        "code": "TW",
        "names": ["台湾", "臺灣", "台", "臺", "新北", "彰化"],
        "en": ["Taiwan"],
    },
    "日本": {
        "code": "JP",
        "names": ["日本", "东京", "東京", "大阪", "埼玉", "名古屋", "沪日", "深日", "川日", "泉日"],
        "en": ["Japan", "Tokyo", "Osaka"],
    },
    "美国": {
        "code": "US",
        "names": ["美国", "美國", "美", "洛杉矶", "圣何塞", "圣荷西", "西雅图", "硅谷",
                  "芝加哥", "纽约", "达拉斯", "凤凰城", "波特兰", "拉斯维加斯", "费利蒙"],
        "en": ["United States", "America", "Los Angeles", "Seattle", "Silicon Valley"],
    },
    "新加坡": {
        "code": "SG",
        "names": ["新加坡", "狮城", "獅城", "坡"],
        "en": ["Singapore"],
    },
    "韩国": {
        "code": "KR",
        "names": ["韩国", "韓國", "韩", "韓", "首尔", "首爾"],
        "en": ["Korea", "Seoul"],
    },
    "马来西亚": {
        "code": "MY",
        "names": ["马来西亚", "馬來西亞", "马来", "吉隆坡"],
        "en": ["Malaysia", "Kuala Lumpur"],
    },
    "日本(其他)": {  # 占位：保持与旧分组习惯兼容时可用，默认不单独成区
        "code": "",
        "names": [],
        "en": [],
    },
    "土耳其": {
        "code": "TR",
        "names": ["土耳其", "伊斯坦布尔"],
        "en": ["Turkey", "Istanbul"],
    },
    "阿根廷": {
        "code": "AR",
        "names": ["阿根廷", "布宜诺斯艾利斯"],
        "en": ["Argentina"],
    },
    "英国": {
        "code": "GB",
        "names": ["英国", "英國", "伦敦", "倫敦"],
        "en": ["United Kingdom", "Britain", "London"],
        "alt_codes": ["UK"],
    },
    "德国": {
        "code": "DE",
        "names": ["德国", "德國", "法兰克福", "法蘭克福"],
        "en": ["Germany", "Frankfurt"],
    },
    "法国": {
        "code": "FR",
        "names": ["法国", "法國", "巴黎"],
        "en": ["France", "Paris"],
    },
}

# 去掉占位条目（不参与匹配），仅在用户显式需要时由 region_match 覆盖加回
DEFAULT_REGION_MATCH.pop("日本(其他)", None)

DEFAULT_LOCAL_CONFIG = {
    "paths": {
        # 留空时由 autodetect 用 Clash Verge 当前 profile 作为节点源；
        # 用户显式填写后，autodetect 不再覆盖。
        "config_file": "",
        "result_file": "node_test_result.json",
        "report_file": "节点纯净度报告.txt",
    },
    "clash": {
        "api": "",
        "secret": "",
        "http_proxy": "",
        "select_group": "",
    },
    "regions": {
        "all": list(DEFAULT_REGION_MATCH.keys()),
        "target": ["美国", "台湾", "日本", "新加坡"],
        "import": ["美国", "台湾", "日本", "新加坡"],
    },
    "region_match": DEFAULT_REGION_MATCH,
    "timing": {
        "base_interval": 1.5,
        "max_interval": 10.0,
        "retest_interval": 5.0,
        "switch_verify_timeout": 2.5,
    },
    "report": {
        "open_notepad": True,
        "popup": True,
    },
    "purity_sources": {
        "ipinfo_enabled": True,
        # 内置企业版 IPInfo token（免费、可分发）；如需自备可在 local_config.json 覆盖。
        "ipinfo_token": "151dcf905c7667",
        "iping_enabled": True,
    },
}


def deep_merge_config(base, override):
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def write_local_config_template(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_LOCAL_CONFIG, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_local_config(create_template=False):
    if not os.path.exists(LOCAL_CONFIG_FILE):
        if create_template:
            try:
                write_local_config_template(LOCAL_CONFIG_FILE)
                print(f"已生成本地配置模板: {LOCAL_CONFIG_FILE}")
            except OSError as exc:
                print(f"⚠ 无法生成 local_config.json，将使用内置默认值: {exc}")
        return copy.deepcopy(DEFAULT_LOCAL_CONFIG)

    try:
        with open(LOCAL_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"⚠ 读取 local_config.json 失败，将使用内置默认值: {exc}")
        return copy.deepcopy(DEFAULT_LOCAL_CONFIG)
    if not isinstance(data, dict):
        print("⚠ local_config.json 顶层不是对象，将使用内置默认值。")
        return copy.deepcopy(DEFAULT_LOCAL_CONFIG)
    return deep_merge_config(DEFAULT_LOCAL_CONFIG, data)


def config_section(config, name):
    value = config.get(name, {})
    return value if isinstance(value, dict) else {}


def config_list(value, default):
    return list(value) if isinstance(value, list) else list(default)


def config_bool(value, default):
    return value if isinstance(value, bool) else bool(default)


def resolve_config_path(value):
    path = os.path.expandvars(str(value))
    path = path.replace("%APPDATA%", APPDATA_DIR)
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    return os.path.abspath(path)


LOCAL_CONFIG = load_local_config()
_PATHS = config_section(LOCAL_CONFIG, "paths")
_CLASH = config_section(LOCAL_CONFIG, "clash")
_REGIONS = config_section(LOCAL_CONFIG, "regions")
_REGION_MATCH = config_section(LOCAL_CONFIG, "region_match") or copy.deepcopy(DEFAULT_REGION_MATCH)
_TIMING = config_section(LOCAL_CONFIG, "timing")
_REPORT = config_section(LOCAL_CONFIG, "report")
_PURITY_SOURCES = config_section(LOCAL_CONFIG, "purity_sources")

_CONFIG_FILE_RAW = str(_PATHS.get("config_file", "") or "").strip()
# 用户是否显式指定了节点源文件；若否，autodetect 会用 Clash Verge 当前 profile。
CONFIG_FILE_EXPLICIT = bool(_CONFIG_FILE_RAW)
CONFIG_FILE = resolve_config_path(_CONFIG_FILE_RAW) if _CONFIG_FILE_RAW else ""
RESULT_FILE = resolve_config_path(_PATHS.get("result_file", DEFAULT_LOCAL_CONFIG["paths"]["result_file"]))
REPORT_FILE = resolve_config_path(_PATHS.get("report_file", DEFAULT_LOCAL_CONFIG["paths"]["report_file"]))


# ===== Clash / 纯净度信息源配置 =====
# 这些值优先由 autodetect 在运行时填入；此处仅作为最初默认（通常为空）。
CLASH_API = str(_CLASH.get("api", "") or "")
CLASH_SECRET = str(_CLASH.get("secret", "") or "")
HTTP_PROXY = str(_CLASH.get("http_proxy", "") or "")
SELECT_GROUP = str(_CLASH.get("select_group", "") or "")
# 用户在 local_config.json 里显式填写（非空）的 clash 字段，autodetect 不应覆盖它们。
CLASH_EXPLICIT = {
    key
    for key in ("api", "secret", "http_proxy", "select_group")
    if str(_CLASH.get(key, "") or "").strip()
}
ALL_REGIONS = config_list(_REGIONS.get("all"), DEFAULT_LOCAL_CONFIG["regions"]["all"])
TARGET_REGIONS = config_list(_REGIONS.get("target"), DEFAULT_LOCAL_CONFIG["regions"]["target"])
IMPORT_REGIONS = config_list(_REGIONS.get("import"), DEFAULT_LOCAL_CONFIG["regions"]["import"])
IPPURE_URL = "https://my.ippure.com/v1/info"
IPINFO_URL_TEMPLATE = "https://api.ipinfo.io/lite/{ip}?token={token}"
IPING_URL = "https://api.iping.cc/v1/query"
IPING_WEB_RISK_URL_TEMPLATE = "https://www.iping.cc/ip/{ip}"
IPINFO_ENABLED = config_bool(
    _PURITY_SOURCES.get("ipinfo_enabled"),
    DEFAULT_LOCAL_CONFIG["purity_sources"]["ipinfo_enabled"],
)
IPINFO_TOKEN = str(
    _PURITY_SOURCES.get("ipinfo_token", DEFAULT_LOCAL_CONFIG["purity_sources"]["ipinfo_token"])
)
IPING_ENABLED = config_bool(
    _PURITY_SOURCES.get("iping_enabled"),
    DEFAULT_LOCAL_CONFIG["purity_sources"]["iping_enabled"],
)

# 测试节奏：节点间基础间隔；IPPure 拿不到分时自动翻倍退避（上限 MAX_INTERVAL），成功则衰减回基础值
BASE_INTERVAL = float(_TIMING.get("base_interval", DEFAULT_LOCAL_CONFIG["timing"]["base_interval"]))
MAX_INTERVAL = float(_TIMING.get("max_interval", DEFAULT_LOCAL_CONFIG["timing"]["max_interval"]))
RETEST_INTERVAL = float(_TIMING.get("retest_interval", DEFAULT_LOCAL_CONFIG["timing"]["retest_interval"]))
SWITCH_VERIFY_TIMEOUT = float(
    _TIMING.get("switch_verify_timeout", DEFAULT_LOCAL_CONFIG["timing"]["switch_verify_timeout"])
)

REPORT_OPEN_NOTEPAD = config_bool(_REPORT.get("open_notepad"), DEFAULT_LOCAL_CONFIG["report"]["open_notepad"])
REPORT_POPUP = config_bool(_REPORT.get("popup"), DEFAULT_LOCAL_CONFIG["report"]["popup"])


def _api_headers():
    return {"Authorization": f"Bearer {CLASH_SECRET}"} if CLASH_SECRET else {}


def _proxies():
    return {"http": HTTP_PROXY, "https": HTTP_PROXY} if HTTP_PROXY else {}


# 这两个在 autodetect 填值后由 refresh_runtime_clash() 重建；先给出初始值。
API_HEADERS = _api_headers()
WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/16.0 Mobile/15E148 Safari/604.1"
    )
}
PROXIES = _proxies()


def apply_clash_runtime(api=None, secret=None, http_proxy=None, select_group=None, force=False):
    """把自动探测/手填得到的 Clash 运行参数写入模块级全局，并重建依赖它们的派生值。

    force=False（默认，供 autodetect 用）：跳过用户在 local_config.json 里显式填过的
    字段，保证「手填 > 自动探测」的优先级。force=True：无条件写入。
    """
    global CLASH_API, CLASH_SECRET, HTTP_PROXY, SELECT_GROUP, API_HEADERS, PROXIES
    if api and (force or "api" not in CLASH_EXPLICIT):
        CLASH_API = str(api)
    if secret and (force or "secret" not in CLASH_EXPLICIT):
        CLASH_SECRET = str(secret)
    if http_proxy and (force or "http_proxy" not in CLASH_EXPLICIT):
        HTTP_PROXY = str(http_proxy)
    if select_group and (force or "select_group" not in CLASH_EXPLICIT):
        SELECT_GROUP = str(select_group)
    API_HEADERS = _api_headers()
    PROXIES = _proxies()


def apply_node_source(path):
    """把节点源文件（通常是 Clash Verge 当前 profile）写入模块级全局。"""
    global CONFIG_FILE
    if path:
        CONFIG_FILE = os.path.abspath(str(path))


# ===== 地区识别 =====
def _build_region_matchers(region_match):
    """把 region_match 表展开成扁平的匹配器列表。

    每个匹配器: (canonical, kind, alias_for_priority, test_callable)
    kind ∈ {flag, name, en, code}，优先级见 _match_priority。
    """
    matchers = []
    for canonical, spec in region_match.items():
        if not isinstance(spec, dict):
            continue
        code = str(spec.get("code", "") or "")
        flag = flag_for(code) if code else ""
        if flag:
            matchers.append((canonical, "flag", flag, lambda t, low, f=flag: f in t))
        for nm in spec.get("names", []) or []:
            nm = str(nm)
            if nm:
                matchers.append((canonical, "name", nm, lambda t, low, n=nm: n in t))
        for en in spec.get("en", []) or []:
            en_low = str(en).lower()
            if en_low:
                matchers.append((canonical, "en", en_low, lambda t, low, e=en_low: e in low))
        codes = ([code] if code else []) + list(spec.get("alt_codes", []) or [])
        for cd in codes:
            cd = str(cd)
            if not cd:
                continue
            pat = re.compile(r"(?<![A-Za-z])" + re.escape(cd) + r"(?![A-Za-z])", re.IGNORECASE)
            matchers.append((canonical, "code", cd, lambda t, low, p=pat: bool(p.search(t))))
    return matchers


REGION_MATCH = _REGION_MATCH
REGION_MATCHERS = _build_region_matchers(REGION_MATCH)


def _match_priority(kind, alias):
    if kind == "flag":
        return 100
    if kind == "name":
        return 60 + len(alias) * 2          # 越长越具体，优先级越高
    if kind == "en":
        return 50 + len(alias)
    return 40                                # 两字母代码兜底，最低


def region_of(name, fallback_first_token=True):
    """把任意节点名归一到一个标准地区名。

    支持多种命名习惯：中文名/简称、城市名、运营商别名、英文全称、两字母代码、
    国旗 emoji。匹配到多个地区时按优先级（国旗 > 长中文名 > 英文 > 代码）取最优，
    同优先级按地区表顺序取靠前者。都没匹配上时回退到首段文本（保持旧行为）。
    """
    text = str(name)
    low = text.lower()
    best_key = None
    best_region = None
    for order, (canonical, kind, alias, test) in enumerate(REGION_MATCHERS):
        try:
            hit = test(text, low)
        except Exception:
            hit = False
        if not hit:
            continue
        key = (_match_priority(kind, alias), -order)
        if best_key is None or key > best_key:
            best_key = key
            best_region = canonical
    if best_region is not None:
        return best_region
    if fallback_first_token:
        parts = text.split()
        return parts[0] if parts else text
    return None


CONSOLE_WIDTH = 60

LOG = logging.getLogger("node_purity_tool")


def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


configure_stdio()


_PLAIN_PRINT = builtins.print
_RICH_STDOUT = Console(markup=False, highlight=False, soft_wrap=True) if Console else None
_RICH_STDERR = Console(markup=False, highlight=False, soft_wrap=True, stderr=True) if Console else None


def rich_enabled():
    return _RICH_STDOUT is not None


def rich_console_for_file(file):
    if not rich_enabled():
        return None
    if file is None or file is sys.stdout:
        return _RICH_STDOUT
    if file is sys.stderr:
        return _RICH_STDERR
    return None


def style_for_line(text):
    stripped = str(text).lstrip()
    if stripped.startswith("✓"):
        return "green"
    if stripped.startswith("✗"):
        return "bold red"
    if stripped.startswith("⚠"):
        return "yellow"
    if stripped.startswith("ℹ"):
        return "cyan"
    return None


def print(*objects, sep=" ", end="\n", file=None, flush=False):
    console = rich_console_for_file(file)
    if console is None:
        return _PLAIN_PRINT(*objects, sep=sep, end=end, file=file, flush=flush)

    text = sep.join(str(obj) for obj in objects)
    console.print(text, style=style_for_line(text), end=end)
    if flush:
        console.file.flush()
    return None


def print_section(title):
    if rich_enabled() and Rule:
        _RICH_STDOUT.print()
        _RICH_STDOUT.print(Rule(str(title), style="bold cyan"))
        return
    print("\n" + "=" * CONSOLE_WIDTH)
    print(title)
    print("=" * CONSOLE_WIDTH)


def print_step(title):
    if rich_enabled() and Rule:
        _RICH_STDOUT.print()
        _RICH_STDOUT.print(Rule(str(title), style="dim cyan"))
        return
    print("\n" + "-" * CONSOLE_WIDTH)
    print(title)
    print("-" * CONSOLE_WIDTH)


def print_menu_table(options):
    if rich_enabled() and Table:
        table = Table(show_header=False, box=box.SIMPLE if box else None, padding=(0, 1))
        table.add_column("选项", style="bold cyan", justify="right", no_wrap=True)
        table.add_column("说明")
        for key, label in options:
            table.add_row(str(key), str(label))
        _RICH_STDOUT.print(table)
        return

    for key, label in options:
        print(f"  {key}. {label}")


def format_duration(seconds):
    seconds = int(round(max(0, float(seconds))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分{seconds:02d}秒"
    if minutes:
        return f"{minutes}分{seconds:02d}秒"
    return f"{seconds}秒"


def progress_label(stage, current, total):
    width = max(2, len(str(total)))
    return f"[{stage} {current:0{width}d}/{total:0{width}d}]"


def ps_single_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def send_windows_notification(title, body):
    """Best-effort silent Windows toast notification; never affects the main flow."""
    if os.name != "nt":
        return False
    script = f"""
$ErrorActionPreference = 'Stop'
$title = {ps_single_quote(title)}
$body = {ps_single_quote(body)}
$template = [Windows.UI.Notifications.ToastTemplateType, Windows.UI.Notifications, ContentType = WindowsRuntime]::ToastText02
$xml = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]::GetTemplateContent($template)
$texts = $xml.GetElementsByTagName('text')
$null = $texts.Item(0).AppendChild($xml.CreateTextNode($title))
$null = $texts.Item(1).AppendChild($xml.CreateTextNode($body))
$toastNode = $xml.SelectSingleNode('/toast')
$audio = $xml.CreateElement('audio')
$audio.SetAttribute('silent', 'true')
$null = $toastNode.AppendChild($audio)
$toast = [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime]::new($xml)
$notifier = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]::CreateToastNotifier('节点纯净度测试')
$notifier.Show($toast)
"""
    try:
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "timeout": 5,
            "check": False,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            **kwargs,
        )
        return result.returncode == 0
    except Exception as exc:
        LOG.debug("Windows 通知发送失败: %s", exc)
        return False


def print_final_summary(command, ok, started_at, dry_run=False, interrupted=False):
    elapsed = format_duration(time.time() - started_at)
    status = "已中断" if interrupted else "成功" if ok else "失败"
    if rich_enabled() and Panel and Table and Text:
        status_style = "yellow" if interrupted else "green" if ok else "bold red"
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim", no_wrap=True)
        table.add_column()
        table.add_row("命令", str(command))
        table.add_row("结果", Text(status, style=status_style))
        table.add_row("耗时", elapsed)
        if dry_run:
            table.add_row("dry-run", "是")
        _RICH_STDOUT.print()
        _RICH_STDOUT.print(Panel(table, title="运行结束", border_style=status_style))
    else:
        print_section("运行结束")
        print(f"命令: {command}")
        print(f"结果: {status}")
        print(f"耗时: {elapsed}")
        if dry_run:
            print("dry-run: 是")
    return status, elapsed


def finish_with_summary(command, ok, started_at, dry_run=False, interrupted=False):
    status, elapsed = print_final_summary(
        command, ok, started_at, dry_run=dry_run, interrupted=interrupted
    )
    title = f"节点纯净度测试：{status}"
    body = f"命令 {command}；耗时 {elapsed}；dry-run {'是' if dry_run else '否'}"
    send_windows_notification(title, body)


# ===== 通用工具 =====
def require_files(*paths):
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        for p in missing:
            print(f"✗ 找不到文件: {p}")
        return False
    return True


def prompt_yes_no(question):
    ans = input(question).strip().lower()
    return ans in ("y", "yes")


def now_str():
    return datetime.now().isoformat(timespec="seconds")


def parse_regions(value):
    """解析 --regions 参数；空值返回默认地区，非法地区返回 None。"""
    if not value:
        return list(TARGET_REGIONS)
    chosen = [part for part in re.split(r"[,，\s]+", value.strip()) if part]
    if not chosen:
        print(f"✗ --regions 为空（可选: {'、'.join(ALL_REGIONS)}）")
        return None
    invalid = [r for r in chosen if r not in ALL_REGIONS]
    if invalid:
        print(f"✗ 无效地区: {'、'.join(invalid)}（可选: {'、'.join(ALL_REGIONS)}）")
        return None
    return [r for r in ALL_REGIONS if r in chosen]


def setup_logging(verbose=False, log_file=None):
    LOG.handlers.clear()
    LOG.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(formatter)
    LOG.addHandler(console)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        LOG.addHandler(file_handler)
