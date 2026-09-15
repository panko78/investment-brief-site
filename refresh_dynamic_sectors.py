"""Keep sector modules synchronized with the dynamic all-industry ranking.

Intraday: project the live day/3/5/10 ranking directly into market_details.sectors.
Post-close: try to enrich selected leaders with completed-session Eastmoney history.
This removes the old fixed four-sector fallback from the visible sector modules.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak

import collect_market_details as cmd

ROOT = Path(__file__).resolve().parent
MARKET = ROOT / "market_details.json"
RANKING = ROOT / "sector_ranking.json"
DASHBOARD = ROOT / "dashboard.json"
MAX_SECTORS = 20
MIN_COMPLETE = 8


def first(row, names):
    for name in names:
        if name in row and row.get(name) not in (None, ""):
            return row.get(name)
    return None


def direct_board_map():
    data = cmd.get("https://push2.eastmoney.com/api/qt/clist/get", {
        "pn": 1, "pz": 500, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f3", "fs": "m:90+t:2+f:!50", "fields": "f12,f14"
    }).get("data") or {}
    out = {}
    for row in data.get("diff") or []:
        code, name = row.get("f12"), row.get("f14")
        if code and name:
            out[str(name).strip()] = str(code).strip()
    return out


def board_map():
    try:
        rows = cmd.frame(ak.stock_board_industry_name_em())
        out = {}
        for row in rows:
            name = first(row, ["板块名称", "名称", "行业"])
            code = first(row, ["板块代码", "代码"])
            if name and code:
                out[str(name).strip()] = str(code).strip()
        if out:
            return out
    except Exception as exc:
        print("AKShare board map failed", type(exc).__name__, str(exc)[:120], flush=True)
    out = direct_board_map()
    if not out:
        raise ValueError("No Eastmoney industry board name/code mapping")
    return out


def live_projection(ranking):
    rows = [r for r in ranking.get("rows", []) if r.get("name") and r.get("day_net_yuan") is not None]
    rows.sort(key=lambda r: r.get("day_net_yuan") or 0, reverse=True)
    as_of = (ranking.get("updated_at") or "")[:10] or None
    sectors = []
    for row in rows[:MAX_SECTORS]:
        sectors.append({
            "code": None,
            "name": row.get("name"),
            "data_date": as_of,
            "return_pct": row.get("return_pct"),
            "turnover_yuan": None,
            "turnover_pct": None,
            "day_net_yuan": row.get("day_net_yuan"),
            "net_to_turnover_pct": row.get("day_net_ratio_pct"),
            "turnover_vs_prev20": None,
            "windows": {
                "3": {"net_yuan": row.get("three_day_net_yuan"), "inflow_days": None, "return_pct": row.get("three_day_return_pct"), "excess_pct_point": None, "observed_flow_days": 3 if row.get("three_day_net_yuan") is not None else 0},
                "5": {"net_yuan": row.get("five_day_net_yuan"), "inflow_days": None, "return_pct": row.get("five_day_return_pct"), "excess_pct_point": None, "observed_flow_days": 5 if row.get("five_day_net_yuan") is not None else 0},
                "10": {"net_yuan": row.get("ten_day_net_yuan"), "inflow_days": None, "return_pct": row.get("ten_day_return_pct"), "excess_pct_point": None, "observed_flow_days": 10 if row.get("ten_day_net_yuan") is not None else 0},
            },
        })
    return sectors


def main():
    if not MARKET.exists() or not RANKING.exists():
        print("Dynamic sectors skipped: required file missing")
        return

    market = json.loads(MARKET.read_text(encoding="utf-8"))
    ranking = json.loads(RANKING.read_text(encoding="utf-8"))
    target = market.get("data_date")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))

    # During the session, visible sector modules should move with the live industry
    # ranking instead of staying frozen on the historical four-sector watchlist.
    if now.weekday() < 5 and (now.hour, now.minute) < (15, 10):
        sectors = live_projection(ranking)
        if len(sectors) < MIN_COMPLETE:
            raise ValueError(f"Live sector ranking incomplete: {len(sectors)} rows")
        market["sectors"] = sectors
        market["sector_mode"] = "live_ranking"
        market["sector_scope"] = "动态行业资金榜：当日、3日、5日、10日资金均来自当前行业排名；盘中不伪造成交强度、流入天数或相对收益。"
        market["sector_ranking_updated_at"] = ranking.get("updated_at")
        market["sector_ranking_source"] = ranking.get("source")
        MARKET.write_text(json.dumps(market, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print("SAVED live dynamic sectors", len(sectors), [x["name"] for x in sectors[:8]], flush=True)
        return

    if not target:
        print("Dynamic sectors skipped: market data_date missing")
        return

    liquidity = json.loads(DASHBOARD.read_text(encoding="utf-8")).get("liquidity", {})
    benchmark = [
        {"date": r["date"], "close": r["sh_close"]}
        for r in liquidity.get("sh_index_series", [])
        if r.get("date") and r.get("sh_close") is not None and r["date"] <= target
    ]
    benchmark = sorted(benchmark, key=lambda x: x["date"])
    if len(benchmark) < 22 or benchmark[-1]["date"] != target:
        raise ValueError("Verified benchmark does not cover market data_date")

    mapping = board_map()
    ranked = [r for r in ranking.get("rows", []) if r.get("name") and r.get("day_net_yuan") is not None]
    ranked.sort(key=lambda r: r.get("day_net_yuan") or 0, reverse=True)

    selected = []
    seen = set()
    for row in ranked:
        name = str(row["name"]).strip()
        code = mapping.get(name)
        if not code or code in seen:
            continue
        selected.append((code, name))
        seen.add(code)
        if len(selected) >= 12:
            break

    if len(selected) < MIN_COMPLETE:
        # Keep dynamic modules fresh even if historical board mapping is temporarily unavailable.
        sectors = live_projection(ranking)
        market["sectors"] = sectors
        market["sector_mode"] = "live_ranking"
        market["sector_scope"] = "动态行业资金榜；历史行业板块映射暂不可用，因此成交强度/流入天数/相对收益留空。"
        market["sector_ranking_updated_at"] = ranking.get("updated_at")
        market["sector_ranking_source"] = ranking.get("source")
        MARKET.write_text(json.dumps(market, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print("Historical sector mapping incomplete; saved live projection", flush=True)
        return

    jobs = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for code, name in selected:
            jobs[ex.submit(cmd.prices, "90." + code, target)] = (code, name, "price")
            jobs[ex.submit(cmd.flows, "90." + code, target)] = (code, name, "flow")
        raw = {}
        for fut in as_completed(jobs):
            code, name, kind = jobs[fut]
            try:
                raw.setdefault(code, {"name": name})[kind] = fut.result()
            except Exception as exc:
                print("dynamic sector", code, name, kind, type(exc).__name__, str(exc)[:120], flush=True)

    sectors = []
    for code, name in selected:
        item = raw.get(code, {})
        stat = cmd.stats(item.get("price", []), item.get("flow", []), benchmark, target)
        windows = stat.get("windows", {})
        complete = stat.get("turnover_yuan") is not None and stat.get("day_net_yuan") is not None and all((windows.get(str(n)) or {}).get("net_yuan") is not None for n in (3, 5, 10))
        if complete:
            sectors.append({"code": code, "name": name, **stat})

    if len(sectors) < MIN_COMPLETE:
        sectors = live_projection(ranking)
        market["sector_mode"] = "live_ranking"
        market["sector_scope"] = "动态行业资金榜；完整历史抓取不足，当前使用行业排名中的当日/3日/5日/10日资金。"
    else:
        sectors.sort(key=lambda x: x.get("day_net_yuan") or 0, reverse=True)
        market["sector_mode"] = "completed_history"
        market["sector_scope"] = f"动态行业资金榜：按当前资金排名选取前{len(sectors)}个行业，再用完整交易日历史计算3/5/10日持续性。"

    market["sectors"] = sectors
    market["sector_ranking_updated_at"] = ranking.get("updated_at")
    market["sector_ranking_source"] = ranking.get("source")
    MARKET.write_text(json.dumps(market, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("SAVED dynamic sectors", market.get("sector_mode"), len(sectors), [x["name"] for x in sectors[:8]], flush=True)


if __name__ == "__main__":
    main()
