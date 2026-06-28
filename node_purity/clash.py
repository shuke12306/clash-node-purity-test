from .config import *


def request_with_retry(
    method,
    url,
    *,
    attempts=3,
    retry_statuses=(429, 500, 502, 503, 504),
    backoff=2,
    timeout=None,
    **kwargs,
):
    method = method.upper()
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            LOG.debug("HTTP %s %s attempt %s/%s", method, url, attempt, attempts)
            resp = requests.request(method, url, timeout=timeout, **kwargs)
            LOG.debug("HTTP %s %s -> %s", method, url, resp.status_code)
            if resp.status_code in retry_statuses and attempt < attempts:
                wait = backoff * attempt
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait = max(wait, int(retry_after))
                LOG.warning(
                    "HTTP %s %s returned %s; retrying in %ss",
                    method,
                    url,
                    resp.status_code,
                    wait,
                )
                time.sleep(wait)
                continue
            return resp
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            if attempt >= attempts:
                LOG.warning("HTTP %s %s failed after %s attempts: %s", method, url, attempts, exc)
                raise
            wait = backoff * attempt
            LOG.warning("HTTP %s %s failed: %s; retrying in %ss", method, url, exc, wait)
            time.sleep(wait)
        except requests.exceptions.RequestException:
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError(f"HTTP {method} {url} failed without response")


def load_all_results(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        if "all_ranked" in data:
            return data["all_ranked"]
        if "by_region" in data:
            out = []
            for rows in data["by_region"].values():
                out.extend(rows)
            return out
        if "top10_lowest_risk" in data:
            return data["top10_lowest_risk"]
        return []
    return data


def numeric_score(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def result_score(row):
    score = row.get("purity_score")
    if score is None:
        score = row.get("fraud_score")
    return numeric_score(score)


def result_ippure_score(row):
    scores = row.get("source_scores") if isinstance(row.get("source_scores"), dict) else {}
    value = row.get("ippure_score")
    if value is None:
        value = scores.get("ippure")
    ippure = row.get("ippure") if isinstance(row.get("ippure"), dict) else {}
    if value is None:
        value = ippure.get("fraud_score")
    return numeric_score(value)


def result_iping_web_risk_score(row):
    scores = row.get("source_scores") if isinstance(row.get("source_scores"), dict) else {}
    value = row.get("iping_web_risk_score")
    if value is None:
        value = scores.get("iping_web")
    return numeric_score(value)


def format_score(score):
    value = numeric_score(score)
    if value is None:
        return "N/A"
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def bool_value(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y", "是"):
        return True
    if text in ("false", "0", "no", "n", "否"):
        return False
    return None


def row_type_text(row):
    iping = row.get("iping") if isinstance(row.get("iping"), dict) else {}
    return " ".join(
        str(value)
        for value in (
            row.get("type"),
            row.get("usage_type"),
            row.get("display_type"),
            iping.get("type"),
            iping.get("usage_type"),
        )
        if value
    )


def row_is_proxy(row):
    iping = row.get("iping") if isinstance(row.get("iping"), dict) else {}
    for value in (
        row.get("is_proxy"),
        row.get("isProxy"),
        iping.get("is_proxy"),
        iping.get("isProxy"),
    ):
        parsed = bool_value(value)
        if parsed is not None:
            return parsed
    return None


def row_risk_tag(row):
    iping = row.get("iping") if isinstance(row.get("iping"), dict) else {}
    for value in (row.get("risk_tag"), iping.get("risk_tag")):
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def row_is_residential(row):
    ippure = row.get("ippure") if isinstance(row.get("ippure"), dict) else {}
    for value in (
        row.get("isResidential"),
        row.get("is_residential"),
        ippure.get("isResidential"),
        ippure.get("is_residential"),
    ):
        parsed = bool_value(value)
        if parsed is not None:
            return parsed
    return None


def row_is_broadcast(row):
    ippure = row.get("ippure") if isinstance(row.get("ippure"), dict) else {}
    iping = row.get("iping") if isinstance(row.get("iping"), dict) else {}
    for value in (
        row.get("isBroadcast"),
        row.get("is_broadcast"),
        ippure.get("isBroadcast"),
        ippure.get("is_broadcast"),
        iping.get("isBroadcast"),
        iping.get("is_broadcast"),
    ):
        parsed = bool_value(value)
        if parsed is not None:
            return parsed
    return None


def ip_quality_rank(row):
    text = row_type_text(row)
    is_residential = row_is_residential(row)
    is_broadcast = row_is_broadcast(row)
    is_proxy = row_is_proxy(row)
    risk_tag = row_risk_tag(row)

    if (
        is_proxy is True
        or risk_tag
        or is_broadcast is True
        or any(word in text for word in ("广播", "代理", "VPN", "欺诈", "风险"))
    ):
        return 4
    if is_residential is True or "住宅" in text:
        return 0
    if "原生" in text:
        return 1
    if (
        is_residential is False
        or any(word in text for word in ("数据中心", "机房", "托管", "IDC", "Hosting", "hosting"))
    ):
        return 3
    return 2


def ip_quality_label(row):
    labels = {
        0: "住宅优先",
        1: "原生优先",
        2: "普通/未知",
        3: "机房/数据中心",
        4: "广播/高风险",
    }
    return labels.get(ip_quality_rank(row), "普通/未知")


def is_rankable_score(score):
    score = numeric_score(score)
    return score is not None and score <= 50


def is_rankable_result(row):
    return "error" not in row and is_rankable_score(result_score(row))


def result_sort_key(row):
    if "error" in row:
        return (3, 99, 0)
    score = result_score(row)
    if score is None:
        return (2, ip_quality_rank(row), 0)
    if not is_rankable_result(row):
        return (1, ip_quality_rank(row), score)
    return (0, ip_quality_rank(row), score)


def lowest_risk_node(results, region):
    cands = []
    for row in results:
        name = row.get("node", "")
        if region_of(name) != region or "error" in row:
            continue
        score = result_score(row)
        if not is_rankable_result(row):
            continue
        cands.append((ip_quality_rank(row), score, name))
    if not cands:
        return None
    cands.sort(key=lambda item: (item[0], item[1]))
    _, score, name = cands[0]
    return (score, name)


def pick_best_per_region(results, regions, verbose=True):
    picks = {}
    if verbose:
        print("各地区最低综合风险节点:")
    for region in regions:
        best = lowest_risk_node(results, region)
        if best is None:
            if verbose:
                print(f"  {region}: ⚠ 无可排名节点，跳过")
            continue
        picks[region] = best
        if verbose:
            best_row = next((row for row in results if row.get("node") == best[1]), {})
            print(
                f"  {region}: {best[1]}（综合分 {format_score(best[0])}，"
                f"{ip_quality_label(best_row)}）"
            )
    return picks


def describe_result_age():
    """打印结果文件的生成时间与 partial 提示，帮助判断数据新旧。"""
    try:
        with open(RESULT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    if not isinstance(data, dict):
        print("ℹ 结果文件为旧版扁平格式（无时间戳），建议重跑一次 test 升级格式。")
        return
    if data.get("partial"):
        print("⚠ 结果来自一次被中断的测试（partial），数据可能不完整。")
    tested_at = data.get("tested_at")
    if not tested_at:
        print("ℹ 结果文件无时间戳（旧版生成），无法判断新旧。")
        return
    try:
        age = datetime.now() - datetime.fromisoformat(tested_at)
    except ValueError:
        return
    hours = age.total_seconds() / 3600
    if hours < 1:
        desc = f"{int(age.total_seconds() // 60)} 分钟前"
    elif hours < 24:
        desc = f"{hours:.1f} 小时前"
    else:
        desc = f"{age.days} 天前"
    print(f"ℹ 测试结果生成于 {desc}（{tested_at}）")
    if age.days >= 3:
        print("⚠ 结果已超过 3 天，建议先重新跑一次 test 再更新。")


def score_status(score):
    score = numeric_score(score)
    if score is None:
        return "无评分"
    if score <= 25:
        return "低风险 ✅"
    if score <= 50:
        return "中风险 🟡"
    if score <= 75:
        return "高风险 ⚠️"
    return "极高风险 ‼️"
