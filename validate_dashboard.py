import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DASH = ROOT / 'dashboard.json'
DETAILS = ROOT / 'market_details.json'
SECTOR_RANKING = ROOT / 'sector_ranking.json'
BRIEF = ROOT / 'brief.json'
INDEX = ROOT / 'index.html'
TZ = ZoneInfo('Asia/Shanghai')


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
    if (now.hour, now.minute) >= (15, 10):
        return now.date().isoformat()
    return previous_weekday(now.date()).isoformat()


def latest_date(rows):
    vals = [r.get('date') for r in rows or [] if r.get('date')]
    return max(vals) if vals else None


def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=TZ)
    except Exception:
        return None


def age_minutes(now, value):
    dt = parse_dt(value)
    if not dt:
        return None
    return (now - dt.astimezone(TZ)).total_seconds() / 60


def live_market_window(now):
    return now.weekday() < 5 and (now.hour, now.minute) >= (9, 35) and (now.hour, now.minute) <= (15, 50)


def stock_complete(row, expected):
    if row.get('data_date') != expected:
        return False
    for key in ('close', 'return_pct', 'turnover_yuan', 'day_net_yuan'):
        if row.get(key) is None:
            return False
    windows = row.get('windows') or {}
    for n in (3, 5, 10):
        w = windows.get(str(n)) or {}
        if w.get('net_yuan') is None or w.get('inflow_days') is None:
            return False
        if w.get('return_pct') is None or w.get('excess_pct_point') is None:
            return False
        if (w.get('observed_flow_days') or 0) < n:
            return False
    return True


def live_sector_complete(row):
    if row.get('name') in (None, '') or row.get('day_net_yuan') is None:
        return False
    windows = row.get('windows') or {}
    # 3-day can occasionally be unavailable from the fallback provider; 5/10-day
    # and current-day values are mandatory. Missing 3-day is a warning, not a stale fallback.
    return all((windows.get(str(n)) or {}).get('net_yuan') is not None for n in (5, 10))


def dataset_fresh(details, key, now, max_age_min=45):
    ds = (details.get('datasets') or {}).get(key) or {}
    fetched_at = ds.get('fetched_at')
    age = age_minutes(now, fetched_at)
    return bool(fetched_at and age is not None and age <= max_age_min), fetched_at, age


def main():
    now = datetime.now(TZ)
    expected = expected_latest_market_date(now)
    today = now.date().isoformat()
    data = json.loads(DASH.read_text(encoding='utf-8'))
    liq = data.get('liquidity', {})

    sh_date = latest_date(liq.get('sh_index_series'))
    turnover_date = latest_date(liq.get('turnover_series'))
    margin_date = latest_date(liq.get('margin_series'))

    problems = []
    warnings = []
    modules = {}

    # 1) Liquidity / index freshness: date is the contract, not merely a recent file mtime.
    if sh_date != expected:
        problems.append(f'上证指数数据过期：期望 {expected}，实际 {sh_date}')
    if turnover_date != expected:
        problems.append(f'成交额数据过期：期望 {expected}，实际 {turnover_date}')
    if not margin_date:
        problems.append('两融余额没有可用数据')
    elif margin_date < previous_weekday(datetime.fromisoformat(expected).date()).isoformat():
        warnings.append(f'两融余额披露滞后：最新 {margin_date}，市场最新完整交易日 {expected}')
    modules['liquidity'] = {
        'status': 'ok' if sh_date == expected and turnover_date == expected else 'stale',
        'expected_date': expected,
        'sh_index_as_of': sh_date,
        'turnover_as_of': turnover_date,
        'margin_as_of': margin_date,
        'checked_at': liq.get('freshness', {}).get('checked_at'),
    }

    detail_summary = {'status': 'missing'}
    if DETAILS.exists():
        raw = DETAILS.read_text(encoding='utf-8').strip()
        if not raw:
            problems.append('market_details.json 为空')
        else:
            details = json.loads(raw)
            detail_date = details.get('data_date')
            details_age = age_minutes(now, details.get('updated_at'))
            sectors = details.get('sectors') or []
            sector_mode = details.get('sector_mode') or 'completed_history'
            stocks = details.get('stocks') or []
            limits = details.get('limit_history') or []
            live_limit = details.get('live_limit_snapshot') or {}
            etf_dates = details.get('etf_comparison_dates') or []
            etf_changes = details.get('etf_changes') or []
            availability = details.get('availability') or []
            transient_errors = [x for x in availability if x.get('status') == 'error']

            if details_age is None or details_age > 30:
                problems.append(f'market_details.json 本轮未刷新：updated_at={details.get("updated_at")}')

            # 2) Sector ranking: when the market is live, same-day + recent timestamp is required.
            if sector_mode == 'live_ranking':
                usable_sectors = [x for x in sectors if live_sector_complete(x)]
                sector_ok = len(usable_sectors) >= 8
                sector_updated = details.get('sector_ranking_updated_at')
                sector_age = age_minutes(now, sector_updated)
                if not sector_ok:
                    problems.append(f'动态行业资金数据不完整：可用 {len(usable_sectors)}/8')
                if live_market_window(now):
                    if not sector_updated or str(sector_updated)[:10] != today:
                        problems.append(f'动态行业榜不是当日数据：{sector_updated}')
                    elif sector_age is None or sector_age > 30:
                        problems.append(f'动态行业榜抓取已过期：{sector_updated}，距今约 {sector_age:.0f} 分钟')
                missing_three = sum(
                    1 for x in usable_sectors
                    if ((x.get('windows') or {}).get('3') or {}).get('net_yuan') is None
                )
                if missing_three:
                    warnings.append(
                        f'动态行业3日资金上游暂缺：{missing_three}/{len(usable_sectors)} 个行业；'
                        '当日、5日、10日继续使用最新数据，3日留空，不回退旧板块'
                    )
                modules['sectors'] = {
                    'status': 'ok' if sector_ok and (not live_market_window(now) or (sector_age is not None and sector_age <= 30 and str(sector_updated)[:10] == today)) else 'stale',
                    'mode': sector_mode,
                    'rows': len(sectors),
                    'usable_rows': len(usable_sectors),
                    'updated_at': sector_updated,
                    'source': details.get('sector_ranking_source'),
                }
            else:
                usable_sectors = [x for x in sectors if x.get('data_date') == expected and x.get('return_pct') is not None and x.get('turnover_yuan') is not None and x.get('day_net_yuan') is not None]
                sector_ok = len(usable_sectors) >= 4
                if not sector_ok:
                    problems.append(f'板块资金数据不完整：可用 {len(usable_sectors)}/4')
                modules['sectors'] = {
                    'status': 'ok' if sector_ok else 'stale',
                    'mode': sector_mode,
                    'rows': len(sectors),
                    'usable_rows': len(usable_sectors),
                    'as_of': expected,
                }

            # 3) Candidate stocks: latest completed-session contract plus fresh same-day fetch after close.
            complete_stocks = [x for x in stocks if stock_complete(x, expected)]
            incomplete_stocks = [x.get('code') or x.get('name') or '?' for x in stocks if not stock_complete(x, expected)]
            expected_stock_count = len(stocks)
            stocks_ok = expected_stock_count > 0 and len(complete_stocks) == expected_stock_count
            if expected_stock_count == 0:
                warnings.append('重点个股候选池为空')
            elif not stocks_ok:
                warnings.append(f'重点个股价格/资金/3-5-10日窗口不完整：完整 {len(complete_stocks)}/{expected_stock_count}；缺失 {",".join(incomplete_stocks)}')
            if expected == today and (now.hour, now.minute) >= (15, 10):
                stale_stock_datasets = []
                for x in stocks:
                    code = x.get('code')
                    if not code:
                        continue
                    for prefix in ('price_', 'flow_'):
                        fresh, fetched_at, age = dataset_fresh(details, prefix + code, now, 45)
                        if not fresh:
                            stale_stock_datasets.append(f'{prefix}{code}@{fetched_at or "missing"}')
                if stale_stock_datasets:
                    problems.append('收盘重点股数据不是本轮新抓取：' + ', '.join(stale_stock_datasets[:10]))
            modules['stocks'] = {
                'status': 'ok' if stocks_ok else 'degraded',
                'expected_date': expected,
                'usable': len(complete_stocks),
                'expected': expected_stock_count,
                'incomplete': incomplete_stocks,
            }

            # 4) Limit-up review: during/after the session a same-day live snapshot is mandatory.
            completed_limit_ok = any(x.get('date') == expected and x.get('limit_count') is not None and x.get('failed_count') is not None for x in limits)
            live_required = live_market_window(now)
            live_limit_age = age_minutes(now, live_limit.get('fetched_at'))
            live_limit_ok = (
                live_limit.get('status') == 'ok'
                and live_limit.get('date') == today
                and live_limit.get('limit_count') is not None
                and live_limit.get('failed_count') is not None
                and live_limit_age is not None
                and live_limit_age <= 30
            )
            if live_required and not live_limit_ok:
                problems.append(
                    '涨停板实时快照未更新：'
                    f'date={live_limit.get("date")}, fetched_at={live_limit.get("fetched_at")}, '
                    f'age_min={None if live_limit_age is None else round(live_limit_age, 1)}'
                )
            if not completed_limit_ok:
                problems.append('涨停/炸板完整交易日历史缺少最新应有日期')
            modules['limits'] = {
                'status': 'ok' if completed_limit_ok and (not live_required or live_limit_ok) else 'stale',
                'completed_as_of': expected if completed_limit_ok else latest_date(limits),
                'live_required': live_required,
                'live_date': live_limit.get('date'),
                'live_fetched_at': live_limit.get('fetched_at'),
                'live_limit_count': live_limit.get('limit_count'),
                'live_failed_count': live_limit.get('failed_count'),
                'live_age_minutes': None if live_limit_age is None else round(live_limit_age, 1),
            }

            # 5) ETF: disclosure can legitimately lag; never fail solely because SSE has not published today.
            etf_pair_ok = len(etf_dates) >= 2 and len(etf_changes) > 0
            etf_current = expected in etf_dates
            if not etf_pair_ok:
                warnings.append('ETF份额比较未形成可用的两日对比结果')
            elif not etf_current:
                warnings.append(f'上交所ETF份额尚未发布到 {expected}；当前使用最近已核验统计日 {etf_dates[-1]}')
            modules['etf'] = {
                'status': 'ok' if etf_pair_ok else 'degraded',
                'comparison_dates': etf_dates,
                'rows': len(etf_changes),
                'current_day_available': etf_current,
            }

            if detail_date != expected:
                problems.append(f'资金/完整交易日明细数据过期：期望 {expected}，实际 {detail_date}')

            detail_summary = {
                'status': 'ok' if sector_ok and stocks_ok and completed_limit_ok and etf_pair_ok and (not live_required or live_limit_ok) else 'degraded',
                'data_date': detail_date,
                'updated_at': details.get('updated_at'),
                'sector_mode': sector_mode,
                'usable_sectors': len(usable_sectors),
                'usable_stocks': len(complete_stocks),
                'expected_stocks': expected_stock_count,
                'incomplete_stocks': incomplete_stocks,
                'limit_up_ok': completed_limit_ok,
                'live_limit_ok': live_limit_ok,
                'etf_comparison_ok': etf_pair_ok,
                'etf_as_of': etf_dates[-1] if etf_dates else None,
                'etf_current_day_available': etf_current,
                'etf_change_rows': len(etf_changes),
                'transient_request_errors': len(transient_errors),
            }
    else:
        problems.append('market_details.json 不存在')

    # 6) Independent source file freshness prevents a silently retained sector_ranking.json.
    if SECTOR_RANKING.exists():
        sr = json.loads(SECTOR_RANKING.read_text(encoding='utf-8'))
        sr_age = age_minutes(now, sr.get('updated_at'))
        if live_market_window(now) and (str(sr.get('updated_at') or '')[:10] != today or sr_age is None or sr_age > 30):
            problems.append(f'sector_ranking.json 未在本轮刷新：{sr.get("updated_at")}')
    else:
        problems.append('sector_ranking.json 不存在')

    # 7) Frontend structural contract: stale data fixes are useless if UI still reads old fields.
    if INDEX.exists():
        html = INDEX.read_text(encoding='utf-8')
        for marker in ('live_limit_snapshot', 'limitFreshness', "cache:'no-store'", 'data_quality'):
            if marker not in html:
                problems.append(f'前端缺少新鲜度接线：{marker}')
    else:
        problems.append('index.html 不存在')

    # Brief is editorial and may not change every run, but it must not be from an older calendar day on weekdays.
    if BRIEF.exists():
        try:
            brief = json.loads(BRIEF.read_text(encoding='utf-8'))
            brief_date = brief.get('date')
            if now.weekday() < 5 and brief_date and brief_date < today:
                warnings.append(f'brief.json 仍为较早日期 {brief_date}；核心行情模块不受影响')
            modules['brief'] = {'status': 'ok' if not brief_date or brief_date >= today else 'degraded', 'date': brief_date, 'updated_at': brief.get('updated_at')}
        except Exception as exc:
            problems.append(f'brief.json 无法解析：{type(exc).__name__}')

    quality = {
        'checked_at': now.isoformat(),
        'expected_market_date': expected,
        'sh_index_as_of': sh_date,
        'turnover_as_of': turnover_date,
        'margin_as_of': margin_date,
        'modules': modules,
        'market_details': detail_summary,
        'warnings': warnings,
        'problems': problems,
        'publish_status': 'blocked' if problems else ('degraded' if warnings else 'ok'),
        'note': '所有核心模块分别校验实际日期与抓取时间；关键模块失败时禁止用旧缓存伪装成功。盘中涨停使用当日实时快照，完整交易日历史独立保存。'
    }
    data['data_quality'] = quality
    data['updated_at'] = now.strftime('%Y-%m-%d %H:%M')
    DASH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    print(json.dumps(quality, ensure_ascii=False, indent=2))
    if problems:
        raise SystemExit('Dashboard validation failed: ' + '；'.join(problems))


if __name__ == '__main__':
    main()
