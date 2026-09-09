"""Preserve the last verified SSE ETF-share comparison across fresh Actions runners.

The SSE same-day ETF share endpoint can lag or fail transiently.  The market-detail
collector deliberately treats ETF share change as a dated comparison, not RMB net
subscription.  This helper captures the last committed verified comparison before
collection and restores it only when the fresh collector cannot produce a valid
pair.  A pinned historical commit is used only as a one-time bootstrap if the
current repository copy has already been emptied by an earlier failed run.
"""
import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
MARKET = ROOT / "market_details.json"
BACKUP = ROOT / ".etf-last-verified.json"
BOOTSTRAP = (
    "https://raw.githubusercontent.com/panko78/investment-brief-site/"
    "cb82288ed84366c4b83ed623f8b4930b345fcde8/market_details.json"
)


def valid_payload(obj):
    dates = obj.get("etf_comparison_dates") or []
    rows = obj.get("etf_changes") or []
    return len(dates) == 2 and dates[0] < dates[1] and len(rows) > 0


def compact(obj):
    return {
        "etf_comparison_dates": obj.get("etf_comparison_dates") or [],
        "etf_changes": obj.get("etf_changes") or [],
    }


def load(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def capture():
    current = load(MARKET)
    if valid_payload(current):
        payload = compact(current)
        source = "current committed market_details.json"
    else:
        req = Request(BOOTSTRAP, headers={"User-Agent": "market-dashboard-etf-fallback/1.0"})
        with urlopen(req, timeout=20) as response:
            historical = json.loads(response.read().decode("utf-8"))
        if not valid_payload(historical):
            raise RuntimeError("Pinned ETF bootstrap does not contain a valid comparison")
        payload = compact(historical)
        source = "pinned last-known-good repository commit"
    BACKUP.write_text(json.dumps(payload, ensure_ascii=False))
    print("ETF fallback captured", payload["etf_comparison_dates"], len(payload["etf_changes"]), "from", source)


def restore():
    current = load(MARKET)
    if valid_payload(current):
        print("Fresh ETF comparison valid", current.get("etf_comparison_dates"), len(current.get("etf_changes") or []))
        return
    backup = load(BACKUP)
    if not valid_payload(backup):
        raise RuntimeError("Fresh ETF comparison invalid and no verified fallback is available")
    current.update(compact(backup))
    availability = current.setdefault("availability", [])
    availability.append({
        "dataset": "ETF份额比较",
        "status": "fallback",
        "source": "上次已核验并提交的上交所ETF份额比较",
        "date": backup["etf_comparison_dates"][-1],
        "rows": len(backup["etf_changes"]),
        "note": "当日ETF官方份额源暂不可用；保留最近一次已核验比较，不将旧份额变化冒充为当日净申购"
    })
    MARKET.write_text(json.dumps(current, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print("ETF fallback restored", backup["etf_comparison_dates"], len(backup["etf_changes"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["capture", "restore"])
    args = parser.parse_args()
    capture() if args.mode == "capture" else restore()


if __name__ == "__main__":
    main()
