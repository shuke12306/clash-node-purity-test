#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纯标准库测试运行器（无需 pytest）。本地或 CI 直接跑：

    python tests/run.py

覆盖：地区匹配、加权打分 + 降级归一化、数据源健康度、跨平台探测解析
（用临时目录造假 config.yaml / profiles.yaml，不依赖真实 Clash 客户端）。

CI 机器上没有安装任何 Clash 客户端、也没有 local_config.json，正好验证
“找不到客户端时优雅返回、不报错” 这条底线。
"""

import os
import sys
import tempfile

# 让 tests/ 能找到上级的 node_purity 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


_PASS = 0
_FAIL = 0


def check(name, got, expect):
    global _PASS, _FAIL
    if got == expect:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}: got={got!r} expect={expect!r}")


def check_true(name, cond):
    check(name, bool(cond), True)


# ===== 1. 地区匹配 =====
def test_region_matching():
    print("== 地区匹配 ==")
    from node_purity.config import region_of

    cases = {
        "香港 01": "香港",
        "美国静态住宅": "美国",
        "优选-26日18时日本01": "日本",
        "🇺🇸 美国(AI+Google)": "美国",
        "台湾 07": "台湾",
        "新加坡 09": "新加坡",
        "HK-IEPL-01": "香港",
        "JP_Tokyo_BGP": "日本",
        "US 洛杉矶 4x": "美国",
        "SG Premium x2": "新加坡",
        "🇰🇷 Korea 01": "韩国",
        "狮城专线": "新加坡",
        "东京 IPLC": "日本",
        "Hong Kong 02": "香港",
        "United States 05": "美国",
        "JAPAN premium": "日本",
    }
    for name, expect in cases.items():
        check(f"region_of({name!r})", region_of(name), expect)

    # 不该误判：trust 里的 us 不能匹配成美国（词边界）
    check("region_of('trustzone-fast') 不误判", region_of("trustzone-fast"), "trustzone-fast")


# ===== 2. 加权打分 + 降级归一化 =====
def test_weighted_scoring():
    print("== 加权打分 + 降级归一化 ==")
    from node_purity import config
    from node_purity import purity

    def combine(ippure, web, weights):
        config.SOURCE_WEIGHTS = weights
        ippure_info = {"fraud_score": ippure} if ippure is not None else None
        web_info = {"score": web} if web is not None else None
        score, _ = purity.combine_purity_score(ippure_info, None, web_info)
        return score

    # 默认 50/50 = 等权平均
    check("50/50 等权", combine(10, 30, {"ippure": 0.5, "iping_web": 0.5}), 20.0)
    # 缺一源，权重归一化（IPPure 挂，只剩 iping_web）
    check("缺一源归一化", combine(None, 30, {"ippure": 0.5, "iping_web": 0.5}), 30.0)
    # 全缺 -> None
    check("全缺=None", combine(None, None, {"ippure": 0.5, "iping_web": 0.5}), None)
    # 空 dict = 等权
    check("空权重=等权", combine(10, 30, {}), 20.0)
    # 真加权 80/20: 10*.8 + 30*.2 = 14.0
    check("80/20 真加权", combine(10, 30, {"ippure": 0.8, "iping_web": 0.2}), 14.0)
    # 出分源权重全 0 -> 回退等权防除零
    check("权重全0防除零", combine(10, 30, {"ippure": 0.0, "iping_web": 0.0}), 20.0)

    # 数据契约：四键仍在
    config.SOURCE_WEIGHTS = {}
    _, srcs = purity.combine_purity_score({"fraud_score": 10}, None, {"score": 30})
    check("source_scores 四键不变", sorted(srcs.keys()), ["ipinfo", "iping", "iping_web", "ippure"])


# ===== 3. 数据源健康度 =====
def test_source_health():
    print("== 数据源健康度 ==")
    from node_purity import purity

    results = [
        {"node": "a", "source_scores": {"ippure": 10, "iping_web": 20},
         "iping": {"x": 1}, "ipinfo": {"y": 1}},
        {"node": "b", "source_scores": {"ippure": None, "iping_web": 20},
         "iping": {"x": 1}, "ipinfo": None},
        {"node": "c", "error": "切换失败"},  # 失败节点不计入分母
    ]
    health, total = purity.summarize_source_health(results)
    check("健康度分母只算成功节点", total, 2)
    check("ippure 成功数", health["ippure"], 1)
    check("iping_web 成功数", health["iping_web"], 2)
    check("ipinfo 成功数", health["ipinfo"], 1)

    line = purity.format_source_health(health, total)
    check_true("健康度行含 IPInfo 1/2", "IPInfo 1/2" in line)

    # 全失败时返回空
    h2, t2 = purity.summarize_source_health([{"node": "x", "error": "e"}])
    check("全失败 total=0", t2, 0)


# ===== 4. 跨平台探测解析（合成目录，不依赖真实客户端）=====
def test_detect_parsing():
    print("== 跨平台探测解析（合成目录）==")
    from node_purity import autodetect

    with tempfile.TemporaryDirectory() as d:
        # 造假的内核 config.yaml
        with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as f:
            f.write(
                "mixed-port: 7890\n"
                "external-controller: 127.0.0.1:9090\n"
                "secret: test-secret\n"
            )
        runtime = autodetect.detect_runtime(d)
        check("detect_runtime api", runtime.get("api"), "http://127.0.0.1:9090")
        check("detect_runtime secret", runtime.get("secret"), "test-secret")
        check("detect_runtime http_proxy", runtime.get("http_proxy"), "http://127.0.0.1:7890")

        # external-controller 形如 :9091 时补 127.0.0.1
        with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as f:
            f.write("external-controller: ':9091'\nport: 7777\n")
        runtime2 = autodetect.detect_runtime(d)
        check("controller 省略主机补全", runtime2.get("api"), "http://127.0.0.1:9091")
        check("回退 port", runtime2.get("http_proxy"), "http://127.0.0.1:7777")

        # 造假的 profiles.yaml + profile 文件
        os.makedirs(os.path.join(d, "profiles"), exist_ok=True)
        with open(os.path.join(d, "profiles", "abc123.yaml"), "w", encoding="utf-8") as f:
            f.write("proxies: []\nproxy-groups: []\n")
        with open(os.path.join(d, "profiles.yaml"), "w", encoding="utf-8") as f:
            f.write(
                "current: abc123\n"
                "items:\n"
                "  - uid: abc123\n"
                "    name: MySub.yaml\n"
                "    file: abc123.yaml\n"
            )
        path, name = autodetect.detect_current_profile(d)
        check_true("profile 路径解析", path and path.endswith("abc123.yaml"))
        check("profile 显示名去后缀", name, "MySub")


# ===== 5. 探测优雅降级（无客户端目录时不报错）=====
def test_detect_graceful():
    print("== 探测优雅降级 ==")
    from node_purity import autodetect

    # detect_runtime 指向不存在的目录 -> 空 dict，不抛异常
    runtime = autodetect.detect_runtime(os.path.join(tempfile.gettempdir(), "no_such_dir_xyz"))
    check("不存在目录返回空 dict", runtime, {})

    # detect_current_profile 同理 -> (None, "")
    path, name = autodetect.detect_current_profile(os.path.join(tempfile.gettempdir(), "no_such_dir_xyz"))
    check("不存在目录 profile=None", (path, name), (None, ""))


def main():
    print("=" * 50)
    print("CNPT 测试套件")
    print("=" * 50)
    test_region_matching()
    test_weighted_scoring()
    test_source_health()
    test_detect_parsing()
    test_detect_graceful()
    print("=" * 50)
    print(f"结果: {_PASS} passed, {_FAIL} failed")
    print("=" * 50)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
