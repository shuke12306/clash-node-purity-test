from . import config
from .config import *
from .clash import *


# ===== 第一步：测试节点 =====
def load_nodes(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f.read())
    if isinstance(data, dict):
        return data.get("proxies", [])
    return data


def build_group_map(node_names):
    """一次性拉取代理组，为每个待测节点确定要切换的 Selector 组。

    返回 (group_map, originals)：
      group_map  节点名 -> 目标组名（优先 config.SELECT_GROUP）
      originals  所有会被用到的组当前选中的节点，测试结束后恢复
    API 读取失败时返回 (None, {})。
    """
    try:
        resp = request_with_retry(
            "GET", f"{config.CLASH_API}/proxies", headers=config.API_HEADERS, timeout=5
        )
        resp.raise_for_status()
        proxies = resp.json().get("proxies", {})
    except Exception as exc:
        print(f"✗ 读取代理组失败: {exc}")
        return None, {}

    selectors = {
        name: info for name, info in proxies.items()
        if info.get("type") == "Selector"
    }
    group_map = {}
    main_members = selectors.get(config.SELECT_GROUP, {}).get("all", [])
    for node_name in node_names:
        if node_name in main_members:
            group_map[node_name] = config.SELECT_GROUP
            continue
        for group_name, info in selectors.items():
            if node_name in info.get("all", []):
                group_map[node_name] = group_name
                break

    originals = {}
    for group in set(group_map.values()):
        now = selectors.get(group, {}).get("now")
        if now:
            originals[group] = now
    return group_map, originals


def restore_selection(originals):
    """测试结束后把各组切回原选中节点，不改变用户当前出口。"""
    for group, node_name in originals.items():
        try:
            request_with_retry(
                "PUT",
                f"{config.CLASH_API}/proxies/{group}",
                headers=config.API_HEADERS,
                json={"name": node_name},
                timeout=5,
            )
            print(f"✓ 已恢复「{group}」原选中节点: {node_name}")
        except Exception as exc:
            print(f"⚠ 恢复「{group}」选中节点失败: {exc}")


def switch_node(node_name, target_group):
    if not target_group:
        print(f"✗ 没有可切换的 Selector 组包含节点: {node_name}")
        return False

    try:
        put_resp = request_with_retry(
            "PUT",
            f"{config.CLASH_API}/proxies/{target_group}",
            headers=config.API_HEADERS,
            json={"name": node_name},
            timeout=5,
        )
        if put_resp.status_code not in (200, 204):
            print(f"✗ 切换请求异常 HTTP {put_resp.status_code}: {put_resp.text[:200]}")
            return False
    except Exception as exc:
        print(f"✗ 切换节点失败: {exc}")
        return False

    deadline = time.time() + SWITCH_VERIFY_TIMEOUT
    last_now = None
    while True:
        try:
            verify = request_with_retry(
                "GET",
                f"{config.CLASH_API}/proxies/{target_group}",
                headers=config.API_HEADERS,
                timeout=5,
            ).json()
        except Exception as exc:
            print(f"⚠ 无法确认切换结果: {exc}，假定已切换")
            return True
        last_now = verify.get("now")
        if last_now == node_name:
            print(f"✓ 已切换到节点: {node_name}（组: {target_group}）")
            return True
        if time.time() >= deadline:
            break
        time.sleep(0.3)

    print(f"✗ 切换未生效，当前为: {last_now}（期望: {node_name}）")
    return False


def query_ippure(retries=3):
    try:
        resp = request_with_retry(
            "GET",
            IPPURE_URL,
            proxies=config.PROXIES,
            headers=WEB_HEADERS,
            timeout=15,
            attempts=retries,
            backoff=3,
        )
    except Exception as exc:
        print(f"  ⚠ IPPure 查询失败: 请求异常({exc})")
        return None

    if not resp.ok:
        print(f"  ⚠ IPPure 查询失败: HTTP {resp.status_code}")
        return None

    try:
        data = resp.json()
    except Exception:
        print("  ⚠ IPPure 查询失败: 返回非 JSON(疑似限流/拦截页)")
        return None

    if not isinstance(data, dict) or not data.get("ip"):
        print("  ⚠ IPPure 查询失败: JSON 无 ip 字段(疑似异常响应)")
        return None

    return {
        "ip": data.get("ip"),
        "fraud_score": clamp_score(data.get("fraudScore")),
        "country": data.get("countryCode"),
        "region": data.get("region"),
        "city": data.get("city"),
        "isp": data.get("asOrganization"),
        "asn": f"AS{data['asn']}" if data.get("asn") else None,
        "is_residential": data.get("isResidential"),
        "is_broadcast": data.get("isBroadcast"),
    }


def clamp_score(value):
    score = numeric_score(value)
    if score is None:
        return None
    return max(0.0, min(100.0, score))


def query_ipinfo(ip, retries=2):
    if not IPINFO_ENABLED or not IPINFO_TOKEN:
        return None
    url = IPINFO_URL_TEMPLATE.format(ip=ip, token=IPINFO_TOKEN)
    try:
        resp = request_with_retry(
            "GET",
            url,
            headers=WEB_HEADERS,
            timeout=10,
            attempts=retries,
            backoff=2,
        )
    except Exception as exc:
        print(f"  ⚠ IPInfo 查询失败: 请求异常({exc})")
        return None

    if not resp.ok:
        print(f"  ⚠ IPInfo 查询失败: HTTP {resp.status_code}")
        return None

    try:
        data = resp.json()
    except Exception:
        print("  ⚠ IPInfo 查询失败: 返回非 JSON")
        return None

    if not isinstance(data, dict) or not data.get("ip"):
        print("  ⚠ IPInfo 查询失败: JSON 无 ip 字段")
        return None

    return {
        "ip": data.get("ip"),
        "asn": data.get("asn"),
        "as_name": data.get("as_name"),
        "as_domain": data.get("as_domain"),
        "country_code": data.get("country_code"),
        "country": data.get("country"),
        "continent_code": data.get("continent_code"),
        "continent": data.get("continent"),
    }


def query_iping(ip, retries=2):
    if not IPING_ENABLED:
        return None
    try:
        resp = request_with_retry(
            "GET",
            IPING_URL,
            params={"ip": ip, "language": "zh"},
            headers=WEB_HEADERS,
            timeout=10,
            attempts=retries,
            backoff=2,
        )
    except Exception as exc:
        print(f"  ⚠ IPing 查询失败: 请求异常({exc})")
        return None

    if not resp.ok:
        print(f"  ⚠ IPing 查询失败: HTTP {resp.status_code}")
        return None

    try:
        payload = resp.json()
    except Exception:
        print("  ⚠ IPing 查询失败: 返回非 JSON")
        return None

    if not isinstance(payload, dict) or str(payload.get("code")) != "200":
        print(f"  ⚠ IPing 查询失败: {payload.get('msg', '异常响应') if isinstance(payload, dict) else '异常响应'}")
        return None

    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("ip"):
        print("  ⚠ IPing 查询失败: JSON 无 data.ip 字段")
        return None

    return {
        "ip": data.get("ip"),
        "continent": data.get("continent"),
        "country": data.get("country"),
        "region": data.get("region"),
        "city": data.get("city"),
        "isp": data.get("isp"),
        "is_proxy": data.get("is_proxy"),
        "type": data.get("type"),
        "usage_type": data.get("usage_type"),
        "risk_score": data.get("risk_score"),
        "risk_tag": data.get("risk_tag"),
        "isBroadcast": data.get("isBroadcast"),
        "is_broadcast": data.get("is_broadcast"),
        "asn": data.get("asn"),
        "as_owner": data.get("as_owner"),
        "as_domain": data.get("as_domain"),
        "company": data.get("company"),
    }


def parse_iping_web_risk(html_text):
    marker = "风险等级"
    start = html_text.find(marker)
    if start < 0:
        return None
    chunk = html_text[start:start + 2000]
    percent_match = re.search(r"(\d+(?:\.\d+)?)\s*%", chunk)
    if not percent_match:
        return None

    label_chunk = chunk[percent_match.end():percent_match.end() + 160]
    label_text = html.unescape(re.sub(r"<[^>]+>", " ", label_chunk)).strip()
    label_parts = [part for part in re.split(r"\s+", label_text) if part]
    label = label_parts[0] if label_parts else ""
    return {
        "score": clamp_score(percent_match.group(1)),
        "label": label,
    }


def query_iping_web_risk(ip, retries=2):
    if not IPING_ENABLED:
        return None
    url = IPING_WEB_RISK_URL_TEMPLATE.format(ip=ip)
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            session = requests.Session()
            session.trust_env = False
            resp = session.get(url, headers=WEB_HEADERS, timeout=12)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * attempt)
                continue
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(2 * attempt)
                continue
            print(f"  ⚠ IPing 网页风险查询失败: 请求异常({exc})")
            return None

        if not resp.ok:
            print(f"  ⚠ IPing 网页风险查询失败: HTTP {resp.status_code}")
            return None

        parsed = parse_iping_web_risk(resp.text)
        if not parsed or parsed.get("score") is None:
            print("  ⚠ IPing 网页风险查询失败: 未解析到风险等级")
            return None
        parsed["url"] = url
        return parsed

    if last_exc:
        print(f"  ⚠ IPing 网页风险查询失败: 请求异常({last_exc})")
    return None


def score_iping(info):
    # IPing 的 risk_score 与网页百分比含义不一致，只保存原始值，不参与数值评分。
    return None


def combine_purity_score(ippure_info, iping_info=None, iping_web_risk=None):
    source_scores = {
        "ippure": clamp_score(ippure_info.get("fraud_score")) if ippure_info else None,
        "iping_web": clamp_score(iping_web_risk.get("score")) if iping_web_risk else None,
        "iping": score_iping(iping_info),
        "ipinfo": None,
    }
    available = [score for score in source_scores.values() if score is not None]
    if not available:
        return None, source_scores
    return round(sum(available) / len(available), 1), source_scores


def source_score_summary(row):
    scores = row.get("source_scores") or {}
    parts = []
    if scores.get("ippure") is not None:
        parts.append(f"IPPure {format_score(scores.get('ippure'))}")
    iping_web_score = result_iping_web_risk_score(row)
    if iping_web_score is not None:
        label = row.get("iping_web_risk_label") or ""
        parts.append(f"IPing网页 {format_score(iping_web_score)}% {label}".rstrip())
    iping_raw_risk_score = get_iping_raw_risk_score(row)
    if iping_raw_risk_score not in (None, ""):
        parts.append(f"IPing原始risk_score {iping_raw_risk_score}（不参与评分）")
    if row.get("ipinfo"):
        parts.append("IPInfo 参考")
    return " / ".join(parts) if parts else "无可用来源"


def get_iping_raw_risk_score(row):
    value = row.get("iping_raw_risk_score")
    if value is None and isinstance(row.get("iping"), dict):
        value = row["iping"].get("risk_score")
    return value


def format_bool(value):
    parsed = bool_value(value)
    if parsed is True:
        return "true"
    if parsed is False:
        return "false"
    return "unknown"


def format_bool_cn(value):
    parsed = bool_value(value)
    if parsed is True:
        return "是"
    if parsed is False:
        return "否"
    return "未知"


def get_exit_ip_fallback():
    for url in ("https://api.ip.sb/ip", "https://api.ipify.org"):
        try:
            resp = request_with_retry(
                "GET",
                url,
                proxies=config.PROXIES,
                headers=WEB_HEADERS,
                timeout=10,
                attempts=2,
            )
            if resp.ok and resp.text.strip():
                return resp.text.strip()
        except Exception:
            continue
    return None


def preflight_check():
    print_step("启动自检中...")
    ok = True

    if not config.CLASH_API:
        print("✗ 没有 Clash 外部控制器地址。")
        print("  请确认本机已安装并启动 Clash Verge（会自动探测），")
        print("  或在 local_config.json 的 clash.api / secret 里手动填写。")
        print("-" * CONSOLE_WIDTH)
        return False

    try:
        resp = request_with_retry("GET", f"{config.CLASH_API}/version", headers=config.API_HEADERS, timeout=5)
        if resp.status_code == 401:
            print("✗ API 密钥不对（HTTP 401）")
            print("  自动探测到的密钥与内核不符；可在 local_config.json 的 clash.secret 手动覆盖。")
            ok = False
        elif resp.ok:
            ver = resp.json().get("version", "?")
            print(f"✓ API 连通（{config.CLASH_API}，内核版本 {ver}）")
        else:
            print(f"✗ API 异常响应 HTTP {resp.status_code}")
            ok = False
    except Exception:
        print(f"✗ 连不上 API（{config.CLASH_API or '未探测到'}）")
        print("  请在 Clash Verge 里打开「启用外部控制器」；本工具会自动读取其地址与密钥。")
        print("  若仍失败，可在 local_config.json 的 clash.api / clash.secret 手动填写。")
        ok = False

    if ok:
        try:
            groups = request_with_retry(
                "GET", f"{config.CLASH_API}/proxies", headers=config.API_HEADERS, timeout=5
            ).json().get("proxies", {})
            group = groups.get(config.SELECT_GROUP)
            if not group:
                print(f"✗ 配置里找不到选择组「{config.SELECT_GROUP}」")
                ok = False
            elif group.get("type") != "Selector":
                print(f"✗ 「{config.SELECT_GROUP}」不是可手动切换的 Selector 组")
                ok = False
            else:
                print(f"✓ 选择组就绪（{config.SELECT_GROUP}，{len(group.get('all', []))} 个节点）")
        except Exception as exc:
            print(f"✗ 读取代理组失败: {exc}")
            ok = False

    if ok:
        try:
            resp = request_with_retry(
                "GET",
                "https://api.ip.sb/ip",
                proxies=config.PROXIES,
                headers=WEB_HEADERS,
                timeout=10,
            )
            if resp.ok:
                print(f"✓ 代理出网正常（{config.HTTP_PROXY}，当前出口 {resp.text.strip()}）")
            else:
                print(f"✗ 代理响应异常 HTTP {resp.status_code}")
                ok = False
        except Exception:
            print(f"✗ 走代理出网失败（{config.HTTP_PROXY or '未探测到代理端口'}）")
            print("  请确认 Clash Verge 已启动且能正常访问国外网站；代理端口会自动探测。")
            ok = False

    if ok:
        probe = query_ippure(retries=1)
        if probe:
            print(f"✓ IPPure 可用（当前出口 {probe['ip']}，欺诈分 {probe['fraud_score']}）")
        else:
            print("⚠ IPPure 暂时查询不到（会在正式测试时重试）")
        print("ℹ 综合纯净度来源: IPPure 评分；IPing 做类型/风险标签参考，IPInfo 做 ASN/地区参考")

    print("-" * CONSOLE_WIDTH)
    return ok


def test_node(node, group_map, progress=None):
    name = node.get("name", "Unknown")
    print_section(progress or f"测试节点: {name}")

    if not switch_node(name, group_map.get(name)):
        return {"node": name, "error": "切换失败"}

    print("正在查询 IPPure / IPInfo / IPing...")
    ippure_info = query_ippure()

    if ippure_info:
        ip = ippure_info["ip"]
    else:
        ip = get_exit_ip_fallback()
        if not ip:
            return {"node": name, "error": "IPPure 与 IP 源均不可用"}
        print(f"✓ 出口IP: {ip}（IPPure 无数据）")

    ipinfo = query_ipinfo(ip)
    iping = query_iping(ip)
    iping_web_risk = query_iping_web_risk(ip)
    purity_score, source_scores = combine_purity_score(ippure_info, iping, iping_web_risk)
    iping_type = (iping or {}).get("type")
    usage_type = (iping or {}).get("usage_type")
    iping_raw_risk_score = (iping or {}).get("risk_score")
    iping_web_risk_score = (iping_web_risk or {}).get("score")
    iping_web_risk_label = (iping_web_risk or {}).get("label")
    iping_web_url = (iping_web_risk or {}).get("url")
    is_residential = (ippure_info or {}).get("is_residential")
    is_broadcast = (ippure_info or {}).get("is_broadcast")

    type_parts = [part for part in (iping_type, usage_type) if part]
    if is_residential is True:
        type_parts.append("住宅 🏠")
    elif is_residential is False:
        type_parts.append("机房 🏢")
    if is_broadcast is True:
        type_parts.append("广播")
    type_str = " / ".join(dict.fromkeys(type_parts)) if type_parts else "未知"

    loc = " - ".join(
        x for x in (
            ippure_info.get("country") if ippure_info else None,
            ippure_info.get("region") if ippure_info else None,
            ippure_info.get("city") if ippure_info else None,
        ) if x
    )
    if not loc and iping:
        loc = " - ".join(x for x in (iping.get("country"), iping.get("region"), iping.get("city")) if x)
    if not loc and ipinfo:
        loc = " - ".join(x for x in (ipinfo.get("country"), ipinfo.get("continent")) if x)

    isp = (
        (ippure_info or {}).get("isp")
        or (iping or {}).get("isp")
        or (ipinfo or {}).get("as_name")
    )
    asn = (
        (ippure_info or {}).get("asn")
        or (iping or {}).get("asn")
        or (ipinfo or {}).get("asn")
    )

    print(f"✓ 出口IP: {ip}  ({isp or '?'} / {asn or '?'})")
    print(f"  位置: {loc or '?'}   类型: {type_str}")
    print(
        f"  细节: type={iping_type or '?'}  usage_type={usage_type or '?'}  "
        f"住宅={format_bool_cn(is_residential)}  广播={format_bool_cn(is_broadcast)}  "
        f"IPing原始risk_score={iping_raw_risk_score if iping_raw_risk_score not in (None, '') else '?'}（不参与评分）"
    )
    if source_scores.get("ippure") is not None:
        print(f"  IPPure: {format_score(source_scores['ippure'])}")
    if source_scores.get("iping_web") is not None:
        print(
            f"  IPing网页风险: {format_score(source_scores['iping_web'])}% "
            f"{iping_web_risk_label or ''}"
        )
    if ipinfo:
        print(f"  IPInfo: {ipinfo.get('as_name') or '?'} / {ipinfo.get('country') or '?'}")
    if purity_score is not None:
        print(f"✓ 综合纯净度分: {format_score(purity_score)} ({score_status(purity_score)})")
    else:
        print("⚠ 未返回任何可用评分")

    row = {
        "node": name,
        "exit_ip": ip,
        "purity_score": purity_score,
        "fraud_score": purity_score,
        "ippure_score": source_scores.get("ippure"),
        "iping_web_risk_score": iping_web_risk_score,
        "iping_web_risk_label": iping_web_risk_label,
        "iping_web_url": iping_web_url,
        "iping_raw_risk_score": iping_raw_risk_score,
        "source_scores": source_scores,
        "ippure": ippure_info,
        "ipinfo": ipinfo,
        "iping": iping,
        "isp": isp,
        "asn": asn,
        "type": iping_type or type_str,
        "usage_type": usage_type,
        "isResidential": is_residential,
        "isBroadcast": is_broadcast,
        "display_type": type_str,
        "location": loc,
        "ippure_url": IPPURE_URL,
        "abuse_url": f"https://www.abuseipdb.com/check/{ip}",
    }
    row["ip_quality"] = ip_quality_label(row)
    return row


def sorted_results(results):
    return sorted(results, key=result_sort_key)


def build_result_payload(results):
    ranked = sorted_results(results)
    scored = [
        row for row in ranked
        if is_rankable_result(row)
    ]
    by_region = {}
    for row in ranked:
        by_region.setdefault(region_of(row.get("node", "")), []).append(row)
    return {
        "top10_lowest_risk": scored[:10],
        "by_region": by_region,
        "all_ranked": ranked,
    }


def row_quality(row):
    """结果质量：2=有综合分，1=有出口 IP 无分，0=完全失败。补测时只用更好的结果覆盖。"""
    if result_score(row) is not None:
        return 2
    if "error" not in row:
        return 1
    return 0


def run_main_pass(selected, group_map, results):
    """主测试循环；结果逐个追加进 results，中断时已测部分得以保留。"""
    interval = BASE_INTERVAL
    total = len(selected)
    for index, node in enumerate(selected):
        name = node.get("name", "Unknown")
        row = test_node(
            node,
            group_map,
            progress=f"{progress_label('主测', index + 1, total)} {name}",
        )
        row["tested_at"] = now_str()
        results.append(row)
        if index == len(selected) - 1:
            break
        if result_score(row) is not None:
            interval = max(BASE_INTERVAL, interval * 0.5)
        elif row.get("error") != "切换失败":
            # 多源都没给出分数时，疑似限流/挑战页，放缓节奏
            interval = min(MAX_INTERVAL, interval * 2)
        LOG.debug("inter-node interval: %.1fs", interval)
        time.sleep(interval)


def retest_failed_nodes(results, nodes_by_name, group_map):
    """对无分/失败节点自动补测一轮，避免因偶发限流整轮重跑。"""
    targets = [
        row["node"] for row in results
        if row_quality(row) < 2 and row["node"] in nodes_by_name
    ]
    if not targets:
        return

    print_step(
        f"⟳ 自动补测 {len(targets)} 个无综合分/失败节点（间隔 {RETEST_INTERVAL:.0f}s）:\n"
        f"  {', '.join(targets)}"
    )

    indexed = {row["node"]: i for i, row in enumerate(results)}
    total = len(targets)
    for index, name in enumerate(targets, 1):
        time.sleep(RETEST_INTERVAL)
        row = test_node(
            nodes_by_name[name],
            group_map,
            progress=f"{progress_label('补测', index, total)} {name}",
        )
        row["tested_at"] = now_str()
        if row_quality(row) > row_quality(results[indexed[name]]):
            results[indexed[name]] = row
            print(f"  ✓ 补测成功，已更新「{name}」的结果")
        else:
            print(f"  ⚠ 补测仍未取得更好结果，保留「{name}」原记录")


def save_results(results, regions, partial=False):
    """合并旧结果后写入 RESULT_FILE，返回最终 payload。

    正常完成：替换本次已测地区的全部旧条目，保留其他地区旧数据。
    中断（partial）：只覆盖实际测到的节点，其余旧数据全部保留。
    """
    tested_names = {row["node"] for row in results}
    allowed_regions = set(TARGET_REGIONS) | set(IMPORT_REGIONS) | set(regions)
    kept = []
    dropped_disabled_regions = set()
    if os.path.exists(RESULT_FILE):
        try:
            old_rows = load_all_results(RESULT_FILE)
        except Exception as exc:
            print(f"⚠ 旧结果文件读取失败，将只保存本次结果: {exc}")
            old_rows = []
        for row in old_rows:
            if row.get("node") in tested_names:
                continue
            row_region = region_of(row.get("node", ""))
            if row_region not in allowed_regions:
                dropped_disabled_regions.add(row_region)
                continue
            if not partial and row_region in regions:
                continue
            kept.append(row)
        if kept:
            kept_regions = sorted({region_of(row.get("node", "")) for row in kept})
            print(f"\n（保留未重测的旧结果: {'、'.join(kept_regions)}）")
        if dropped_disabled_regions:
            print(f"\n（已移除禁用地区旧结果: {'、'.join(sorted(dropped_disabled_regions))}）")

    output = {"tested_at": now_str(), "tested_regions": list(regions)}
    if partial:
        output["partial"] = True
    output.update(build_result_payload(results + kept))
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return output


def print_test_summary(output):
    ranked = output["all_ranked"]
    scored = output.get("top10_lowest_risk") or []
    by_region = output.get("by_region") or {}

    print_section("测试完成！")
    print(f"\n结果已保存: {RESULT_FILE}\n")

    print("汇总（先按 IP 类型优先级，再按综合分；越靠前越优先）:")
    display_rank = 0
    for row in ranked:
        node = row["node"]
        if "error" in row:
            print(f"  --. {node:14s} {'-':18s} {row['error']}")
            continue
        ip = row.get("exit_ip", "N/A")
        score = result_score(row)
        if score is not None:
            rankable = is_rankable_result(row)
            rank_prefix = " --." if not rankable else None
            if rankable:
                display_rank += 1
                rank_prefix = f" {display_rank:2d}."
            iping_web_score = result_iping_web_risk_score(row)
            rank_note = "不参与排名" if not rankable else ip_quality_label(row)
            detail = (
                f"type={row.get('type') or '?'}  "
                f"usage={row.get('usage_type') or '?'}  "
                f"住宅={format_bool_cn(row.get('isResidential'))}  "
                f"广播={format_bool_cn(row.get('isBroadcast'))}  "
                f"IPing网页风险={format_score(iping_web_score) if iping_web_score is not None else '?'}%  "
                f"IPing原始risk_score={get_iping_raw_risk_score(row) or '?'}（不参与评分）"
            )
            print(
                f" {rank_prefix} {node:14s} {ip:18s} "
                f"综合分 {format_score(score):>5s}  {score_status(score):8s}  "
                f"{rank_note}  {detail}"
            )
        else:
            print(f"  --. {node:14s} {ip:18s} 无法获取评分")

    print("\n🏆 各国家综合最优 TOP 5:")
    any_region_top = False
    ordered_regions = [region for region in ALL_REGIONS if region in by_region]
    ordered_regions.extend(region for region in by_region if region not in ordered_regions)
    for region in ordered_regions:
        region_rows = [
            row for row in by_region.get(region, [])
            if is_rankable_result(row)
        ][:5]
        if not region_rows:
            continue
        any_region_top = True
        print(f"\n{region}:")
        for rank, row in enumerate(region_rows, 1):
            score = result_score(row)
            print(
                f"  {rank}. {row['node']:14s} {row.get('exit_ip', 'N/A'):18s} "
                f"综合分 {format_score(score):>5s}  {ip_quality_label(row)}  {row.get('location', '?')}"
            )

    if not any_region_top:
        print("  无可用评分节点。")

    if scored:
        print("\n详细复核（全局综合最优 TOP 10）:")
        for row in scored[:10]:
            print(f"\n{row['node']}  ({row.get('isp', '?')} / {row.get('location', '?')}):")
            print(f"  来源: {source_score_summary(row)}")
            print(
                f"  IP 类型: type={row.get('type') or '?'}  "
                f"usage_type={row.get('usage_type') or '?'}  "
                f"住宅={format_bool_cn(row.get('isResidential'))}  "
                f"广播={format_bool_cn(row.get('isBroadcast'))}  "
                f"IPing网页风险={format_score(result_iping_web_risk_score(row)) if result_iping_web_risk_score(row) is not None else '?'}%  "
                f"IPing原始risk_score={get_iping_raw_risk_score(row) or '?'}（不参与评分）  "
                f"优先级={ip_quality_label(row)}"
            )
            print(f"  {row['abuse_url']}")


def select_nodes_for_regions(nodes, regions):
    return [node for node in nodes if region_of(node.get("name", "")) in regions]


def print_selected_node_summary(selected, regions):
    region_count = {}
    for node in selected:
        region = region_of(node.get("name", ""))
        region_count[region] = region_count.get(region, 0) + 1

    print(f"\n共 {len(selected)} 个节点待测试:")
    for region in regions:
        if region in region_count:
            print(f"  {region}: {region_count[region]} 个")


def execute_selected_node_tests(selected, group_map, original_selection):
    results = []
    interrupted = False
    try:
        run_main_pass(selected, group_map, results)
        retest_failed_nodes(
            results, {node.get("name", ""): node for node in selected}, group_map
        )
    except KeyboardInterrupt:
        interrupted = True
        print("\n⚠ 测试被中断，正在保存已完成的部分...")
    finally:
        restore_selection(original_selection)
    return results, interrupted


def run_test(regions, assume_yes=False):
    if not config.CONFIG_FILE:
        print("✗ 没有可用的节点源。请确认 Clash Verge 已启动（工具会自动读取当前 profile），")
        print("  或在 local_config.json 的 paths.config_file 指定一个节点源 YAML。")
        return False
    if not require_files(config.CONFIG_FILE):
        return False

    print_section("节点纯净度自动化测试（IPPure + IPing + IPInfo）")
    print("\n前提条件:")
    print("1. Clash Verge 已启动并开启「外部控制器」")
    print(f"2. 代理端口与 API 可访问（当前: 代理 {config.HTTP_PROXY or '未探测到'}，API {config.CLASH_API or '未探测到'}）")
    print("3. 确保能正常访问 IPPure、IPInfo、IPing")
    print(f"\n节点源: {config.CONFIG_FILE}")
    print(f"本次将测试地区: {', '.join(regions)}")
    if not assume_yes:
        input("\n按回车开始测试...")

    if not preflight_check():
        print("\n✗ 自检未通过，请按上面提示修正配置后重试。")
        return False

    nodes = load_nodes(config.CONFIG_FILE)
    selected = select_nodes_for_regions(nodes, regions)

    if not selected:
        print("✗ 未匹配到任何目标地区的节点，请检查 --regions 与配置")
        return False

    print_selected_node_summary(selected, regions)

    selected_names = [node.get("name", "") for node in selected]
    group_map, original_selection = build_group_map(selected_names)
    if group_map is None:
        return False
    if not group_map:
        print("✗ 没有任何待测节点出现在可切换的 Selector 组里，请检查 Clash 配置。")
        return False
    missing = [name for name in selected_names if name not in group_map]
    if missing:
        preview = "、".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        print(f"⚠ {len(missing)} 个节点不在任何 Selector 组中，将标记为切换失败: {preview}")

    print("\n开始测试...")
    results, interrupted = execute_selected_node_tests(
        selected, group_map, original_selection
    )

    if interrupted and not results:
        print("尚无任何完成的节点，未写入结果文件。")
        raise KeyboardInterrupt

    output = save_results(results, regions, partial=interrupted)

    if interrupted:
        print(f"⚠ 已保存 {len(results)} 个节点的部分结果（文件已标记 partial）。")
        raise KeyboardInterrupt

    print_test_summary(output)
    return True
