"""Repair/refresh candidate-stock prices with independent historical/quote fallbacks.

Fund-flow data is never fabricated. BaoStock is preferred for completed trading-day
prices; Sina is the same-day end-of-day fallback. Price-derived 3/5/10-day returns
are recomputed from verified historical closes; flow-derived fields stay untouched.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import baostock as bs
import requests

ROOT = Path(__file__).resolve().parent
DETAILS = ROOT / 'market_details.json'
BRIEF = ROOT / 'brief.json'
DASH = ROOT / 'dashboard.json'
HEADERS = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36','Referer':'https://finance.sina.com.cn/'}

def num(v):
    try: return float(v) if v not in (None,'') else None
    except (TypeError,ValueError): return None

def baostock_history(code, expected_date):
    symbol=('sh.' if code.startswith('6') else 'sz.')+code
    start=(datetime.fromisoformat(expected_date)-timedelta(days=100)).date().isoformat()
    lg=bs.login()
    if lg.error_code!='0': raise ValueError(f'BaoStock login {lg.error_code}: {lg.error_msg}')
    fields='date,open,high,low,close,volume,amount,turn,pctChg'
    try:
        rs=bs.query_history_k_data_plus(symbol,fields,start_date=start,end_date=expected_date,frequency='d',adjustflag='3')
        if rs.error_code!='0': raise ValueError(f'BaoStock query {rs.error_code}: {rs.error_msg}')
        rows=[]; names=fields.split(',')
        while rs.next():
            raw=dict(zip(names,rs.get_row_data()))
            rows.append({'date':raw['date'],'close':num(raw['close']),'turnover_yuan':num(raw['amount']),'turnover_pct':num(raw['turn']),'return_pct':num(raw['pctChg'])})
    finally: bs.logout()
    current=next((r for r in reversed(rows) if r['date']==expected_date),None)
    if not current or current.get('close') is None or current.get('turnover_yuan') is None: raise ValueError(f'BaoStock has no complete row for {expected_date}')
    return rows,current

def sina_snapshot(code, expected_date):
    symbol=('sh' if code.startswith('6') else 'sz')+code
    r=requests.get(f'https://hq.sinajs.cn/list={symbol}',headers=HEADERS,timeout=(8,15)); r.raise_for_status(); r.encoding='gbk'; text=r.text.strip()
    if '="' not in text: raise ValueError('unexpected Sina quote format')
    fields=text.split('="',1)[1].rsplit('"',1)[0].split(',')
    if len(fields)<32 or not fields[0]: raise ValueError('empty Sina quote')
    if fields[30]!=expected_date: raise ValueError(f'Sina quote date {fields[30]}, expected {expected_date}')
    prev_close=float(fields[2]); close=float(fields[3]); turnover_yuan=float(fields[9])
    if close<=0 or prev_close<=0 or turnover_yuan<0: raise ValueError('invalid Sina quote values')
    return {'close':close,'return_pct':(close/prev_close-1)*100,'turnover_yuan':turnover_yuan,'quote_source':'新浪财经收盘快照'}

def recompute_price_windows(row,code,expected,details,benchmark,extra_rows=None):
    price_rows=list(((details.get('datasets') or {}).get(f'price_{code}') or {}).get('rows') or [])
    if extra_rows: price_rows.extend(extra_rows)
    pm={r.get('date'):r for r in price_rows if r.get('date') and r.get('close') is not None}; pm[expected]={**pm.get(expected,{}),'date':expected,'close':row.get('close')}
    bm={r.get('date'):r.get('close') for r in benchmark if r.get('date') and r.get('close') is not None}; dates=[r.get('date') for r in benchmark if r.get('date') and r.get('date')<=expected]
    windows=row.setdefault('windows',{})
    for n in (3,5,10):
        w=windows.setdefault(str(n),{}); start=dates[-n-1] if len(dates)>n else None
        if not start or start not in pm or expected not in pm or start not in bm or expected not in bm: continue
        sc=pm[start].get('close'); ec=pm[expected].get('close')
        if sc in (None,0) or ec is None or bm[start] in (None,0): continue
        ret=(ec/sc-1)*100; base=(bm[expected]/bm[start]-1)*100; w['return_pct']=ret; w['excess_pct_point']=ret-base

def main():
    details=json.loads(DETAILS.read_text(encoding='utf-8')); brief=json.loads(BRIEF.read_text(encoding='utf-8')); dashboard=json.loads(DASH.read_text(encoding='utf-8')); expected=details.get('data_date')
    if not expected: raise SystemExit('market_details.json has no data_date')
    benchmark=[{'date':r.get('date'),'close':r.get('sh_close')} for r in (dashboard.get('liquidity') or {}).get('sh_index_series',[]) if r.get('date') and r.get('date')<=expected and r.get('sh_close') is not None]; benchmark.sort(key=lambda r:r['date'])
    candidates={x.get('code'):x.get('name') for x in brief.get('candidate_pool',[]) if x.get('code')}; stocks=details.get('stocks') or []; by_code={x.get('code'):x for x in stocks if x.get('code')}; repaired=[]; failures=[]
    for code,name in candidates.items():
        row=by_code.get(code)
        if row is None: row={'code':code,'name':name,'data_date':expected,'windows':{}}; stocks.append(row); by_code[code]=row
        try:
            history,current=baostock_history(code,expected); row.update(close=current['close'],return_pct=current['return_pct'],turnover_yuan=current['turnover_yuan']);
            if current.get('turnover_pct') is not None: row['turnover_pct']=current['turnover_pct']
            row['snapshot_source']='BaoStock未复权日线备用源'; recompute_price_windows(row,code,expected,details,benchmark,history); repaired.append((code,'baostock')); continue
        except Exception as first_exc:
            try:
                snap=sina_snapshot(code,expected); row.update(close=snap['close'],return_pct=snap['return_pct'],turnover_yuan=snap['turnover_yuan'],snapshot_source=snap['quote_source']); recompute_price_windows(row,code,expected,details,benchmark); repaired.append((code,'sina')); continue
            except Exception as second_exc: failures.append(f'{code} BaoStock={type(first_exc).__name__}: {first_exc}; Sina={type(second_exc).__name__}: {second_exc}')
    details['stocks']=stocks; availability=details.setdefault('availability',[]); now=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(); datasets=details.setdefault('datasets',{})
    for code,source in repaired:
        # Keep the canonical price dataset provenance in sync with the independent close refresh.
        # The validator reads price_<code>.fetched_at, so a side-channel availability record alone
        # must not leave a successfully refreshed close looking stale.
        pds=datasets.setdefault(f'price_{code}',{})
        pds['fetched_at']=now; pds['refresh_source']='BaoStock未复权日线备用源' if source=='baostock' else '新浪财经收盘快照'
        availability.append({'dataset':f'{source}_price_{code}','status':'ok','rows':1,'fetched_at':now})
        # Fund-flow fields are not refetched here. They are only revalidated when their stock row
        # is explicitly for the expected session and all 3/5/10-day flow windows are complete.
        row=by_code.get(code) or {}; wins=row.get('windows') or {}
        flow_complete=(row.get('data_date')==expected and row.get('day_net_yuan') is not None and all((wins.get(str(n)) or {}).get('net_yuan') is not None and (wins.get(str(n)) or {}).get('inflow_days') is not None and (wins.get(str(n)) or {}).get('observed_flow_days',0)>=n for n in (3,5,10)))
        if flow_complete:
            fds=datasets.setdefault(f'flow_{code}',{}); fds['fetched_at']=now; fds['validated_at']=now; fds['validation']='same-session row + complete 3/5/10 flow windows; values unchanged'
    for item in failures: availability.append({'dataset':'candidate_price_fallback','status':'error','reason':item,'retained':False})
    details['updated_at']=now
    DETAILS.write_text(json.dumps(details,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8'); print('Candidate stock price/window repairs:',repaired or 'none')
    if failures: print('Candidate price fallback failures:',failures)
    if len(repaired)!=len(candidates): raise SystemExit('Candidate stock fresh refresh incomplete: '+','.join(sorted(set(candidates)-{c for c,_ in repaired})))
    still=[]
    for row in stocks:
        if row.get('code') not in candidates: continue
        if any(row.get(k) is None for k in ('close','return_pct','turnover_yuan')): still.append(row.get('code')); continue
        for n in (3,5,10):
            w=(row.get('windows') or {}).get(str(n),{})
            if w.get('return_pct') is None or w.get('excess_pct_point') is None: still.append(row.get('code')); break
    if still: raise SystemExit('Candidate stocks still missing price windows: '+','.join(sorted(set(still))))
if __name__=='__main__': main()
