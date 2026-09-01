"""Market data subsystem for FinAlly.

Public API:
    PriceUpdate         - Immutable price snapshot dataclass
    PriceCache          - Thread-safe in-memory price store (latest price + rolling history)
    MarketDataSource    - Abstract interface for data providers
    create_market_data_source - Factory that selects simulator or Massive
    create_stream_router - FastAPI router factory for the SSE endpoint
    create_history_router - FastAPI router factory for the price history endpoint
"""

from .cache import HISTORY_MAXLEN, PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate
from .stream import create_history_router, create_stream_router

__all__ = [
    "PriceUpdate",
    "PriceCache",
    "HISTORY_MAXLEN",
    "MarketDataSource",
    "create_market_data_source",
    "create_stream_router",
    "create_history_router",
]
