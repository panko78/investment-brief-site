import json
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'dashboard.json'


def sanitize_tungsten(data):
    item = (data.get('commodities') or {}).get('tungsten')
    if not item or not item.get('series'):
        return False
    rows = [r for r in item['series'] if r.get('date') and isinstance(r.get('close'), (int, float))]
    vals = [float(r['close']) for r in rows]
    if len(vals) < 4:
        return False
    center = median(vals)
    # APT public quotes can move sharply, but a one-off >30% deviation from the
    # series median is much more likely to be a regex/article-value mismatch.
    lo, hi = center * 0.70, center * 1.30
    clean = [r for r in rows if lo <= float(r['close']) <= hi]
    changed = len(clean) != len(rows)
    if not clean:
        return False
    clean = sorted(clean, key=lambda r: r['date'])[-35:]
    item['series'] = clean
    item['latest'] = clean[-1]['close']
    if len(clean) >= 2 and clean[0]['close']:
        item['month_change'] = round((clean[-1]['close'] / clean[0]['close'] - 1) * 100, 2)
    if changed:
        item['source_note'] = (item.get('source_note') or '') + '；已自动剔除与序列中位数偏离超过30%的孤立异常报价。'
    return changed


def main():
    data = json.loads(DATA.read_text(encoding='utf-8'))
    changed = sanitize_tungsten(data)
    if changed:
        DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Sanitized dashboard outliers')
    else:
        print('No dashboard outliers detected')


if __name__ == '__main__':
    main()
