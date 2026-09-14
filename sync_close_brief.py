import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DASH = ROOT / "dashboard.json"
DETAILS = ROOT / "market_details.json"
BRIEF = ROOT / "brief.json"
TZ = ZoneInfo("Asia/Shanghai")


def latest_by_date(rows):
    rows = [x for x in (rows or []) if isinstance(x, dict) and x.get("date")]
    return max(rows, key=lambda x: x["date"]) if rows else None


def fmt_yi(v):
    if v is None:
        return "未取得"
    return f"{v / 1e8:+.2f}亿元"


def fmt_pct(v, suffix="%"):
    if v is None:
        return "未取得"
    return f"{v:+.2f}{suffix}"


def main():
    now = datetime.now(TZ)
    if now.weekday() >= 5 or (now.hour, now.minute) < (15, 10):
        print("Not a post-close sync window; brief left unchanged")
        return

    dashboard = json.loads(DASH.read_text(encoding="utf-8"))
    details = json.loads(DETAILS.read_text(encoding="utf-8"))
    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    target = now.date().isoformat()

    liq = dashboard.get("liquidity") or {}
    sh_rows = sorted(
        [x for x in liq.get("sh_index_series", []) if x.get("date") and x.get("sh_close") is not None],
        key=lambda x: x["date"],
    )
    turnover = latest_by_date(liq.get("turnover_series"))
    if details.get("data_date") != target or not sh_rows or sh_rows[-1]["date"] != target:
        raise RuntimeError(f"Verified close data is not current: target={target}, details={details.get('data_date')}, sh={sh_rows[-1]['date'] if sh_rows else None}")
    if not turnover or turnover.get("date") != target:
        raise RuntimeError(f"Turnover is not current: {turnover}")

    current_sh = sh_rows[-1]
    prev_sh = sh_rows[-2] if len(sh_rows) >= 2 else None
    sh_change = None
    if prev_sh and prev_sh.get("sh_close"):
        sh_change = (current_sh["sh_close"] / prev_sh["sh_close"] - 1) * 100

    current_limit = next((x for x in details.get("limit_history", []) if x.get("date") == target), None)
    if not current_limit or current_limit.get("limit_count") is None or current_limit.get("failed_count") is None:
        raise RuntimeError("Current-day limit-up/failed-board data is not verified")

    snap = brief.setdefault("closing_snapshot", {})
    snap["as_of"] = f"{target} 15:00 北京时间"
    indices = snap.setdefault("indices", {})
    indices["shanghai"] = {"close": current_sh["sh_close"], "change_pct": round(sh_change, 2) if sh_change is not None else None}
    # Other index close values are not part of the verified dashboard pipeline. Do not carry
    # potentially stale intraday values into a post-close snapshot.
    for key in ("shenzhen", "chinext", "star50", "beijing50"):
        indices[key] = {"close": None, "change_pct": None}

    market = snap.setdefault("market", {})
    market["turnover_billion"] = round(turnover["turnover"] / 10, 2)
    market["limit_up_cls_non_st"] = current_limit["limit_count"]
    market["failed_limit_cls_non_st"] = current_limit["failed_count"]
    market["seal_rate_pct"] = round(current_limit.get("seal_rate_pct"), 2) if current_limit.get("seal_rate_pct") is not None else None
    market["advancers"] = None
    market["decliners"] = None
    market["limit_down_total"] = None
    snap["leading"] = []
    snap["lagging"] = []
    snap["note"] = "收盘数值仅同步自动管线已核验字段：上证、沪深成交额、涨停/炸板。其他指数点位、涨跌家数、跌停数及收盘强弱板块未在本管线内独立核验，因此不沿用午间值、不做估算。"

    stocks = {str(x.get("code")): x for x in details.get("stocks", []) if x.get("code")}
    for item in brief.get("candidate_pool", []):
        row = stocks.get(str(item.get("code")))
        if not row or row.get("data_date") != target:
            continue
        windows = row.get("windows") or {}
        pieces = [f"收盘{fmt_pct(row.get('return_pct'))}", f"当日主力净流入{fmt_yi(row.get('day_net_yuan'))}"]
        for n in (3, 5, 10):
            w = windows.get(str(n)) or {}
            pieces.append(
                f"{n}日净流入{fmt_yi(w.get('net_yuan'))}、流入{w.get('inflow_days') if w.get('inflow_days') is not None else '未取得'}/{n}天、超额{fmt_pct(w.get('excess_pct_point'), '个百分点')}"
            )
        item["price_fund_persistence"] = "；".join(pieces)

    cross = brief.setdefault("cross_validation", {})
    margin = latest_by_date(liq.get("margin_series"))
    fin = cross.setdefault("financing", {})
    if margin:
        fin["data_period"] = f"截至{margin['date']}"
        fin["coverage"] = f"两融余额{margin.get('margin_balance')}亿元；按交易所披露节奏使用最近已核验交易日"
        fin["note"] = "披露滞后数据，不与主力资金、ETF或龙虎榜相加。"

    etf_dates = details.get("etf_comparison_dates") or []
    etf_rows = details.get("etf_changes") or []
    etf = cross.setdefault("etf_share", {})
    if len(etf_dates) >= 2 and etf_rows:
        etf["data_period"] = f"{etf_dates[-2]}→{etf_dates[-1]}"
        etf["status"] = f"使用上交所最近两个已核验统计日，共{len(etf_rows)}条可比ETF；若当日份额尚未披露则明确滞后，不估算。"

    dq = brief.setdefault("data_quality", {})
    dq["as_of"] = now.strftime("%Y-%m-%d %H:%M 北京时间")
    dq["verified"] = f"收盘核心市场日{target}；上证、成交额、涨停/炸板、4个板块资金及5只重点股3/5/10日窗口已通过自动校验。"
    dq["unavailable"] = "其他指数收盘点位、全市场涨跌家数/跌停数、收盘强弱板块排名，以及尚未由官方发布到当日的ETF份额，不在自动收盘同步中估算。"
    dq["rule"] = "仅把已通过 dashboard/market_details 最终校验的数据同步为收盘事实；无法核验字段保持空值，午间主观判断不自动改写为收盘结论。"

    decision = brief.setdefault("decision_summary", {})
    decision["close_data_note"] = "该模块的主观判断仍保留原发布时间；收盘事实请以 closing_snapshot、candidate_pool.price_fund_persistence 和 data_quality 为准。"

    brief["date"] = target
    brief["close_data_synced_at"] = now.strftime("%Y-%m-%d %H:%M")
    BRIEF.write_text(json.dumps(brief, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Synced verified close data into brief.json for {target}")


if __name__ == "__main__":
    main()
