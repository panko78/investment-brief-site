"""Replace the old fixed four-sector watchlist with a dynamic industry set.

The selector uses the market-wide industry fund-flow ranking collected in
sector_ranking.json. Historical price/flow windows are then fetched for the selected
Eastmoney industry boards so the existing dashboard can keep its 3/5/10-day logic.
Intraday ranking and completed-day persistence are deliberately not mixed.
If the dynamic refresh is incomplete, the last verified sector set is retained.
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
MAX_SECTORS = 12
MIN_COMPLETE = 8
BOARD_LIST_URL = "https://17.push2.eastmoney.com/api/qt/clist/get"


def first(row, names):
    for name in names:
        if name in row and row.get(name) not in (None, ""):
            return row.get(name)
    return None


def board_map():
    """Resolve Eastmoney industry names to BK codes with two independent paths."""
    errors = []
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
        errors.append(f"AKShare: {type(exc).__name__}: {str(exc)[:120]}")

    try:
        data = cmd.get(BOARD_LIST_URL, {
            "pn": 1,
            "pz": 200,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": "m:90+t:2+f:!50",
            "fields": "f12,f14",
        }).get("data") or {}
        diff = data.get("diff") or []
        out = {
            str(row.get("f14")).strip(): str(row.get("f12")).strip()
            for row in diff
            if row.get("f14") and row.get("f12")
        }
        if out:
            print("Industry board map recovered from direct Eastmoney endpoint", len(out), flush=True)
            return out
    except Exception as exc:
        errors.append(f"Direct: {type(exc).__name__}: {str(exc)[:120]}")

    raise ValueError("No Eastmoney industry board mapping; " + " | ".join(errors))


def main():
    if not MARKET.exists() or not RANKING.exists():
        print("Dynamic sectors skipped: required file missing")
        return

    market = json.loads(MARKET.read_text(encoding="utf-8"))
    ranking = json.loads(RANKING.read_text(encoding="utf-8"))
    target = market.get("data_date")
    if not target:
        print("Dynamic sectors skipped: market data_date missing")
        return

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    # The live sector ranking is partial-session data during market hours, while
    # market_details.data_date intentionally remains the latest completed session.
    # Keep the two separate until the post-close refresh so 3/5/10-day statistics
    # are never selected using a different day's intraday ranking.
    intraday = now.weekday() < 5 and (9, 25) <= (now.hour, now.minute) < (15, 10)
    if intraday and target != now.date().isoformat():
        print("Dynamic sector history skipped intraday; live ranking remains in sector_ranking.json", flush=True)
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
        if len(selected) >= MAX_SECTORS:
            break

    if len(selected) < MIN_COMPLETE:
        raise ValueError(f"Only {len(selected)} ranked industries mapped to Eastmoney board codes")

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
        price = item.get("price", [])
        flow = item.get("flow", [])
        stat = cmd.stats(price, flow, benchmark, target)
        windows = stat.get("windows", {})
        complete = (
            stat.get("turnover_yuan") is not None
            and stat.get("day_net_yuan") is not None
            and all((windows.get(str(n)) or {}).get("net_yuan") is not None for n in (3, 5, 10))
        )
        if complete:
            sectors.append({"code": code, "name": name, **stat})

    if len(sectors) < MIN_COMPLETE:
        raise ValueError(f"Dynamic sector refresh incomplete: {len(sectors)}/{len(selected)} complete; retaining previous verified set")

    sectors.sort(key=lambda x: x.get("day_net_yuan") or 0, reverse=True)
    market["sectors"] = sectors
    market["sector_scope"] = (
        f"动态行业资金榜：按全市场行业主力净流入选取前{len(sectors)}个行业，"
        "再用东方财富行业板块日线和历史资金流计算3/5/10日持续性；概念板块不与行业混排。"
    )
    market["sector_ranking_updated_at"] = ranking.get("updated_at")
    market["sector_ranking_source"] = ranking.get("source")
    MARKET.write_text(json.dumps(market, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("SAVED dynamic sectors", target, len(sectors), [x["name"] for x in sectors], flush=True)


if __name__ == "__main__":
    main()
