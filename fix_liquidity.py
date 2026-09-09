import json
import time
from pathlib import Path
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "dashboard.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def safe_float(x):
    try:
        if pd.isna(x):
            return None
        return float(str(x).replace(",", "").strip())
    except Exception:
        return None


def date_key(x):
    s = str(x).strip().replace("/", "-")
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return None


def recent_weekdays(n=50):
    d = datetime.now().date()
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def retry_call(fn, label, attempts=4, base_sleep=1.2):
    last = None
    for i in range(attempts):
        try:
            value = fn()
            if value is None:
                raise RuntimeError("empty response")
            return value
        except Exception as exc:
            last = exc
            print(f"{label} attempt {i+1}/{attempts} failed: {exc}")
            if i + 1 < attempts:
                time.sleep(base_sleep * (i + 1))
    raise last


def fetch_all_a_margin(start_date: str, end_date: str):
    """全A两融余额：上交所 + 深交所 + 北交所，统一为亿元。"""
    sh = retry_call(lambda: ak.stock_margin_sse(start_date=start_date, end_date=end_date), "SSE margin")
    if sh is None or sh.empty:
        raise RuntimeError("SSE margin data is empty")

    sh_map = {}
    for _, row in sh.iterrows():
        d = date_key(row.get("信用交易日期"))
        v = safe_float(row.get("融资融券余额"))
        if d and v is not None:
            sh_map[d] = v / 1e8

    result = []
    for d in sorted(sh_map.keys()):
        ds = d.replace("-", "")
        try:
            sz = retry_call(lambda ds=ds: ak.stock_margin_szse(date=ds), f"SZSE margin {ds}", attempts=3, base_sleep=.8)
            bj = retry_call(lambda ds=ds: ak.stock_margin_bse(date=ds), f"BSE margin {ds}", attempts=3, base_sleep=.8)
            if sz is None or sz.empty or bj is None or bj.empty:
                continue
            sz_bal = safe_float(sz.iloc[0].get("融资融券余额"))
            bj_bal = safe_float(bj.iloc[0].get("融资融券余额"))
            if sz_bal is None or bj_bal is None:
                continue
            total = sh_map[d] + sz_bal + bj_bal / 10000.0
            result.append({"date": d, "margin_balance": round(total, 2)})
        except Exception as exc:
            print("margin skip", d, exc)

    if not result:
        raise RuntimeError("No complete SSE+SZSE+BSE margin observations")
    return result[-30:]


def fetch_hs_turnover_official():
    """沪深A股成交额：交易所官方每日市场汇总，统一为亿元。"""
    rows = []
    for d in recent_weekdays(50):
        ds = d.strftime("%Y%m%d")
        try:
            sse = retry_call(lambda ds=ds: ak.stock_sse_deal_daily(date=ds), f"SSE turnover {ds}", attempts=2, base_sleep=.35)
            if sse is None or sse.empty:
                continue
            hit = sse[sse["单日情况"].astype(str).str.contains("成交金额", na=False)]
            if hit.empty:
                continue
            rr = hit.iloc[0]
            sh_amt = (safe_float(rr.get("主板A")) or 0) + (safe_float(rr.get("科创板")) or 0)

            sz = retry_call(lambda ds=ds: ak.stock_szse_summary(date=ds), f"SZSE turnover {ds}", attempts=2, base_sleep=.35)
            if sz is None or sz.empty:
                continue
            sz_amt = 0.0
            for category in ("主板A股", "创业板A股"):
                z = sz[sz["证券类别"].astype(str) == category]
                if not z.empty:
                    v = safe_float(z.iloc[0].get("成交金额"))
                    if v is not None:
                        sz_amt += v / 1e8

            if sh_amt > 0 and sz_amt > 0:
                rows.append({"date": d.isoformat(), "turnover": round(sh_amt + sz_amt, 2)})
        except Exception as exc:
            print("turnover official skip", ds, exc)

    if not rows:
        raise RuntimeError("No official SSE+SZSE turnover observations")
    return rows[-30:]


def eastmoney_index_amount(secid: str, start_date: str, end_date: str):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid, "klt": 101, "fqt": 0,
        "beg": start_date, "end": end_date,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "7eea3edcaed734bea9cbfc24409ed989"
    }
    def _fetch():
        r = requests.get(url, params=params, headers=UA, timeout=(8, 20))
        r.raise_for_status()
        j = r.json()
        lines = (j.get("data") or {}).get("klines") or []
        if not lines:
            raise RuntimeError("no kline rows")
        return lines
    lines = retry_call(_fetch, f"Eastmoney index amount {secid}", attempts=4)
    out = {}
    for line in lines:
        a = line.split(",")
        if len(a) < 7:
            continue
        d = a[0]
        amount = safe_float(a[6])
        if d and amount is not None and amount > 0:
            out[d] = amount / 1e8
    return out


def fetch_hs_turnover_fallback(start_date: str, end_date: str):
    """备用：上证A股指数(000002)+深证A股指数(399107)的成交额，统一为亿元。"""
    sh = eastmoney_index_amount("1.000002", start_date, end_date)
    sz = eastmoney_index_amount("0.399107", start_date, end_date)
    common = sorted(set(sh) & set(sz))
    rows = [{"date": d, "turnover": round(sh[d] + sz[d], 2)} for d in common if sh[d] > 0 and sz[d] > 0]
    if not rows:
        raise RuntimeError("No Eastmoney A-share index turnover observations")
    return rows[-30:]


def fetch_hs_turnover(start_date: str, end_date: str):
    try:
        return fetch_hs_turnover_official(), "交易所官方成交额"
    except Exception as exc:
        print("Official turnover failed, switching to A-share index amount fallback:", exc)
        return fetch_hs_turnover_fallback(start_date, end_date), "东方财富上证A股指数+深证A股指数成交额备用源"


def fetch_sh_index(start_date: str, end_date: str):
    """上证指数（000001）日收盘点位，保留最近30个交易日。"""
    df = None
    try:
        df = retry_call(lambda: ak.stock_zh_index_daily_em(symbol="sh000001", start_date=start_date, end_date=end_date), "Shanghai Composite EM", attempts=3)
    except Exception as exc:
        print("Shanghai Composite EM source failed, fallback to Sina:", exc)

    if df is None or df.empty:
        df = retry_call(lambda: ak.stock_zh_index_daily(symbol="sh000001"), "Shanghai Composite Sina", attempts=3)

    if df is None or df.empty:
        raise RuntimeError("Shanghai Composite data is empty")

    date_col = next((c for c in df.columns if str(c).lower() == "date" or str(c) == "日期"), None)
    close_col = next((c for c in df.columns if str(c).lower() == "close" or str(c) == "收盘"), None)
    if date_col is None or close_col is None:
        raise RuntimeError(f"Unexpected Shanghai Composite columns: {list(df.columns)}")

    rows = []
    for _, row in df.iterrows():
        d = date_key(row.get(date_col))
        v = safe_float(row.get(close_col))
        if d and v is not None:
            rows.append({"date": d, "sh_close": round(v, 2)})

    rows = sorted(rows, key=lambda x: x["date"])
    if not rows:
        raise RuntimeError("No Shanghai Composite observations")
    return rows[-30:]


def main():
    data = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
    old = data.get("liquidity", {})
    today = datetime.now().date()
    start = (today - timedelta(days=80)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    warnings = []

    try:
        margin_series = fetch_all_a_margin(start, end)
    except Exception as exc:
        print("margin update failed, keep previous series:", exc)
        margin_series = old.get("margin_series") or [
            {"date": x["date"], "margin_balance": x["margin_balance"]}
            for x in old.get("series", []) if x.get("margin_balance") is not None
        ]
        warnings.append("两融接口本次失败，沿用上一成功数据并保留真实数据日期")

    try:
        turnover_series, turnover_source_short = fetch_hs_turnover(start, end)
    except Exception as exc:
        print("turnover update failed, keep previous series:", exc)
        turnover_series = old.get("turnover_series") or [
            {"date": x["date"], "turnover": x["turnover"]}
            for x in old.get("series", []) if x.get("turnover") is not None
        ]
        turnover_source_short = "上一成功数据"
        warnings.append("成交额主源和备用源均失败，沿用上一成功数据并保留真实数据日期")

    try:
        sh_index_series = fetch_sh_index(start, end)
    except Exception as exc:
        print("Shanghai Composite update failed, keep previous series:", exc)
        sh_index_series = old.get("sh_index_series", [])
        warnings.append("上证指数主源和备用源均失败，沿用上一成功数据并保留真实数据日期")

    if not margin_series or not turnover_series or not sh_index_series:
        raise RuntimeError("Liquidity data unavailable and no previous successful series exists")

    mm = {x["date"]: x["margin_balance"] for x in margin_series}
    tm = {x["date"]: x["turnover"] for x in turnover_series}
    common = sorted(set(mm) & set(tm))[-30:]
    legacy_series = [{"date": d, "margin_balance": mm[d], "turnover": tm[d]} for d in common]

    high_30d = {
        "margin_balance": max(margin_series, key=lambda x: x["margin_balance"]),
        "turnover": max(turnover_series, key=lambda x: x["turnover"]),
        "sh_index": max(sh_index_series, key=lambda x: x["sh_close"]),
    }

    freshness = {
        "margin_as_of": margin_series[-1]["date"],
        "turnover_as_of": turnover_series[-1]["date"],
        "sh_index_as_of": sh_index_series[-1]["date"],
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    data["liquidity"] = {
        "status": "多源校验更新正常" if not warnings else "；".join(warnings),
        "freshness": freshness,
        "margin_series": margin_series[-30:],
        "turnover_series": turnover_series[-30:],
        "sh_index_series": sh_index_series[-30:],
        "series": legacy_series,
        "high_30d": high_30d,
        "margin_source": "上交所融资融券汇总 + 深交所融资融券汇总 + 北交所融资融券汇总（全A，统一为亿元；失败时保留上一成功值和实际日期）",
        "turnover_source": "优先上交所每日股票成交概况 + 深交所证券类别统计；失败自动切换东方财富上证A股指数(000002)+深证A股指数(399107)成交额，当前：" + turnover_source_short,
        "sh_index_source": "上证指数（000001）日收盘；东方财富主源，新浪备用源",
        "source": "每个序列独立记录真实数据日期；页面更新时间不再代表所有序列均更新至同一天",
    }
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
