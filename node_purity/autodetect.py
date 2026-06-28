#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
零配置自动发现：从本机 Clash Verge 的运行时文件里读出连接参数和当前 profile，
免去用户手填 API 地址、密钥、代理端口、profile 路径。

读取来源（Windows，Clash Verge Rev 默认布局）：
  %APPDATA%\\io.github.clash-verge-rev.clash-verge-rev\\
      config.yaml     运行时内核配置：external-controller / secret / mixed-port
      profiles.yaml   current: <uid> 指向当前启用的 profile
      profiles\\<uid>.yaml  当前 profile，本工具直接拿它的 proxies 当节点源

探测是 best-effort：任何一步失败都不报错，只是该项保持原值（来自 local_config.json
或内置默认），让用户仍可手填回退。非 Windows 环境直接跳过。
"""

import os

import requests
import yaml

from . import config
from .config import (
    LOG,
    apply_clash_runtime,
    apply_node_source,
    print as cprint,
)


def clash_verge_dir():
    """返回 Clash Verge 配置目录；不存在则返回 None。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    candidates = [
        os.path.join(appdata, "io.github.clash-verge-rev.clash-verge-rev"),
        # 旧版本目录名兜底
        os.path.join(appdata, "clash-verge"),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def _load_yaml(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        LOG.debug("读取 YAML 失败 %s: %s", path, exc)
        return None


def detect_runtime(verge_dir):
    """从 config.yaml 解析 external-controller / secret / mixed-port。"""
    cfg = _load_yaml(os.path.join(verge_dir, "config.yaml"))
    if not cfg:
        return {}

    result = {}
    controller = str(cfg.get("external-controller", "") or "").strip()
    if controller:
        # external-controller 可能是 127.0.0.1:9091 或 :9091
        if controller.startswith(":"):
            controller = "127.0.0.1" + controller
        result["api"] = f"http://{controller}"

    secret = cfg.get("secret")
    if secret is not None:
        result["secret"] = str(secret)

    # 代理端口优先 mixed-port，回退 port
    port = cfg.get("mixed-port") or cfg.get("port")
    if port:
        result["http_proxy"] = f"http://127.0.0.1:{port}"

    return result


def _strip_yaml_ext(value):
    value = str(value or "").strip()
    for ext in (".yaml", ".yml"):
        if value.lower().endswith(ext):
            return value[: -len(ext)]
    return value


def detect_current_profile(verge_dir):
    """从 profiles.yaml 的 current 找到当前 profile 文件路径与显示名。

    返回 (profile_path, display_name)；找不到返回 (None, "")。
    显示名优先取 items 里的 name 字段（去 .yaml 后缀），回退到文件名 stem，
    再回退到 uid，供报告文件命名使用。
    """
    profiles = _load_yaml(os.path.join(verge_dir, "profiles.yaml"))
    if not profiles:
        return None, ""
    current = profiles.get("current")
    if not current:
        return None, ""

    # current 可能直接是 uid，也可能在 items 里有对应 file 字段
    file_name = None
    display_name = ""
    for item in profiles.get("items", []) or []:
        if isinstance(item, dict) and item.get("uid") == current:
            file_name = item.get("file") or f"{current}.yaml"
            display_name = _strip_yaml_ext(item.get("name"))
            break
    if not file_name:
        file_name = f"{current}.yaml"

    profile_path = os.path.join(verge_dir, "profiles", file_name)
    if not os.path.isfile(profile_path):
        return None, ""

    if not display_name:
        display_name = _strip_yaml_ext(file_name) or str(current)
    return profile_path, display_name


_GROUP_KEYWORDS = ("节点选择", "手动", "选择", "Select", "Proxy", "PROXY", "节点")


def _pick_group_by_keyword(names):
    for keyword in _GROUP_KEYWORDS:
        for name in names:
            if keyword in name:
                return name
    return ""


def detect_select_group_via_api():
    """查实时 Clash API 的 Selector 组，挑一个可手动切换的主选择组。

    这才是运行时真相：像「🚀 节点选择」这种组常由 Clash Verge 合并 Merge/Script
    在运行时生成，只存在于 API，不在 profile 静态文件里。
    优先名字含关键词的组，否则取成员最多的（排除 GLOBAL）。读不到返回 ""。
    """
    try:
        resp = requests.get(
            f"{config.CLASH_API}/proxies",
            headers=config.API_HEADERS,
            timeout=5,
        )
        resp.raise_for_status()
        proxies = resp.json().get("proxies", {})
    except Exception as exc:
        LOG.debug("查实时 API 代理组失败: %s", exc)
        return ""

    selectors = {
        name: info for name, info in proxies.items()
        if isinstance(info, dict) and info.get("type") == "Selector"
    }
    if not selectors:
        return ""

    names = list(selectors.keys())
    hit = _pick_group_by_keyword(names)
    if hit:
        return hit

    # 兜底：取成员最多的 Selector。GLOBAL 通常包含全部节点，是「能切到任意节点」
    # 最稳妥的组，所以让它一起参与按成员数排序，而不是排除它。
    ranked = sorted(
        names,
        key=lambda n: len(selectors[n].get("all", []) or []),
        reverse=True,
    )
    return ranked[0] if ranked else ""


def detect_select_group_from_profile(profile_path):
    """兜底：从 profile 静态文件的 proxy-groups 里挑 Selector 组。"""
    data = _load_yaml(profile_path)
    if not data:
        return ""
    groups = data.get("proxy-groups", [])
    if not isinstance(groups, list):
        return ""

    selectors = [
        g for g in groups
        if isinstance(g, dict) and str(g.get("type", "")).lower() == "select"
    ]
    if not selectors:
        return ""

    names = [str(g.get("name", "")) for g in selectors]
    hit = _pick_group_by_keyword(names)
    if hit:
        return hit

    best = max(selectors, key=lambda g: len(g.get("proxies", []) or []))
    return str(best.get("name", ""))


def autodetect(verbose=True):
    """运行自动探测并把结果应用到 config 全局；返回探测到的字段摘要 dict。"""
    if os.name != "nt":
        if verbose:
            cprint("ℹ 非 Windows 环境，跳过 Clash Verge 自动探测，使用 local_config.json / 默认值。")
        return {}

    verge_dir = clash_verge_dir()
    if not verge_dir:
        if verbose:
            cprint("ℹ 未发现 Clash Verge 配置目录，使用 local_config.json / 默认值。")
        return {}

    summary = {}
    runtime = detect_runtime(verge_dir)
    if runtime:
        apply_clash_runtime(
            api=runtime.get("api"),
            secret=runtime.get("secret"),
            http_proxy=runtime.get("http_proxy"),
        )
        summary.update(runtime)

    profile_path, profile_name = detect_current_profile(verge_dir)
    if profile_path:
        # 用户没在 local_config.json 显式指定节点源时，才用当前 profile 作为节点源；
        # 显式指定了就尊重用户选择，不覆盖。
        if not config.CONFIG_FILE_EXPLICIT:
            apply_node_source(profile_path, profile_name)
            summary["profile"] = profile_path
            if profile_name:
                summary["profile_name"] = profile_name
        else:
            summary["profile_skipped"] = profile_path
        # 切换组优先查实时 API（运行时真相），读不到再回退 profile 静态文件
        if not config.SELECT_GROUP:
            group = detect_select_group_via_api() or detect_select_group_from_profile(profile_path)
            if group:
                apply_clash_runtime(select_group=group)
                summary["select_group"] = group

    if verbose:
        if summary:
            cprint("✓ 已自动发现 Clash Verge 配置：")
            if "api" in summary:
                cprint(f"  外部控制器: {summary['api']}")
            if "http_proxy" in summary:
                cprint(f"  代理端口: {summary['http_proxy']}")
            if "secret" in summary:
                masked = "（空）" if not summary["secret"] else "******"
                cprint(f"  API 密钥: {masked}")
            if "profile" in summary:
                cprint(f"  当前 profile: {summary['profile']}")
            if "select_group" in summary:
                cprint(f"  切换组: {summary['select_group']}")
            cprint("  （以上可在 local_config.json 中手动覆盖）")
        else:
            cprint("⚠ 找到 Clash Verge 目录但未解析出可用参数，请检查 local_config.json。")
    return summary
