import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DASH = ROOT / 'dashboard.json'
DETAILS = ROOT / 'market_details.json'


def previous_weekday(d):
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def expected_latest_market_date(now):
    if now.weekday() >= 5:
        d = now.date()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d.isoformat()
    if now.hour >= 16:
        return now.date().isoformat()
    return previous_weekday(now.date()).isoformat()


def latest_date(rows):
    vals = [r.get('date') for r in rows or [] if r.get('date')]
    return max(vals) if vals else None


def main():
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    expected = expected_latest_market_date(now)
    data = json.loads(DASH.read_text(encoding='utf-8'))
    liq = data.get('liquidity', {})

    sh_date = latest_date(liq.get('sh_index_series'))
    turnover_date = latest_date(liq.get('turnover_series'))
    margin_date = latest_date(liq.get('margin_series'))

    problems = []
    warnings = []
    if sh_date != expected:
        problems.append(f'上证指数数据过期：期望 {expected}，实际 {sh_date}')
    if turnover_date != expected:
        problems.append(f'成交额数据过期：期望 {expected}，实际 {turnover_date}')
    if not margin_date:
        problems.append('两融余额没有可用数据')
    elif margin_date < previous_weekday(datetime.fromisoformat(expected).date()).isoformat():
        warnings.append(f'两融余额披露滞后：最新 {margin_date}，市场最新完整交易日 {expected}')

    detail_summary = {'status': 'missing'}
    if DETAILS.exists():
        details = json.loads(DETAILS.read_text(encoding='utf-8'))
        detail_date = details.get('data_date')
        sectors = details.get('sectors') or []
        stocks = details.get('stocks') or []
        limits = details.get('limit_history') or []
        etf_dates = details.get('etf_comparison_dates') or []
        etf_changes = details.get('etf_changes') or []
        availability = details.get('availability') or []
        transient_errors = [x for x in availability if x.get('status') == 'error']

        usable_sectors = [x for x in sectors if x.get('data_date') == expected and x.get('return_pct') is not None and x.get('turnover_yuan') is not None and x.get('day_net_yuan') is not None]
        usable_stocks = [x for x in stocks if x.get('data_date') == expected and (x.get('close') is not None or x.get('day_net_yuan') is not None)]
        limit_ok = any(x.get('date') == expected and x.get('limit_count') is not None and x.get('failed_count') is not None for x in limits)
        etf_ok = expected in etf_dates and len(etf_dates) >= 2 and len(etf_changes) > 0

        detail_summary = {
            'status': 'ok' if len(usable_sectors) >= 4 and len(usable_stocks) >= 4 and limit_ok and etf_ok else 'degraded',
            'data_date': detail_date,
            'usable_sectors': len(usable_sectors),
            'usable_stocks': len(usable_stocks),
            'limit_up_ok': limit_ok,
            'etf_comparison_ok': etf_ok,
            'etf_change_rows': len(etf_changes),
            'transient_request_errors': len(transient_errors),
        }
        if detail_date != expected:
            problems.append(f'资金/涨停明细数据过期：期望 {expected}，实际 {detail_date}')
        if len(usable_sectors) < 4:
            problems.append(f'板块资金数据不完整：可用 {len(usable_sectors)}/4')
        if len(usable_stocks) < 4:
            warnings.append(f'重点个股明细部分不完整：可用 {len(usable_stocks)}/5')
        if not limit_ok:
            problems.append('涨停/炸板数据缺少最新完整交易日')
        if not etf_ok:
            warnings.append('ETF份额比较未形成可用的两日对比结果')
    else:
        problems.append('market_details.json 不存在')

    quality = {
        'checked_at': now.isoformat(),
        'expected_market_date': expected,
        'sh_index_as_of': sh_date,
        'turnover_as_of': turnover_date,
        'margin_as_of': margin_date,
        'market_details': detail_summary,
        'warnings': warnings,
        'problems': problems,
        'publish_status': 'blocked' if problems else ('degraded' if warnings else 'ok'),
        'note': '质量状态按最终可展示数据判断；单个上游请求失败但被重试、缓存或备用源完整补齐时，不再误报为降级。'
    }
    data['data_quality'] = quality
    data['updated_at'] = now.strftime('%Y-%m-%d %H:%M')
    DASH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    print(json.dumps(quality, ensure_ascii=False, indent=2))
    if problems:
        raise SystemExit('Dashboard validation failed: ' + '；'.join(problems))


if __name__ == '__main__':
    main()
