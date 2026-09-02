"""Smoke-test the Massive REST API against a live key.

Run once a real MASSIVE_API_KEY exists — it confirms auth, the multi-ticker
snapshot request, and the nanosecond-to-second timestamp conversion in one
pass. Timestamps far in the future mean the divisor regressed; an
AttributeError means the attribute name regressed (see massive_client.py).

    cd backend && uv run python scripts/verify_massive.py
"""

import os
from datetime import UTC, datetime

from massive import RESTClient
from massive.rest.models import SnapshotMarketType

NANOS_PER_SECOND = 1_000_000_000
TICKERS = ["AAPL", "GOOGL", "MSFT", "NVDA", "TSLA"]


def main() -> None:
    client = RESTClient(api_key=os.environ["MASSIVE_API_KEY"])

    print(f"market: {client.get_market_status().market}")

    snapshots = client.get_snapshot_all(SnapshotMarketType.STOCKS, TICKERS)
    print(f"requested {len(TICKERS)}, received {len(snapshots)}")

    for snap in snapshots:
        trade = snap.last_trade
        if trade is None or trade.price is None:
            print(f"{snap.ticker}: no trade data")
            continue
        when = datetime.fromtimestamp(trade.sip_timestamp / NANOS_PER_SECOND, UTC)
        print(f"{snap.ticker}: ${trade.price:.2f} at {when:%Y-%m-%d %H:%M:%S} UTC")

    missing = set(TICKERS) - {s.ticker for s in snapshots}
    if missing:
        print(f"absent from response (unknown or untraded): {sorted(missing)}")


if __name__ == "__main__":
    main()
