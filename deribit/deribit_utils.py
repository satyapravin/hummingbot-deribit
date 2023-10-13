from decimal import Decimal
from typing import Any, Dict

from pydantic import Field, SecretStr

from hummingbot.client.config.config_data_types import BaseConnectorConfigMap, ClientFieldData
from hummingbot.core.data_type.trade_fee import TradeFeeSchema

DEFAULT_FEES = TradeFeeSchema(
    maker_percent_fee_decimal=Decimal("0.002"),
    taker_percent_fee_decimal=Decimal("0.002"),
)

CENTRALIZED = True

EXAMPLE_PAIR = "BTCUSDT"

class DeribitConfigMap(BaseConnectorConfigMap):
    connector: str = Field(default="deribit", const=True, client_data=None)
    deribit_client_id: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Deribit Client ID",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        ),
    )
    deribit_client_secret: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Deribit Client Secret",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        ),
    )

KEYS = DeribitConfigMap.construct()

def is_exchange_information_valid(exchange_info: Dict[str, Any]) -> bool:
    """
    Verifies if a trading pair is enabled to operate with based on its exchange information
    :param exchange_info: the exchange information for a trading pair
    :return: True if the trading pair is enabled, False otherwise
    """
    symbol = exchange_info.get("symbol")
    return symbol is not None and symbol.count("_") <= 1
