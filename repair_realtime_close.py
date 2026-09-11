import json
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
try:
    from curl_cffi import requests as curl_requests
except Exception:
    curl_requests = None

import collect_market_details as cmd

ROOT = Path(__file__).resolve().parent
DASH = ROOT / 'dashboard.json'
DETAILS = ROOT / 'market_details.json'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36', 'Referer': 'https://quote.eastmoney.com/'}
QUOTE_URL = 'https://push2.eastmoney.com/api/qt/stock/get'


def number(x, scale=1.0):
    try:
        if x in (None, '-', ''):
            return None
        return float(x) / scale
    except Exception:
        return None


def browser_get(url, params=None, timeout=12, encoding=None):
    last = None
    try:
        r = requests.get(url, params=params, headers=UA, timeout=timeout)
        r.raise_for_status()
        if encoding:
            r.encoding = encoding
        return r
    except Exception as exc:
        last = exc
    if curl_requests is not None:
        try:
            r = curl_requests.get(url, params=params, headers=UA, timeout=timeout, impersonate='chrome')
            r.raise_for_status()
            if encoding:
                r.encoding = encoding
            return r
        except Exception as exc:
            last = exc
    raise last


def em_snapshot(secid, target):
    params = {
        'secid': secid,
        'fields': 'f43,f48,f62,f168,f170,f184',
        'ut': '7eea3edcaed734bea9cbfc24409ed989',
    }
    last = None
    for i in range(4):
        try:
            data = (browser_get(QUOTE_URL, params=params, timeout=12).json() or {}).get('data') or {}
            close = number(data.get('f43'), 100)
            turnover = number(data.get('f48'))
            net = number(data.get('f62'))
            ret = number(data.get('f170'), 100)
            turnover_pct = number(data.get('f168'), 100)
            net_ratio = number(data.get('f184'), 100)
            if close is None or turnover is None or turnover <= 0 or net is None:
                raise RuntimeError('Eastmoney realtime snapshot missing required fields')
            return (
                {'date': target, 'close': close, 'return_pct': ret, 'turnover_yuan': turnover, 'turnover_pct': turnover_pct},
                {'date': target, 'net_yuan': net, 'net_ratio_pct': net_ratio},
            )
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (i + 1))
    raise last


def tencent_turnover(expected):
    # Prefer A-share indices that match the existing dashboard scope. If Tencent does
    # not expose either symbol, fall back to the broad exchange indices, whose quoted
    # amount field is still an exchange-level turnover snapshot.
    symbol_sets = [('sh000002', 'sz399107'), ('sh000001', 'sz399001')]
    last = None
    for symbols in symbol_sets:
        try:
            url = 'https://qt.gtimg.cn/q=' + ','.join(symbols)
            text = browser_get(url, timeout=10, encoding='gbk').text
            amounts = []
            dates = []
            for symbol in symbols:
                m = re.search(r'v_' + re.escape(symbol) + r'="([^"]+)"', text)
                if not m:
                    raise RuntimeError(f'Tencent quote missing {symbol}')
                fields = m.group(1).split('~')
                stamp = next((x for x in fields if re.fullmatch(r'\d{14}', str(x or ''))), None)
                triplet = next((x for x in fields if isinstance(x, str) and x.count('/') == 2 and x.split('/')[-1].isdigit() and len(x.split('/')[-1]) >= 9), None)
                if not stamp or not triplet:
                    raise RuntimeError(f'Tencent quote missing date/amount for {symbol}')
                dates.append(datetime.strptime(stamp[:8], '%Y%m%d').date().isoformat())
                amounts.append(float(triplet.split('/')[-1]) / 1e8)
            if any(d != expected for d in dates):
                raise RuntimeError(f'Tencent turnover date mismatch {dates} expected {expected}')
            total = round(sum(amounts), 2)
            if total <= 1000:
                raise RuntimeError(f'Tencent turnover implausible {total}')
            return total, '腾讯行情A股指数成交额备用源' if symbols[0] == 'sh000002' else '腾讯上证指数+深证成指成交额备用源'
        except Exception as exc:
            last = exc
    raise last


def merge_target(rows, row, target):
    out = [x for x in (rows or []) if isinstance(x, dict) and x.get('date') != target]
    out.append(row)
    out.sort(key=lambda x: x.get('date') or '')
    return out[-45:]


def repair_details(data, expected):
    if not DETAILS.exists():
        return []
    details = json.loads(DETAILS.read_text(encoding='utf-8'))
    if details.get('data_date') != expected:
        return []
    datasets = details.get('datasets') or {}
    benchmark = [
        {'date': r['date'], 'close': r['sh_close']}
        for r in (data.get('liquidity') or {}).get('sh_index_series', [])
        if r.get('date') and r.get('sh_close') is not None and r['date'] <= expected
    ]
    benchmark.sort(key=lambda x: x['date'])
    if len(benchmark) < 11 or benchmark[-1]['date'] != expected:
        return []

    repaired = []
    for group_name, prefix in (('sectors', '90.'), ('stocks', None)):
        group = details.get(group_name) or []
        for item in group:
            code = str(item.get('code') or '')
            if not code:
                continue
            price_key, flow_key = 'price_' + code, 'flow_' + code
            p = (datasets.get(price_key) or {}).get('rows') or []
            f = (datasets.get(flow_key) or {}).get('rows') or []
            need_price = not any(r.get('date') == expected and r.get('close') is not None and r.get('turnover_yuan') is not None for r in p if isinstance(r, dict))
            need_flow = not any(r.get('date') == expected and r.get('net_yuan') is not None for r in f if isinstance(r, dict))
            if not need_price and not need_flow:
                continue
            secid = ('90.' + code) if group_name == 'sectors' else (('1.' if code.startswith('6') else '0.') + code)
            try:
                price_row, flow_row = em_snapshot(secid, expected)
            except Exception as exc:
                print('Realtime snapshot repair failed', code, type(exc).__name__, exc)
                continue
            now_iso = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
            if need_price:
                p = merge_target(p, price_row, expected)
                datasets[price_key] = {'fetched_at': now_iso, 'rows': p}
            if need_flow:
                f = merge_target(f, flow_row, expected)
                datasets[flow_key] = {'fetched_at': now_iso, 'rows': f}
            rebuilt = {'code': code, 'name': item.get('name'), **cmd.stats(p, f, benchmark, expected)}
            item.clear(); item.update(rebuilt)
            repaired.append(code)

    if repaired:
        details['datasets'] = datasets
        details['updated_at'] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        sources = details.setdefault('sources', [])
        if not any(x.get('name') == '东财实时快照备用源' for x in sources if isinstance(x, dict)):
            sources.append({'name': '东财实时快照备用源', 'url': QUOTE_URL})
        DETAILS.write_text(json.dumps(details, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return repaired


def main():
    data = json.loads(DASH.read_text(encoding='utf-8'))
    expected = cmd.datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    # The workflow invokes this after 15:10 on same-day close runs. For safety,
    # derive the true expected date from the dashboard validator convention.
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    if now.weekday() >= 5 or (now.hour, now.minute) < (15, 10):
        d = now.date()
        if now.weekday() < 5:
            from datetime import timedelta
            d -= timedelta(days=1)
        while d.weekday() >= 5:
            from datetime import timedelta
            d -= timedelta(days=1)
        expected = d.isoformat()

    repaired = repair_details(data, expected)
    print('Realtime detail repairs:', repaired)

    liq = data.get('liquidity') or {}
    turnover = liq.get('turnover_series') or []
    latest = max((r.get('date') for r in turnover if r.get('date')), default=None)
    if latest != expected:
        try:
            value, source = tencent_turnover(expected)
            turnover = [r for r in turnover if r.get('date') != expected]
            turnover.append({'date': expected, 'turnover': value})
            turnover.sort(key=lambda r: r.get('date') or '')
            liq['turnover_series'] = turnover[-30:]
            liq.setdefault('freshness', {})['turnover_as_of'] = expected
            liq['turnover_source'] = '优先交易所官方汇总；历史失败切东方财富；收盘日最终备用：' + source
            mm = {x['date']: x['margin_balance'] for x in liq.get('margin_series', []) if x.get('date') and x.get('margin_balance') is not None}
            tm = {x['date']: x['turnover'] for x in liq.get('turnover_series', []) if x.get('date') and x.get('turnover') is not None}
            common = sorted(set(mm) & set(tm))[-30:]
            liq['series'] = [{'date': d, 'margin_balance': mm[d], 'turnover': tm[d]} for d in common]
            data['liquidity'] = liq
            data['updated_at'] = now.strftime('%Y-%m-%d %H:%M')
            DASH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            print('Realtime turnover repair:', expected, value, source)
        except Exception as exc:
            print('Realtime turnover repair failed:', type(exc).__name__, exc)


if __name__ == '__main__':
    main()
