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
    # Before the close, the latest complete daily dataset should be the previous weekday.
    # After 16:00, today's close is expected on normal weekdays.
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
        usable_sectors = [x for x in sectors if x.get('return_pct') is not None or x.get('day_net_yuan') is not None]
        availability = details.get('availability') or []
        errors = [x for x in availability if x.get('status') == 'error']
        retained = [x for x in errors if x.get('retained')]
        detail_summary = {
            'status': 'ok' if usable_sectors else 'degraded',
            'data_date': detail_date,
            'usable_sectors': len(usable_sectors),
            'dataset_errors': len(errors),
            'retained_cache_errors': len(retained),
        }
        if detail_date != expected:
            problems.append(f'资金/涨停明细数据过期：期望 {expected}，实际 {detail_date}')
        if not usable_sectors:
            problems.append('资金板块没有可用的当日板块数据')
        elif errors:
            warnings.append(f'资金明细部分接口失败 {len(errors)} 项；成功数据已发布，失败项保留缓存/明确标记')
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
    }
    data['data_quality'] = quality
    data['updated_at'] = now.strftime('%Y-%m-%d %H:%M')
    DASH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    print(json.dumps(quality, ensure_ascii=False, indent=2))
    if problems:
        raise SystemExit('Dashboard validation failed: ' + '；'.join(problems))


if __name__ == '__main__':
    main()
