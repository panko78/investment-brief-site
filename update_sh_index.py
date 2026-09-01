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
    try:
        return pd.to_datetime(str(x).strip().replace("/", "-")).strftime("%Y-%m-%d")
    except Exception:
        return None


def fetch_index():
    today = datetime.now().date()
    start = (today - timedelta(days=80)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    df = None
    try:
        df = ak.stock_zh_index_daily_em(symbol="sh000001", start_date=start, end_date=end)
    except Exception as exc:
        print("EM index source failed:", exc)
    if df is None or df.empty:
        df = ak.stock_zh_index_daily(symbol="sh000001")
    if df is None or df.empty:
        raise RuntimeError("Shanghai Composite data is empty")
    date_col = next((c for c in df.columns if str(c).lower() == "date" or str(c) == "日期"), None)
    close_col = next((c for c in df.columns if str(c).lower() == "close" or str(c) == "收盘"), None)
    if date_col is None or close_col is None:
        raise RuntimeError(f"Unexpected columns: {list(df.columns)}")
    rows = []
    for _, row in df.iterrows():
        d = date_key(row.get(date_col))
        v = safe_float(row.get(close_col))
        if d and v is not None:
            rows.append({"date": d, "sh_close": round(v, 2)})
    return sorted(rows, key=lambda x: x["date"])[-30:]


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    liq = data.setdefault("liquidity", {})
    series = fetch_index()
    liq["sh_index_series"] = series
    liq["sh_index_source"] = "上证指数（000001）日收盘点位，AKShare公开指数行情接口"
    high = liq.setdefault("high_30d", {})
    high["sh_index"] = max(series, key=lambda x: x["sh_close"])
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
