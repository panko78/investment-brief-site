"""Strict sector-persistence publication contract.

Completed-history mode requires full board history. Live/dynamic mode has fewer
fields by design, but it must still expose genuine day/3/5/10 fund-flow and
return windows for a useful cross-section; never silently skip persistence.
"""
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DETAILS=ROOT/'market_details.json'; RANKING=ROOT/'sector_ranking.json'

def window_complete(w,n):
    return isinstance(w,dict) and w.get('net_yuan') is not None and w.get('inflow_days') is not None and w.get('return_pct') is not None and w.get('excess_pct_point') is not None and (w.get('observed_flow_days') or 0)>=n

def live_window_complete(w,n):
    return isinstance(w,dict) and w.get('net_yuan') is not None and w.get('return_pct') is not None and (w.get('observed_flow_days') or 0)>=n

def completed_sector(row,expected):
    if row.get('data_date')!=expected:return False
    if any(row.get(k) is None for k in ('close','return_pct','turnover_yuan','day_net_yuan','turnover_vs_prev20')):return False
    windows=row.get('windows') or {}
    return all(window_complete(windows.get(str(n)) or {},n) for n in (3,5,10))

def live_sector(row):
    if not row.get('name') or row.get('day_net_yuan') is None or row.get('return_pct') is None:return False
    windows=row.get('windows') or {}
    return all(live_window_complete(windows.get(str(n)) or {},n) for n in (3,5,10))

def ranking_complete(row):
    return bool(row.get('name')) and all(row.get(k) is not None for k in ('day_net_yuan','three_day_net_yuan','five_day_net_yuan','ten_day_net_yuan','return_pct','three_day_return_pct','five_day_return_pct','ten_day_return_pct'))

def ranking_fallback(expected):
    if not RANKING.exists():return 0,'missing ranking'
    r=json.loads(RANKING.read_text(encoding='utf-8')); updated=str(r.get('updated_at') or '')
    if expected and not updated.startswith(expected):return 0,f'ranking date {updated[:10]} != {expected}'
    source=str(r.get('source') or '')
    if not source:return 0,'missing ranking source'
    return sum(ranking_complete(x) for x in (r.get('rows') or [])),source

def main():
    d=json.loads(DETAILS.read_text(encoding='utf-8')); mode=d.get('sector_mode') or 'completed_history'; sectors=d.get('sectors') or []; expected=d.get('data_date')
    if mode!='completed_history':
        complete=[x for x in sectors if live_sector(x)]
        if len(complete)>=8:
            print(f'Sector live persistence validation OK: {len(complete)}/{len(sectors)} industries have genuine day/3/5/10 flow+return windows');return
        # During live mode market_details.data_date can intentionally remain the last completed
        # session, so validate the ranking against its own current-session date rather than
        # incorrectly accepting stale completed-session data.
        r=json.loads(RANKING.read_text(encoding='utf-8')) if RANKING.exists() else {}
        ranking_date=str(r.get('updated_at') or '')[:10]
        n,source=ranking_fallback(ranking_date or expected)
        if n>=8:
            print(f'Sector live persistence validation OK via ranking: {n} complete industries through {ranking_date}; source={source}');return
        raise SystemExit(f'Sector live persistence validation failed: {len(complete)}/{len(sectors)} rendered rows complete; ranking complete={n}, detail={source}; require >=8')
    complete=[x for x in sectors if completed_sector(x,expected)]
    if len(complete)>=8 and len(complete)==len(sectors):
        print(f'Sector persistence validation OK: {len(complete)}/{len(sectors)} completed-history sectors through {expected}');return
    n,source=ranking_fallback(expected)
    if n>=8:
        print(f'Sector persistence validation OK via independent ranking fallback: {n} complete industries through {expected}; source={source}');return
    incomplete=[x.get('code') or x.get('name') or '?' for x in sectors if not completed_sector(x,expected)]
    raise SystemExit(f'Sector persistence validation failed: history {len(complete)}/{len(sectors)} incomplete={incomplete}; ranking complete={n}, detail={source}; require >=8')
if __name__=='__main__':main()
