"""
  Deribit connector
  =========================================================================
  Implementation based on official API docs.
  For more read https://docs.deribit.com/?python#deribit-api-v2-1-1
  =========================================================================
  Copyright(2023) arnold@arnique.net • Version 1.0.0
"""

from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.data_type.in_flight_order import OrderState

API_BASE_URL= "https://test.deribit.com/api/v2/"
WSS_BASE_URL = "wss://test.deribit.com/ws/api/v2"
DEFAULT_DOMAIN = ""

CLIENT_ID_PREFIX = "DRBT-"
MAX_ID_LEN = 32
SECONDS_TO_WAIT_TO_RECEIVE_MESSAGE = 60

DEFAULT_TIME_IN_FORCE = "normal"

CURRENCIES = ["USDC", "BTC", "ETH"]

# Public routes
INSTRUMENT = "public/get_instrument"
INSTRUMENTS = "public/get_instruments"
ORDER_BOOK = "public/get_order_book"
TICKER = "public/ticker"
TIME = "public/get_time"

# Private routes
ACCOUNT = "private/get_account_summary"
BUY = "private/buy"
CANCEL = "private/cancel"
EDIT = "private/edit"
ORDER_DETAILS = "private/get_order_state"
ORDER_TRADES = "private/get_user_trades_by_order"
SELL = "private/sell"

# Order Statuses
ORDER_STATE = {
    "open": OrderState.OPEN,
    "filled": OrderState.FILLED,
    "rejected": OrderState.FAILED,
    "cancelled": OrderState.CANCELED,
    "untriggered": OrderState.OPEN,
}

# Request error codes
RET_CODE_ORDER_NOT_EXISTS = 11044

def DefaultLimit(limit_id, limit = 5):
  return RateLimit(limit_id=limit_id,limit=limit,time_interval=1)

# Rate limits
RATE_LIMITS = [
    DefaultLimit(ACCOUNT),
    DefaultLimit(BUY),
    DefaultLimit(CANCEL),
    DefaultLimit(EDIT),
    DefaultLimit(INSTRUMENT),
    DefaultLimit(INSTRUMENTS, 20),
    DefaultLimit(ORDER_BOOK),
    DefaultLimit(ORDER_DETAILS),
    DefaultLimit(ORDER_TRADES),
    DefaultLimit(SELL),
    DefaultLimit(TICKER, 20),
    DefaultLimit(TIME),
]
