"""Collect a dynamic all-industry fund-flow ranking for the dashboard.

Primary source: Eastmoney industry fund-flow ranking via AKShare.
Fallback: Tonghuashun industry fund-flow ranking via AKShare.
The file is intentionally separate from market_details.json so intraday sector ranking
can refresh without mixing partial-day data into 3/5/10-day completed-session windows.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "sector_ranking.json"


def num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def records(df):
    return json.loads(df.to_json(orient="records", force_ascii=False, date_format="iso"))


def pick(row, exact=None, contains=None):
    exact = exact or []
    contains = contains or []
    for key in exact:
        if key in row and row.get(key) is not None:
            return row.get(key)
    for key, value in row.items():
        if all(part in str(key) for part in contains) and value is not None:
            return value
    return None


def eastmoney_period(indicator):
    rows = records(ak.stock_sector_fund_flow_rank(indicator=indicator, sector_type="行业资金流"))
    out = {}
    for row in rows:
        name = pick(row, exact=["名称", "板块名称", "行业"])
        if not name:
            continue
        change = pick(row, exact=[f"{indicator}涨跌幅", "今日涨跌幅", "涨跌幅"], contains=["涨跌幅"])
        net = pick(row, exact=["主力净流入-净额", "主力净流入净额"], contains=["主力", "净流入", "净额"])
        ratio = pick(row, exact=["主力净流入-净占比", "主力净流入净占比"], contains=["主力", "净流入", "净占比"])
        rank = pick(row, exact=["序号", "排名"])
        out[str(name)] = {
            "name": str(name),
            "change_pct": num(change),
            "net_yuan": num(net),
            "net_ratio_pct": num(ratio),
            "source_rank": int(num(rank)) if num(rank) is not None else None,
        }
    if not out:
        raise ValueError(f"Eastmoney {indicator} industry ranking returned no usable rows")
    return out


def ths_period(symbol):
    rows = records(ak.stock_fund_flow_industry(symbol=symbol))
    out = {}
    for row in rows:
        name = pick(row, exact=["行业", "名称"])
        if not name:
            continue
        change = pick(row, exact=["行业-涨跌幅", "涨跌幅"], contains=["涨跌幅"])
        # THS interface documents '净额' in 亿元; normalize to yuan.
        net = pick(row, exact=["净额"], contains=["净额"])
        rank = pick(row, exact=["序号", "排名"])
        net_billion = num(net)
        out[str(name)] = {
            "name": str(name),
            "change_pct": num(str(change).replace("%", "")) if change is not None else None,
            "net_yuan": net_billion * 1e8 if net_billion is not None else None,
            "net_ratio_pct": None,
            "source_rank": int(num(rank)) if num(rank) is not None else None,
        }
    if not out:
        raise ValueError(f"THS {symbol} industry ranking returned no usable rows")
    return out


def collect_primary():
    today = eastmoney_period("今日")
    five = eastmoney_period("5日")
    ten = eastmoney_period("10日")
    names = set(today) | set(five) | set(ten)
    rows = []
    for name in names:
        d = today.get(name, {})
        f = five.get(name, {})
        t = ten.get(name, {})
        rows.append({
            "name": name,
            "return_pct": d.get("change_pct"),
            "day_net_yuan": d.get("net_yuan"),
            "day_net_ratio_pct": d.get("net_ratio_pct"),
            "five_day_net_yuan": f.get("net_yuan"),
            "ten_day_net_yuan": t.get("net_yuan"),
            "source_rank": d.get("source_rank"),
        })
    return rows, "东方财富行业资金流排名（AKShare）"


def collect_fallback():
    today = ths_period("即时")
    five = ths_period("5日排行")
    ten = ths_period("10日排行")
    names = set(today) | set(five) | set(ten)
    rows = []
    for name in names:
        d = today.get(name, {})
        f = five.get(name, {})
        t = ten.get(name, {})
        rows.append({
            "name": name,
            "return_pct": d.get("change_pct"),
            "day_net_yuan": d.get("net_yuan"),
            "day_net_ratio_pct": None,
            "five_day_net_yuan": f.get("net_yuan"),
            "ten_day_net_yuan": t.get("net_yuan"),
            "source_rank": d.get("source_rank"),
        })
    return rows, "同花顺行业资金流排名（AKShare备用）"


def main():
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    errors = []
    try:
        rows, source = collect_primary()
    except Exception as exc:
        errors.append(f"Eastmoney: {type(exc).__name__}: {str(exc)[:180]}")
        try:
            rows, source = collect_fallback()
        except Exception as fallback_exc:
            errors.append(f"THS: {type(fallback_exc).__name__}: {str(fallback_exc)[:180]}")
            if OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8").strip():
                print("Sector ranking upstream unavailable; retaining last verified file")
                print(" | ".join(errors))
                return
            raise

    usable = [r for r in rows if r.get("day_net_yuan") is not None]
    usable.sort(key=lambda r: r.get("day_net_yuan") or 0, reverse=True)
    for i, row in enumerate(usable, 1):
        row["rank"] = i

    payload = {
        "updated_at": now.isoformat(),
        "source": source,
        "scope": "全市场行业资金流动态排名；概念板块不混入行业排名。今日为当前/最近交易时点快照，5日与10日为同源区间资金排名。",
        "rows": usable,
        "upstream_errors": errors,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("SAVED sector ranking", len(usable), source, now.isoformat(), flush=True)


if __name__ == "__main__":
    main()
