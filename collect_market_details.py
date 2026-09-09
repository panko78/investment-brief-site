"""Collect dated, reproducible market details without rewriting the news brief.

Prices/flows: Eastmoney, yuan. ETF shares: SSE via AKShare, shares (not yuan).
Limits: Eastmoney topic pool; scope differs from news and excludes some boards.
"""
import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import akshare as ak
try:
    from curl_cffi import requests as curl_requests
except Exception:
    curl_requests = None

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'market_details.json'
CACHE = ROOT / '.market-details-cache.json'
SECTORS = {'BK0486': '传媒', 'BK0433': '农林牧渔', 'BK0464': '石油石化', 'BK1036': '半导体'}
PRICE_URL = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
FLOW_URL = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
UA = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36','Referer':'https://quote.eastmoney.com/'}
_request = requests.sessions.Session.request

def bounded_request(self, *args, **kwargs):
    kwargs['timeout'] = (8, 20)
    return _request(self, *args, **kwargs)
requests.sessions.Session.request = bounded_request

def get(url, params):
    params = {**params, 'ut': '7eea3edcaed734bea9cbfc24409ed989'}
    last = None
    for attempt in range(2):
        try:
            response = requests.get(url, params=params, headers=UA)
            response.raise_for_status()
            data=response.json()
            if data is None: raise ValueError('empty JSON')
            return data
        except Exception as exc:
            last=exc
    if curl_requests is not None:
        for attempt in range(2):
            try:
                response = curl_requests.get(url, params=params, headers=UA, timeout=20, impersonate='chrome')
                response.raise_for_status()
                data=response.json()
                if data is None: raise ValueError('empty JSON')
                return data
            except Exception as exc:
                last=exc
    raise last

def number(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None

def frame(df):
    return json.loads(df.to_json(orient='records', force_ascii=False, date_format='iso'))

def eastmoney_prices(secid, end):
    data = get(PRICE_URL, {'secid': secid, 'klt': 101, 'fqt': 0,
        'beg': (datetime.fromisoformat(end)-timedelta(days=100)).strftime('%Y%m%d'),
        'end': end.replace('-', ''), 'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'}).get('data')
    if not data or not data.get('klines'):
        raise ValueError('No dated Eastmoney price rows')
    names = ['open', 'close', 'high', 'low', 'volume_lots', 'turnover_yuan', 'amplitude_pct', 'return_pct', 'change', 'turnover_pct']
    return [{'date': a[0], **dict(zip(names, map(number, a[1:])))}
        for a in (row.split(',') for row in data['klines']) if a[0] <= end][-45:]

def prices(secid, end):
    if not secid.startswith('90.'):
        start=(datetime.fromisoformat(end)-timedelta(days=65)).strftime('%Y%m%d')
        try:
            rows=frame(ak.stock_zh_a_hist(symbol=secid.split('.')[1],start_date=start,end_date=end.replace('-','')))
            if rows:
                names={'开盘':'open','收盘':'close','最高':'high','最低':'low','成交额':'turnover_yuan','涨跌幅':'return_pct','换手率':'turnover_pct'}
                parsed=[{'date':r['日期'][:10],**{v:r.get(k) for k,v in names.items()}} for r in rows][-45:]
                if any(r['date']==end and r.get('close') is not None and r.get('turnover_yuan') is not None for r in parsed):
                    return parsed
        except Exception:
            pass
    return eastmoney_prices(secid, end)

def eastmoney_flows(secid, end):
    data = get(FLOW_URL, {'secid': secid, 'lmt': 0, 'klt': 101,
        'fields1': 'f1,f2,f3,f7', 'fields2': ','.join('f'+str(i) for i in range(51,66))}).get('data')
    if not data or not data.get('klines'):
        raise ValueError('No dated flow rows')
    return [{'date': a[0], 'net_yuan': number(a[1]), 'net_ratio_pct': number(a[6])}
        for a in (row.split(',') for row in data['klines']) if a[0] <= end][-35:]

def flows(secid, end):
    if not secid.startswith('90.'):
        try:
            rows=frame(ak.stock_individual_fund_flow(stock=secid.split('.')[1],market='sh' if secid.startswith('1.') else 'sz'))
            parsed=[{'date':r['日期'][:10],'net_yuan':r['主力净流入-净额'],'net_ratio_pct':r['主力净流入-净占比']} for r in rows if r.get('日期') and r['日期'][:10]<=end][-35:]
            if any(r['date']==end and r.get('net_yuan') is not None for r in parsed):
                return parsed
        except Exception:
            pass
    return eastmoney_flows(secid, end)

def stats(price, flow, benchmark, target):
    pm = {r['date']: r for r in price}; fm = {r['date']: r for r in flow}
    dates = [r['date'] for r in benchmark if r['date'] <= target]
    current = pm.get(target, {})
    out = {'data_date': target, 'close': current.get('close'),
        'return_pct': current.get('return_pct'), 'turnover_yuan': current.get('turnover_yuan'),
        'turnover_pct': current.get('turnover_pct'), 'day_net_yuan': fm.get(target, {}).get('net_yuan'), 'windows': {}}
    for n in [3,5,10]:
        needed = dates[-n:]
        valid_flow = len(needed)==n and all(fm.get(d,{}).get('net_yuan') is not None for d in needed)
        start = dates[-n-1] if len(dates)>n else None
        valid_price = start in pm and target in pm and pm[start].get('close') not in (None,0) and pm[target].get('close') is not None
        ret = (pm[target]['close']/pm[start]['close']-1)*100 if valid_price else None
        bm = {r['date']:r for r in benchmark}
        base_ret = (bm[target]['close']/bm[start]['close']-1)*100 if valid_price else None
        out['windows'][str(n)] = {
            'net_yuan': sum(fm[d]['net_yuan'] for d in needed) if valid_flow else None,
            'inflow_days': sum(fm[d]['net_yuan']>0 for d in needed) if valid_flow else None,
            'return_pct': ret, 'excess_pct_point': ret-base_ret if ret is not None else None,
            'observed_flow_days': sum(d in fm for d in needed)}
    previous = dates[-21:-1]
    amounts = [pm.get(d,{}).get('turnover_yuan') for d in previous]
    out['turnover_vs_prev20'] = current['turnover_yuan']/(sum(amounts)/20) if len(amounts)==20 and all(v is not None and v>0 for v in amounts) and current.get('turnover_yuan') is not None else None
    net = out['day_net_yuan']; amt = out['turnover_yuan']
    out['net_to_turnover_pct'] = net/amt*100 if net is not None and amt else None
    return out

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--date'); parser.add_argument('--reuse',action='store_true'); parser.add_argument('--offline',action='store_true'); args = parser.parse_args()
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    if args.date:
        cutoff = args.date
    elif now.weekday() < 5 and (now.hour, now.minute) >= (15, 10):
        cutoff = now.date().isoformat()
    else:
        d = now.date() - timedelta(days=1)
        while d.weekday() >= 5: d -= timedelta(days=1)
        cutoff = d.isoformat()
    liquidity = json.loads((ROOT/'dashboard.json').read_text()).get('liquidity',{})
    benchmark = [{'date':r['date'],'close':r['sh_close']} for r in liquidity.get('sh_index_series',[]) if r['date']<=cutoff and r.get('sh_close')]
    if len(benchmark)<22: raise ValueError('Dashboard must contain at least 22 verified benchmark trading dates')
    benchmark = sorted(benchmark, key=lambda r:r['date'])
    if len({r['date'] for r in benchmark}) != len(benchmark): raise ValueError('Duplicate benchmark trading dates')
    target = benchmark[-1]['date']
    brief = json.loads((ROOT/'brief.json').read_text())
    stocks = {x['code']:x['name'] for x in brief.get('candidate_pool',[]) if x.get('code')}
    jobs = {}
    for code in SECTORS:
        jobs['price_'+code] = lambda c=code: prices('90.'+c,target)
        jobs['flow_'+code] = lambda c=code: flows('90.'+c,target)
    for code in stocks:
        market = '1.' if code.startswith('6') else '0.'
        jobs['price_'+code] = lambda c=code,m=market: prices(m+c,target)
        jobs['flow_'+code] = lambda c=code,m=market: flows(m+c,target)
    dates = [r['date'] for r in benchmark][-5:]
    for day in dates:
        ds = day.replace('-','')
        jobs['limits_'+day] = lambda d=ds: frame(ak.stock_zt_pool_em(date=d))
        jobs['failed_'+day] = lambda d=ds: frame(ak.stock_zt_pool_zbgc_em(date=d))
    prev = benchmark[-2]['date']
    for day in [prev,target]: jobs['etf_'+day] = lambda d=day: frame(ak.fund_etf_scale_sse(date=d.replace('-','')))
    jobs['lhb_'+target] = lambda: frame(ak.stock_lhb_jgmmtj_em(start_date=target.replace('-',''),end_date=target.replace('-','')))
    jobs['unlocks'] = lambda: frame(ak.stock_restricted_release_detail_em(start_date=now.date().isoformat().replace('-',''),end_date=(now.date()+timedelta(days=30)).isoformat().replace('-','')))
    old = json.loads(OUTPUT.read_text()) if OUTPUT.exists() and OUTPUT.read_text().strip() else {}
    if CACHE.exists(): old.setdefault('datasets', {}).update(json.loads(CACHE.read_text()))
    datasets = old.get('datasets',{}).copy(); availability = []
    if args.reuse:
        for key in list(jobs):
            if key in datasets and datasets[key].get('rows') and any((r.get('date')==target or str(r.get('统计日期',''))[:10]==target) for r in datasets[key]['rows'] if isinstance(r,dict)):
                availability.append({'dataset':key,'status':'cached','rows':len(datasets[key]['rows']),'fetched_at':datasets[key]['fetched_at']}); del jobs[key]
    if args.offline:
        availability.extend({'dataset':k,'status':'error','reason':'Not available in saved responses'} for k in jobs); jobs={}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(f):k for k,f in jobs.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                rows = future.result()
                if not rows: raise ValueError('Empty response; not interpreted as zero')
                datasets[key] = {'fetched_at':now.isoformat(), 'rows':rows}
                CACHE.write_text(json.dumps(datasets,ensure_ascii=False))
                availability.append({'dataset':key,'status':'ok','rows':len(rows)}); print(key,len(rows),flush=True)
            except Exception as exc:
                availability.append({'dataset':key,'status':'error','reason':type(exc).__name__+': '+str(exc)[:180], 'retained':key in datasets}); print(key,type(exc).__name__,flush=True)
    def rows(key):return datasets.get(key,{}).get('rows',[])
    groups = []
    for mapping in [SECTORS,stocks]:
        group=[]
        for code,name in mapping.items():
            p,f=rows('price_'+code),rows('flow_'+code)
            if code in stocks and not any(r.get('date')==target for r in p):
                snapshot=next((r for r in rows('limits_'+target)+rows('failed_'+target) if r.get('代码')==code),None)
                if snapshot: p=p+[{'date':target,'close':snapshot.get('最新价'),'return_pct':snapshot.get('涨跌幅'),'turnover_yuan':snapshot.get('成交额'),'turnover_pct':snapshot.get('换手率')}]
            group.append({'code':code,'name':name,**stats(p,f,benchmark,target)})
        groups.append(group)
    limits=[]
    for day in dates:
        up,failed=rows('limits_'+day),rows('failed_'+day); upcodes={r['代码'] for r in up}; badcodes={r['代码'] for r in failed}
        if upcodes & badcodes: raise ValueError('Limit and failed pools overlap')
        limits.append({'date':day,'limit_count':len(up) if up else None,'failed_count':len(failed) if failed else None,'seal_rate_pct':len(up)/(len(up)+len(failed))*100 if up and failed else None})
    etf=[]; prevmap={r['基金代码']:r for r in rows('etf_'+prev) if r.get('基金代码')}
    for row in rows('etf_'+target):
        previous=prevmap.get(row.get('基金代码'))
        if previous and str(row.get('统计日期',''))[:10]==target and str(previous.get('统计日期',''))[:10]==prev:
            curr=number(row.get('基金份额')); before=number(previous.get('基金份额'))
            if curr is not None and before is not None and before>0:
                etf.append({'code':row['基金代码'],'name':row.get('基金简称',''),'shares':curr,'previous_shares':before,'delta_shares':curr-before,'change_pct':(curr/before-1)*100})
    if not etf and old.get('etf_comparison_dates') == [prev,target]: etf=old.get('etf_changes',[])
    etf.sort(key=lambda r:abs(r['delta_shares']),reverse=True)
    result={'updated_at':now.isoformat(),'data_date':target,'benchmark':'上证指数000001；未复权收盘收益，超额为百分点差','sector_scope':'东方财富板块代码：传媒、农林牧渔、石油石化、半导体；仅4个关注板块，含不同层级，不能加总为全市场，也不拼接新闻中的申万资金序列','limit_scope':'东方财富涨停专题池；接口文档注明不含ST及科创板，未验证北交所完整覆盖。与新闻口径独立；封板资金为快照','etf_scope':'上交所ETF；份额单位为份，差值为净份额变化，不是人民币净申购金额；仅比较同代码和明确日期','sectors':groups[0],'stocks':groups[1],'limit_history':limits,'etf_comparison_dates':[prev,target],'etf_changes':etf,'availability':availability,'datasets':datasets,'sources':[{'name':'东财日线接口','url':PRICE_URL},{'name':'东财历史资金接口','url':FLOW_URL},{'name':'东财涨停池','url':'https://quote.eastmoney.com/ztb/detail#type=ztgc'},{'name':'上交所ETF份额','url':'https://www.sse.com.cn/assortment/fund/etf/list/scale/'}]}
    result['datasets']={k:v for k,v in datasets.items() if not k.startswith('etf_')}
    for dataset in result['datasets'].values():
        rows_=dataset['rows']
        if rows_ and isinstance(rows_[0],dict) and 'date' in rows_[0]:
            ds=[r['date'] for r in rows_ if r.get('date')]
            if len(ds)!=len(set(ds)) or (ds and max(ds)>target): raise ValueError('Duplicate or future-dated history')
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print('SAVED',target,'sectors',len(groups[0]),'stocks',len(groups[1]),'ETF pairs',len(etf),flush=True)

if __name__=='__main__':main()
