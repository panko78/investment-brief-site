import json
from pathlib import Path
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "dashboard.json"


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


def fetch_all_a_margin(start_date: str, end_date: str):
    """全A两融余额：上交所 + 深交所 + 北交所，统一为亿元。"""
    sh = ak.stock_margin_sse(start_date=start_date, end_date=end_date)
    if sh is None or sh.empty:
        raise RuntimeError("SSE margin data is empty")

    sh_map = {}
    for _, row in sh.iterrows():
        d = date_key(row.get("信用交易日期"))
        v = safe_float(row.get("融资融券余额"))
        if d and v is not None:
            sh_map[d] = v / 1e8  # 上交所：元 -> 亿元

    result = []
    for d in sorted(sh_map.keys()):
        ds = d.replace("-", "")
        try:
            sz = ak.stock_margin_szse(date=ds)
            bj = ak.stock_margin_bse(date=ds)
            if sz is None or sz.empty or bj is None or bj.empty:
                continue

            sz_bal = safe_float(sz.iloc[0].get("融资融券余额"))  # 深交所：亿元
            bj_bal = safe_float(bj.iloc[0].get("融资融券余额"))  # 北交所：万元
            if sz_bal is None or bj_bal is None:
                continue

            total = sh_map[d] + sz_bal + bj_bal / 10000.0
            result.append({"date": d, "margin_balance": round(total, 2)})
        except Exception as exc:
            print("margin skip", d, exc)

    if not result:
        raise RuntimeError("No complete SSE+SZSE+BSE margin observations")
    return result[-30:]


def fetch_hs_turnover():
    """沪深A股成交额：交易所官方每日市场汇总，统一为亿元。"""
    rows = []
    for d in recent_weekdays(50):
        ds = d.strftime("%Y%m%d")
        try:
            sse = ak.stock_sse_deal_daily(date=ds)
            if sse is None or sse.empty:
                continue
            hit = sse[sse["单日情况"].astype(str).str.contains("成交金额", na=False)]
            if hit.empty:
                continue
            rr = hit.iloc[0]
            # 上交所每日概况中的成交金额已经是亿元；只取主板A+科创板，排除B股、回购等。
            sh_amt = (safe_float(rr.get("主板A")) or 0) + (safe_float(rr.get("科创板")) or 0)

            sz = ak.stock_szse_summary(date=ds)
            if sz is None or sz.empty:
                continue
            sz_amt = 0.0
            for category in ("主板A股", "创业板A股"):
                z = sz[sz["证券类别"].astype(str) == category]
                if not z.empty:
                    v = safe_float(z.iloc[0].get("成交金额"))
                    if v is not None:
                        sz_amt += v / 1e8  # 深交所：元 -> 亿元

            if sh_amt > 0 and sz_amt > 0:
                rows.append({"date": d.isoformat(), "turnover": round(sh_amt + sz_amt, 2)})
        except Exception as exc:
            print("turnover skip", ds, exc)

    if not rows:
        raise RuntimeError("No official SSE+SZSE turnover observations")
    return rows[-30:]


def fetch_sh_index(start_date: str, end_date: str):
    """上证指数（000001）日收盘点位，保留最近30个交易日。"""
    df = None
    try:
        df = ak.stock_zh_index_daily_em(
            symbol="sh000001", start_date=start_date, end_date=end_date
        )
    except Exception as exc:
        print("Shanghai Composite EM source failed, fallback to Sina:", exc)

    if df is None or df.empty:
        df = ak.stock_zh_index_daily(symbol="sh000001")

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
    today = datetime.now().date()
    start = (today - timedelta(days=80)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    margin_series = fetch_all_a_margin(start, end)
    turnover_series = fetch_hs_turnover()
    sh_index_series = fetch_sh_index(start, end)

    mm = {x["date"]: x["margin_balance"] for x in margin_series}
    tm = {x["date"]: x["turnover"] for x in turnover_series}
    common = sorted(set(mm) & set(tm))[-30:]
    legacy_series = [
        {"date": d, "margin_balance": mm[d], "turnover": tm[d]}
        for d in common
    ]

    data["liquidity"] = {
        "status": "已按交易所官方口径校正",
        "margin_series": margin_series,
        "turnover_series": turnover_series,
        "sh_index_series": sh_index_series,
        "series": legacy_series,
        "high_30d": {
            "margin_balance": max(margin_series, key=lambda x: x["margin_balance"]),
            "turnover": max(turnover_series, key=lambda x: x["turnover"]),
            "sh_index": max(sh_index_series, key=lambda x: x["sh_close"]),
        },
        "margin_source": "上交所融资融券汇总 + 深交所融资融券汇总 + 北交所融资融券汇总（全A，统一为亿元）",
        "turnover_source": "上交所每日股票成交概况（主板A+科创板） + 深交所证券类别统计（主板A股+创业板A股），统一为亿元",
        "sh_index_source": "上证指数（000001）日收盘点位，AKShare公开指数行情接口",
        "source": "两融余额为沪深京三所合计；成交额为沪深A股交易所官方汇总口径；上证指数为日收盘点位",
    }
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
