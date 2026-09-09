"""Repair missing candidate-stock close snapshots with an independent Sina quote fallback.

This does not fabricate fund-flow data. It only fills the current trading day's price,
return and turnover when the primary historical provider failed and the quote can be
verified from Sina's end-of-day snapshot.
"""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
DETAILS = ROOT / 'market_details.json'
BRIEF = ROOT / 'brief.json'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36',
    'Referer': 'https://finance.sina.com.cn/',
}


def sina_snapshot(code: str, expected_date: str):
    symbol = ('sh' if code.startswith('6') else 'sz') + code
    r = requests.get(f'https://hq.sinajs.cn/list={symbol}', headers=HEADERS, timeout=(8, 15))
    r.raise_for_status()
    r.encoding = 'gbk'
    text = r.text.strip()
    if '="' not in text:
        raise ValueError('unexpected Sina quote format')
    payload = text.split('="', 1)[1].rsplit('"', 1)[0]
    fields = payload.split(',')
    if len(fields) < 32 or not fields[0]:
        raise ValueError('empty Sina quote')
    quote_date = fields[30]
    if quote_date != expected_date:
        raise ValueError(f'Sina quote date {quote_date}, expected {expected_date}')
    prev_close = float(fields[2])
    close = float(fields[3])
    turnover_yuan = float(fields[9])
    if close <= 0 or prev_close <= 0 or turnover_yuan < 0:
        raise ValueError('invalid Sina quote values')
    return {
        'close': close,
        'return_pct': (close / prev_close - 1) * 100,
        'turnover_yuan': turnover_yuan,
        'quote_source': '新浪财经收盘快照',
    }


def main():
    if not DETAILS.exists() or not DETAILS.read_text(encoding='utf-8').strip():
        raise SystemExit('market_details.json missing or empty')
    details = json.loads(DETAILS.read_text(encoding='utf-8'))
    brief = json.loads(BRIEF.read_text(encoding='utf-8'))
    expected = details.get('data_date')
    if not expected:
        raise SystemExit('market_details.json has no data_date')

    candidates = {x.get('code'): x.get('name') for x in brief.get('candidate_pool', []) if x.get('code')}
    stocks = details.get('stocks') or []
    by_code = {x.get('code'): x for x in stocks if x.get('code')}
    repaired = []
    failures = []

    for code, name in candidates.items():
        row = by_code.get(code)
        if row is None:
            row = {'code': code, 'name': name, 'data_date': expected, 'windows': {}}
            stocks.append(row)
            by_code[code] = row
        if row.get('close') is not None or row.get('day_net_yuan') is not None:
            continue
        try:
            snap = sina_snapshot(code, expected)
            row['close'] = snap['close']
            row['return_pct'] = snap['return_pct']
            row['turnover_yuan'] = snap['turnover_yuan']
            row['snapshot_source'] = snap['quote_source']
            repaired.append(code)
        except Exception as exc:
            failures.append(f'{code} {type(exc).__name__}: {exc}')

    details['stocks'] = stocks
    availability = details.setdefault('availability', [])
    now = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    for code in repaired:
        availability.append({'dataset': f'sina_snapshot_{code}', 'status': 'ok', 'rows': 1, 'fetched_at': now})
    for item in failures:
        availability.append({'dataset': 'sina_snapshot', 'status': 'error', 'reason': item, 'retained': False})

    DETAILS.write_text(json.dumps(details, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print('Sina stock snapshot repairs:', repaired or 'none')
    if failures:
        print('Sina snapshot failures:', failures)

    still_missing = [x.get('code') for x in stocks if x.get('code') in candidates and x.get('close') is None and x.get('day_net_yuan') is None]
    if still_missing:
        raise SystemExit('Candidate stocks still missing both price and flow: ' + ','.join(still_missing))


if __name__ == '__main__':
    main()
