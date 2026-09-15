"""Strict contract for the sector persistence and best-direction modules.

The general dashboard validator checks many modules. This focused validator exists
so a later repair script cannot accidentally erase 3/5/10-day sector history and
still let the workflow publish successfully.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DETAILS = ROOT / 'market_details.json'


def window_complete(w, n):
    return isinstance(w, dict) and (
        w.get('net_yuan') is not None
        and w.get('inflow_days') is not None
        and w.get('return_pct') is not None
        and w.get('excess_pct_point') is not None
        and (w.get('observed_flow_days') or 0) >= n
    )


def completed_sector(row, expected):
    if row.get('data_date') != expected:
        return False
    for key in ('close', 'return_pct', 'turnover_yuan', 'day_net_yuan', 'turnover_vs_prev20'):
        if row.get(key) is None:
            return False
    windows = row.get('windows') or {}
    return all(window_complete(windows.get(str(n)) or {}, n) for n in (3, 5, 10))


def main():
    details = json.loads(DETAILS.read_text(encoding='utf-8'))
    mode = details.get('sector_mode') or 'completed_history'
    sectors = details.get('sectors') or []
    expected = details.get('data_date')

    if mode != 'completed_history':
        print(f'Sector persistence strict check skipped for mode={mode}; live ranking has a different contract')
        return

    incomplete = [x.get('code') or x.get('name') or '?' for x in sectors if not completed_sector(x, expected)]
    if not sectors:
        raise SystemExit('Sector persistence validation failed: no sectors')
    if incomplete:
        raise SystemExit(
            'Sector persistence validation failed: incomplete 3/5/10 history for '
            + ', '.join(incomplete)
        )
    if len(sectors) < 8:
        raise SystemExit(f'Sector persistence validation failed: only {len(sectors)} complete sectors, require >=8')

    print(f'Sector persistence validation OK: {len(sectors)}/{len(sectors)} complete through {expected}')


if __name__ == '__main__':
    main()
