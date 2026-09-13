#!/usr/bin/env python3
"""Synchronize dashboard liquidity freshness with verified market details.

The liquidity crawler runs before the detailed market collector. If the latter obtains a
newer verified two-financing disclosure, copy that newer point back into dashboard.json
so the dashboard and market_details.json cannot disagree about margin freshness.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

DASHBOARD = Path("dashboard.json")
DETAILS = Path("market_details.json")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def main() -> None:
    if not DASHBOARD.exists() or not DETAILS.exists():
        print("Dashboard/detail file missing; nothing to synchronize")
        return

    dashboard = load(DASHBOARD)
    details = load(DETAILS)

    quality = details.get("quality") or {}
    if quality.get("publish_status") == "blocked":
        print("Detailed market data is blocked; refusing to propagate it")
        return

    margin = details.get("margin") or {}
    detail_date = margin.get("date") or quality.get("two_financing_date")
    detail_balance = as_number(
        margin.get("balance")
        if margin.get("balance") is not None
        else margin.get("margin_balance")
    )
    if not detail_date or detail_balance is None:
        print("No verified detailed margin point available; nothing to synchronize")
        return

    # Require the detailed collector's own quality marker to agree with the point date
    # when it is present. This prevents an unvalidated partial response from being copied.
    verified_date = quality.get("two_financing_date")
    if verified_date and verified_date != detail_date:
        print(f"Margin date mismatch inside market_details: {detail_date} vs {verified_date}; skip")
        return

    liquidity = dashboard.setdefault("liquidity", {})
    freshness = liquidity.setdefault("freshness", {})
    dashboard_date = freshness.get("margin_as_of") or ""
    if dashboard_date and detail_date < dashboard_date:
        print(f"Dashboard margin is newer ({dashboard_date}) than details ({detail_date}); skip")
        return

    series = liquidity.setdefault("margin_series", [])
    by_date = {}
    for row in series:
        if isinstance(row, dict) and row.get("date"):
            by_date[row["date"]] = dict(row)

    target = by_date.get(detail_date, {"date": detail_date})
    target["margin_balance"] = round(detail_balance, 2)

    # If turnover for the same day is already verified elsewhere in the dashboard, attach
    # it to the combined liquidity point without inventing a value.
    if target.get("turnover") is None:
        for row in liquidity.get("turnover_series") or []:
            if isinstance(row, dict) and row.get("date") == detail_date:
                turnover = as_number(row.get("turnover"))
                if turnover is not None:
                    target["turnover"] = round(turnover, 2)
                break

    by_date[detail_date] = target
    liquidity["margin_series"] = [by_date[d] for d in sorted(by_date)][-45:]
    freshness["margin_as_of"] = detail_date
    freshness["checked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Some dashboard revisions carry these optional freshness helpers. Keep them coherent
    # if present, but do not add UI fields that the current frontend does not use.
    a_share_date = freshness.get("a_share_as_of") or freshness.get("sh_index_as_of")
    if "margin_lag_trade_days" in freshness and a_share_date == detail_date:
        freshness["margin_lag_trade_days"] = 0
    if "margin_ok" in freshness:
        freshness["margin_ok"] = True

    dashboard["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    DASHBOARD.write_text(
        json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Synchronized dashboard margin freshness {dashboard_date or '(missing)'} -> {detail_date}; "
        f"balance={detail_balance:.2f}亿元"
    )


if __name__ == "__main__":
    main()
