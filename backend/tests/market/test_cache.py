"""Tests for PriceCache."""

from app.market.cache import PriceCache


class TestPriceCache:
    """Unit tests for the PriceCache."""

    def test_update_and_get(self):
        """Test updating and getting a price."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert cache.get("AAPL") == update

    def test_first_update_is_flat(self):
        """Test that the first update has flat direction."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.direction == "flat"
        assert update.previous_price == 190.50

    def test_direction_up(self):
        """Test price update with upward direction."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 191.00)
        assert update.direction == "up"
        assert update.change == 1.00

    def test_direction_down(self):
        """Test price update with downward direction."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 189.00)
        assert update.direction == "down"
        assert update.change == -1.00

    def test_remove(self):
        """Test removing a ticker from cache."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.remove("AAPL")
        assert cache.get("AAPL") is None

    def test_remove_nonexistent(self):
        """Test removing a ticker that doesn't exist."""
        cache = PriceCache()
        cache.remove("AAPL")  # Should not raise

    def test_get_all(self):
        """Test getting all prices."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)
        all_prices = cache.get_all()
        assert set(all_prices.keys()) == {"AAPL", "GOOGL"}

    def test_version_increments(self):
        """Test that version counter increments."""
        cache = PriceCache()
        v0 = cache.version
        cache.update("AAPL", 190.00)
        assert cache.version == v0 + 1
        cache.update("AAPL", 191.00)
        assert cache.version == v0 + 2

    def test_get_price_convenience(self):
        """Test the convenience get_price method."""
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("NOPE") is None

    def test_len(self):
        """Test __len__ method."""
        cache = PriceCache()
        assert len(cache) == 0
        cache.update("AAPL", 190.00)
        assert len(cache) == 1
        cache.update("GOOGL", 175.00)
        assert len(cache) == 2

    def test_contains(self):
        """Test __contains__ method."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        assert "AAPL" in cache
        assert "GOOGL" not in cache

    def test_custom_timestamp(self):
        """Test updating with a custom timestamp."""
        cache = PriceCache()
        custom_ts = 1234567890.0
        update = cache.update("AAPL", 190.50, timestamp=custom_ts)
        assert update.timestamp == custom_ts

    def test_price_rounding(self):
        """Test that prices are rounded to 2 decimal places."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.12345)
        assert update.price == 190.12


class TestPriceHistory:
    """Unit tests for PriceCache's rolling per-ticker history."""

    def test_history_accumulates_in_order(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00, timestamp=1.0)
        cache.update("AAPL", 191.00, timestamp=2.0)
        cache.update("AAPL", 192.00, timestamp=3.0)

        points = cache.get_history("AAPL")
        assert points == [(1.0, 190.00), (2.0, 191.00), (3.0, 192.00)]

    def test_history_is_bounded_and_oldest_first(self):
        cache = PriceCache(history_maxlen=5)
        for i in range(10):
            cache.update("AAPL", 100.0 + i, timestamp=float(i))

        points = cache.get_history("AAPL")
        assert len(points) == 5
        assert [ts for ts, _ in points] == [5.0, 6.0, 7.0, 8.0, 9.0]

    def test_history_is_empty_for_untracked_ticker(self):
        assert PriceCache().get_history("NOPE") == []

    def test_history_respects_limit_narrower_than_stored(self):
        cache = PriceCache()
        for i in range(10):
            cache.update("AAPL", 100.0 + i, timestamp=float(i))

        points = cache.get_history("AAPL", limit=3)
        assert [ts for ts, _ in points] == [7.0, 8.0, 9.0]

    def test_history_tracks_multiple_tickers_independently(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00, timestamp=1.0)
        cache.update("GOOGL", 175.00, timestamp=1.0)
        cache.update("AAPL", 191.00, timestamp=2.0)

        assert len(cache.get_history("AAPL")) == 2
        assert len(cache.get_history("GOOGL")) == 1

    def test_remove_clears_price_and_history(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.remove("AAPL")

        assert cache.get("AAPL") is None
        assert cache.get_history("AAPL") == []

    def test_remove_history_does_not_affect_other_tickers(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)
        cache.remove("AAPL")

        assert cache.get_history("GOOGL") != []
