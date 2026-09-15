"""Collect a same-day limit-up/failed-limit snapshot for the dashboard.

This module is deliberately separate from completed-session market_details history.
During a trading day the dashboard must not stay frozen on yesterday merely because
other modules use the latest completed session. Upstream failures never turn an
empty response into zero; the previous snapshot remains visible but validation will
mark it stale and block a falsely-successful publication.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak

ROOT = Path(__file__).resolve().parent
MARKET = ROOT / "market_details.json"
TZ = ZoneInfo("Asia/Shanghai")


def frame(df):
    return json.loads(df.to_json(orient="records", force_ascii=False, date_format="iso"))


def fetch_with_retry(fn, date_text, attempts=3):
    last = None
    for i in range(attempts):
        try:
            rows = frame(fn(date=date_text))
            if rows is None:
                raise ValueError("null response")
            return rows
        except Exception as exc:
            last = exc
            print(f"limit snapshot attempt {i+1}/{attempts} {fn.__name__}: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
            if i + 1 < attempts:
                time.sleep(2 * (i + 1))
    raise last


def main():
    now = datetime.now(TZ)
    if not MARKET.exists():
        raise SystemExit("market_details.json missing")

    # Scheduled pre-market runs do not need an intraday snapshot.
    if now.weekday() >= 5 or (now.hour, now.minute) < (9, 30):
        print("Live limit snapshot not required at this time", flush=True)
        return

    date_iso = now.date().isoformat()
    date_arg = date_iso.replace("-", "")
    up = fetch_with_retry(ak.stock_zt_pool_em, date_arg)
    failed = fetch_with_retry(ak.stock_zt_pool_zbgc_em, date_arg)

    # An all-empty response during/after the session is more likely an upstream
    # failure than a genuine zero and must never overwrite verified data as zero.
    if not up and not failed:
        raise ValueError("limit-up and failed-limit pools are both empty; refusing to publish zero")

    up_codes = {str(r.get("代码")) for r in up if r.get("代码")}
    failed_codes = {str(r.get("代码")) for r in failed if r.get("代码")}
    if up_codes & failed_codes:
        raise ValueError("limit-up and failed-limit pools overlap")

    industry = Counter(str(r.get("所属行业") or "未分类") for r in up)
    streaks = []
    leaders = []
    for row in up:
        try:
            streak = int(float(row.get("连板数") or 0))
        except (TypeError, ValueError):
            streak = 0
        streaks.append(streak)
        leaders.append({
            "code": row.get("代码"),
            "name": row.get("名称"),
            "industry": row.get("所属行业"),
            "streak": streak or None,
            "last_price": row.get("最新价"),
            "return_pct": row.get("涨跌幅"),
            "turnover_yuan": row.get("成交额"),
            "seal_amount_yuan": row.get("封板资金"),
            "first_seal_time": row.get("首次封板时间"),
            "last_seal_time": row.get("最后封板时间"),
        })
    leaders.sort(key=lambda x: (x.get("streak") or 0, x.get("seal_amount_yuan") or 0), reverse=True)

    limit_count = len(up)
    failed_count = len(failed)
    denom = limit_count + failed_count
    snapshot = {
        "status": "ok",
        "date": date_iso,
        "fetched_at": now.isoformat(),
        "source": "东方财富涨停/炸板专题池（AKShare，盘中实时快照）",
        "limit_count": limit_count,
        "failed_count": failed_count,
        "seal_rate_pct": (limit_count / denom * 100) if denom else None,
        "highest_streak": max(streaks) if streaks else None,
        "industry_distribution": [
            {"industry": name, "count": count}
            for name, count in industry.most_common(20)
        ],
        "leaders": leaders[:30],
    }

    market = json.loads(MARKET.read_text(encoding="utf-8"))
    market["live_limit_snapshot"] = snapshot
    market["limit_scope"] = (
        "盘中优先显示当日东方财富涨停/炸板专题池实时快照；"
        "完整交易日历史仍保存在 limit_history。接口口径不含ST及科创板，北交所覆盖未单独验证。"
    )
    MARKET.write_text(json.dumps(market, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(
        f"SAVED live limits {date_iso}: up={limit_count} failed={failed_count} "
        f"seal={snapshot['seal_rate_pct']:.2f}% highest={snapshot['highest_streak']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
