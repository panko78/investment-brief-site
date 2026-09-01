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
            sh_map[d] = v / 1e8  # 元 -> 亿元

    result = []
    for d in sorted(sh_map.keys()):
        ds = d.replace("-", "")
        try:
            sz = ak.stock_margin_szse(date=ds)
            bj = ak.stock_margin_bse(date=ds)
            if sz is None or sz.empty or bj is None or bj.empty:
                continue

            sz_bal = safe_float(sz.iloc[0].get("融资融券余额"))  # 亿元
            bj_bal = safe_float(bj.iloc[0].get("融资融券余额"))  # 万元
            if sz_bal is None or bj_bal is None:
                continue

            total = sh_map[d] + sz_bal + bj_bal / 10000.0
            result.append({"date": d, "margin_balance": round(total, 2)})
        except Exception as exc:
            print("margin skip", d, exc)

    if not result:
        raise RuntimeError("No complete SSE+SZSE+BSE margin observations")
    return result[-30:]


def index_amount_map(symbol: str, start_date: str, end_date: str):
    df = ak.stock_zh_index_daily_em(symbol=symbol, start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        raise RuntimeError(f"Index history empty: {symbol}")
    out = {}
    for _, row in df.iterrows():
        d = date_key(row.get("date"))
        amount = safe_float(row.get("amount"))
        if d and amount is not None:
            out[d] = amount / 1e8  # 元 -> 亿元
    return out


def fetch_hs_turnover(start_date: str, end_date: str):
    """沪深两市成交额：上证指数 amount + 深证成指 amount，匹配市场常用两市成交额口径。"""
    sh = index_amount_map("sh000001", start_date, end_date)
    sz = index_amount_map("sz399001", start_date, end_date)
    rows = []
    for d in sorted(set(sh) & set(sz)):
        rows.append({"date": d, "turnover": round(sh[d] + sz[d], 2)})
    if not rows:
        raise RuntimeError("No SH+SZ turnover observations")
    return rows[-30:]


def main():
    data = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
    today = datetime.now().date()
    start = (today - timedelta(days=75)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    margin_series = fetch_all_a_margin(start, end)
    turnover_series = fetch_hs_turnover(start, end)

    mm = {x["date"]: x["margin_balance"] for x in margin_series}
    tm = {x["date"]: x["turnover"] for x in turnover_series}
    common = sorted(set(mm) & set(tm))[-30:]
    legacy_series = [
        {"date": d, "margin_balance": mm[d], "turnover": tm[d]}
        for d in common
    ]

    data["liquidity"] = {
        "status": "已按统一口径校正",
        "margin_series": margin_series,
        "turnover_series": turnover_series,
        "series": legacy_series,
        "high_30d": {
            "margin_balance": max(margin_series, key=lambda x: x["margin_balance"]),
            "turnover": max(turnover_series, key=lambda x: x["turnover"]),
        },
        "margin_source": "上交所融资融券汇总 + 深交所融资融券汇总 + 北交所融资融券汇总（全A，亿元）",
        "turnover_source": "上证指数成交额 + 深证成指成交额（东方财富历史行情；匹配市场常用沪深两市成交额口径）",
        "source": "两融余额为沪深京三所合计；成交额为沪深两市市场常用口径",
    }
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
