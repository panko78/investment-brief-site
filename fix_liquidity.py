import json
import time
from pathlib import Path
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd
import requests

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'dashboard.json'
UA={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36'}

def safe_float(x):
    try:
        if pd.isna(x): return None
        return float(str(x).replace(',','').strip())
    except Exception: return None

def date_key(x):
    try: return pd.to_datetime(str(x).strip().replace('/','-')).strftime('%Y-%m-%d')
    except Exception: return None

def recent_weekdays(n=5):
    d=datetime.now().date(); out=[]
    while len(out)<n:
        if d.weekday()<5: out.append(d)
        d-=timedelta(days=1)
    return sorted(out)

def expected_market_date():
    now=datetime.now(); d=now.date()
    if now.weekday()>=5:
        while d.weekday()>=5: d-=timedelta(days=1)
        return d.isoformat()
    if now.hour>=16: return d.isoformat()
    d-=timedelta(days=1)
    while d.weekday()>=5: d-=timedelta(days=1)
    return d.isoformat()

def retry_call(fn,label,attempts=3,base_sleep=.6):
    last=None
    for i in range(attempts):
        try:
            v=fn()
            if v is None: raise RuntimeError('empty response')
            return v
        except Exception as exc:
            last=exc; print(f'{label} attempt {i+1}/{attempts} failed: {exc}')
            if i+1<attempts: time.sleep(base_sleep*(i+1))
    raise last

def merge_rows(old_rows,new_rows,value_key):
    merged={r['date']:r for r in (old_rows or []) if r.get('date') and r.get(value_key) is not None}
    for r in new_rows or []:
        if r.get('date') and r.get(value_key) is not None: merged[r['date']]=r
    return [merged[k] for k in sorted(merged)][-30:]

def fetch_all_a_margin(start_date,end_date):
    sh=retry_call(lambda: ak.stock_margin_sse(start_date=start_date,end_date=end_date),'SSE margin')
    if sh is None or sh.empty: raise RuntimeError('SSE margin data is empty')
    sh_map={}
    for _,row in sh.iterrows():
        d=date_key(row.get('信用交易日期')); v=safe_float(row.get('融资融券余额'))
        if d and v is not None: sh_map[d]=v/1e8
    result=[]
    for d in sorted(sh_map)[-5:]:
        ds=d.replace('-','')
        try:
            sz=retry_call(lambda ds=ds: ak.stock_margin_szse(date=ds),f'SZSE margin {ds}',attempts=2)
            bj=retry_call(lambda ds=ds: ak.stock_margin_bse(date=ds),f'BSE margin {ds}',attempts=2)
            if sz is None or sz.empty or bj is None or bj.empty: continue
            sv=safe_float(sz.iloc[0].get('融资融券余额')); bv=safe_float(bj.iloc[0].get('融资融券余额'))
            if sv is not None and bv is not None: result.append({'date':d,'margin_balance':round(sh_map[d]+sv+bv/10000.0,2)})
        except Exception as exc: print('margin skip',d,exc)
    if not result: raise RuntimeError('No complete SSE+SZSE+BSE margin observations')
    return result

def fetch_hs_turnover_official():
    rows=[]
    expected=expected_market_date()
    for d in recent_weekdays(4):
        # Intraday runs must never query or validate against an unfinished trading day.
        if d.isoformat()>expected:
            continue
        ds=d.strftime('%Y%m%d')
        try:
            sse=retry_call(lambda ds=ds: ak.stock_sse_deal_daily(date=ds),f'SSE turnover {ds}',attempts=2,base_sleep=.2)
            if sse is None or sse.empty: continue
            hit=sse[sse['单日情况'].astype(str).str.contains('成交金额',na=False)]
            if hit.empty: continue
            rr=hit.iloc[0]; sh_amt=(safe_float(rr.get('主板A')) or 0)+(safe_float(rr.get('科创板')) or 0)
            sz=retry_call(lambda ds=ds: ak.stock_szse_summary(date=ds),f'SZSE turnover {ds}',attempts=2,base_sleep=.2)
            if sz is None or sz.empty: continue
            sz_amt=0.0
            for category in ('主板A股','创业板A股'):
                z=sz[sz['证券类别'].astype(str)==category]
                if not z.empty:
                    v=safe_float(z.iloc[0].get('成交金额'))
                    if v is not None: sz_amt+=v/1e8
            if sh_amt>0 and sz_amt>0: rows.append({'date':d.isoformat(),'turnover':round(sh_amt+sz_amt,2)})
        except Exception as exc: print('turnover official skip',ds,exc)
    if not rows: raise RuntimeError('No official SSE+SZSE turnover observations')
    return rows

def eastmoney_index_amount(secid,start_date,end_date):
    url='https://push2his.eastmoney.com/api/qt/stock/kline/get'
    params={'secid':secid,'klt':101,'fqt':0,'beg':start_date,'end':end_date,'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61','ut':'7eea3edcaed734bea9cbfc24409ed989'}
    def _fetch():
        r=requests.get(url,params=params,headers=UA,timeout=(6,15)); r.raise_for_status(); j=r.json(); lines=(j.get('data') or {}).get('klines') or []
        if not lines: raise RuntimeError('no kline rows')
        return lines
    out={}
    for line in retry_call(_fetch,f'Eastmoney amount {secid}',attempts=3):
        a=line.split(',')
        if len(a)>=7:
            v=safe_float(a[6])
            if v and v>0: out[a[0]]=v/1e8
    return out

def fetch_hs_turnover_fallback(start_date,end_date):
    sh=eastmoney_index_amount('1.000002',start_date,end_date); sz=eastmoney_index_amount('0.399107',start_date,end_date)
    rows=[{'date':d,'turnover':round(sh[d]+sz[d],2)} for d in sorted(set(sh)&set(sz)) if sh[d]>0 and sz[d]>0]
    if not rows: raise RuntimeError('No Eastmoney A-share index turnover observations')
    return rows[-30:]

def fetch_hs_turnover(start_date,end_date):
    expected=expected_market_date()
    try:
        rows=fetch_hs_turnover_official()
        if rows[-1]['date']!=expected: raise RuntimeError(f'official latest {rows[-1]["date"]} != expected {expected}')
        return rows,'交易所官方成交额'
    except Exception as exc:
        print('Official turnover unavailable/stale, switching fallback:',exc)
        rows=fetch_hs_turnover_fallback(start_date,end_date)
        # Eastmoney may expose today's intraday bar. Only completed-market rows are valid here.
        rows=[x for x in rows if x.get('date')<=expected]
        if not rows or rows[-1]['date']!=expected: raise RuntimeError(f'fallback latest {rows[-1]["date"] if rows else None} != expected {expected}')
        return rows,'东方财富上证A股指数+深证A股指数成交额备用源'

def fetch_sh_index(start_date,end_date):
    df=None
    try: df=retry_call(lambda: ak.stock_zh_index_daily_em(symbol='sh000001',start_date=start_date,end_date=end_date),'Shanghai Composite EM',attempts=2)
    except Exception as exc: print('Shanghai Composite EM failed, fallback Sina:',exc)
    if df is None or df.empty: df=retry_call(lambda: ak.stock_zh_index_daily(symbol='sh000001'),'Shanghai Composite Sina',attempts=2)
    if df is None or df.empty: raise RuntimeError('Shanghai Composite data is empty')
    dc=next((c for c in df.columns if str(c).lower()=='date' or str(c)=='日期'),None); cc=next((c for c in df.columns if str(c).lower()=='close' or str(c)=='收盘'),None)
    if dc is None or cc is None: raise RuntimeError(f'Unexpected columns: {list(df.columns)}')
    expected=expected_market_date(); rows=[]
    for _,row in df.iterrows():
        d=date_key(row.get(dc)); v=safe_float(row.get(cc))
        # Sina may include the current unfinished session; exclude it from completed-day series.
        if d and d<=expected and v is not None: rows.append({'date':d,'sh_close':round(v,2)})
    rows=sorted(rows,key=lambda x:x['date'])[-30:]
    if not rows or rows[-1]['date']!=expected: raise RuntimeError('Shanghai Composite latest completed date is stale')
    return rows

def main():
    data=json.loads(DATA.read_text(encoding='utf-8')) if DATA.exists() else {}; old=data.get('liquidity',{}); today=datetime.now().date(); start=(today-timedelta(days=45)).strftime('%Y%m%d'); end=today.strftime('%Y%m%d'); warnings=[]
    try: margin_series=merge_rows(old.get('margin_series') or old.get('series'),fetch_all_a_margin(start,end),'margin_balance')
    except Exception as exc:
        print('margin update failed, keep previous:',exc); margin_series=old.get('margin_series') or [{'date':x['date'],'margin_balance':x['margin_balance']} for x in old.get('series',[]) if x.get('margin_balance') is not None]; warnings.append('两融接口本次失败，保留上一成功数据和真实数据日')
    try:
        fresh,source=fetch_hs_turnover(start,end); turnover_series=merge_rows(old.get('turnover_series') or old.get('series'),fresh,'turnover')
    except Exception as exc:
        print('turnover update failed, keep previous:',exc); turnover_series=old.get('turnover_series') or [{'date':x['date'],'turnover':x['turnover']} for x in old.get('series',[]) if x.get('turnover') is not None]; source='上一成功数据'; warnings.append('成交额主源和备用源均失败，保留上一成功数据和真实数据日')
    try: sh_index_series=merge_rows(old.get('sh_index_series'),fetch_sh_index(start,end),'sh_close')
    except Exception as exc:
        print('index update failed, keep previous:',exc); sh_index_series=old.get('sh_index_series',[]); warnings.append('上证指数主源和备用源均失败，保留上一成功数据和真实数据日')
    if not margin_series or not turnover_series or not sh_index_series: raise RuntimeError('Critical market series unavailable')
    mm={x['date']:x['margin_balance'] for x in margin_series}; tm={x['date']:x['turnover'] for x in turnover_series}; common=sorted(set(mm)&set(tm))[-30:]
    data['liquidity']={'status':'多源校验更新正常' if not warnings else '；'.join(warnings),'freshness':{'margin_as_of':margin_series[-1]['date'],'turnover_as_of':turnover_series[-1]['date'],'sh_index_as_of':sh_index_series[-1]['date'],'checked_at':datetime.now().strftime('%Y-%m-%d %H:%M')},'margin_series':margin_series[-30:],'turnover_series':turnover_series[-30:],'sh_index_series':sh_index_series[-30:],'series':[{'date':d,'margin_balance':mm[d],'turnover':tm[d]} for d in common],'high_30d':{'margin_balance':max(margin_series,key=lambda x:x['margin_balance']),'turnover':max(turnover_series,key=lambda x:x['turnover']),'sh_index':max(sh_index_series,key=lambda x:x['sh_close'])},'margin_source':'上交所+深交所+北交所融资融券汇总（增量更新）','turnover_source':'优先交易所官方汇总；失败切东方财富上证A股指数000002+深证A股指数399107；当前：'+source,'sh_index_source':'上证指数000001：东方财富主源，新浪备用源（盘中自动剔除未收盘当日行）','source':'各序列独立记录真实数据日期'}
    data['updated_at']=datetime.now().strftime('%Y-%m-%d %H:%M'); DATA.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__': main()
