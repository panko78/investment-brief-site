from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"


def main():
    """Fail the workflow if chart markup and JavaScript drift apart.

    This script intentionally does not rewrite index.html. Scheduled market-data
    updates must never modify the page template.
    """
    html = INDEX.read_text(encoding="utf-8")
    required = (
        'id="marginChart"',
        'id="turnoverChart"',
        'id="shIndexChart"',
        "drawTrendChart('margin','marginChart'",
        "drawTrendChart('turnover','turnoverChart'",
        "drawTrendChart('shIndex','shIndexChart'",
        "function dynamicBounds(values)",
    )
    missing = [token for token in required if token not in html]
    if missing:
        raise SystemExit(f"Liquidity chart wiring is incomplete: {missing}")
    if "getElementById('liquidityChart')" in html:
        raise SystemExit("Obsolete combined liquidityChart reference detected")

    print("Liquidity chart wiring is valid; index.html left unchanged")


if __name__ == "__main__":
    main()
