import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
import copy
import time
import re
import json

from bidict import bidict

from hummingbot.connector.exchange.deribit.deribit_api_order_book_data_source import DeribitAPIOrderBookDataSource
from hummingbot.connector.exchange.deribit.deribit_user_stream_data_source import DeribitUserStreamDataSource
from hummingbot.connector.exchange.deribit import deribit_utils, deribit_web_utils as web_utils
from hummingbot.connector.exchange.deribit.deribit_auth import DeribitAuth
import hummingbot.connector.exchange.deribit.deribit_constants as CONSTANTS
from hummingbot.core.network_iterator import NetworkStatus

from hummingbot.connector.exchange_base import s_decimal_NaN
from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.connector.utils import combine_to_hb_trading_pair
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.trade_fee import TokenAmount, TradeFeeBase
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.utils.estimate_fee import build_trade_fee
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory

if TYPE_CHECKING:
    from hummingbot.client.config.config_helpers import ClientConfigAdapter
    
def get_api_error(error_string):
    match = re.search(r'Error: (.+)', error_string)
    
    if match:
        json_string = match.group(1)
        
        try:
            error_dict = json.loads(json_string)
            if "msg" in error_dict and "code" in error_dict:
                return error_dict
            else:
                return None
        except json.JSONDecodeError:
            return None
    else:
        return None

class DeribitExchange(ExchangePyBase):
    web_utils = web_utils

    def __init__(self,
                 client_config_map: "ClientConfigAdapter",
                 deribit_client_id: str,
                 deribit_client_secret: str,
                 trading_pairs: Optional[List[str]] = None,
                 trading_required: bool = True,
                 domain: str = "",
            ):

        self.deribit_client_id = deribit_client_id
        self.deribit_client_secret = deribit_client_secret
        self._trading_required = trading_required
        self._trading_pairs = trading_pairs
        self._domain = domain
        super().__init__(client_config_map)

    #region Props
    @property
    def authenticator(self):
        return DeribitAuth(
            client_id=self.deribit_client_id,
            client_secret=self.deribit_client_secret)
        
    @property
    def name(self) -> str:
        return "deribit"
    
    @property
    def rate_limits_rules(self):
        return CONSTANTS.RATE_LIMITS

    @property
    def domain(self):
        return ""

    @property
    def client_order_id_max_length(self):
        return CONSTANTS.MAX_ID_LEN

    @property
    def client_order_id_prefix(self):
        return CONSTANTS.CLIENT_ID_PREFIX

    @property
    def trading_rules_request_path(self):
        return CONSTANTS.INSTRUMENTS

    @property
    def trading_pairs_request_path(self):
        return CONSTANTS.INSTRUMENTS

    @property
    def check_network_request_path(self):
        return CONSTANTS.TIME

    @property
    def trading_pairs(self):
        return self._trading_pairs

    @property
    def is_cancel_request_in_exchange_synchronous(self) -> bool:
        return True

    @property
    def is_trading_required(self) -> bool:
        return True
    
    #endregion

    #region Misc
    def supported_order_types(self):
        return [OrderType.LIMIT, OrderType.MARKET]
    
    def _create_web_assistants_factory(self) -> WebAssistantsFactory:
        return web_utils.build_api_factory(
            throttler=self._throttler,
            time_synchronizer=self._time_synchronizer,
            auth=self._auth)

    def _is_request_exception_related_to_time_synchronizer(self, request_exception: Exception):
        print("[sync error]", request_exception)
        error_description = str(request_exception)
        is_time_synchronizer_related = '"code":"50113"' in error_description
        return is_time_synchronizer_related

    def _is_order_not_found_during_status_update_error(self, status_update_exception: Exception) -> bool:
        # TODO: implement this method correctly for the connector
        # The default implementation was added when the functionality to detect not found orders was introduced in the
        # ExchangePyBase class. Also fix the unit test test_lost_order_removed_if_not_found_during_order_status_update
        # when replacing the dummy implementation
        return False

    def _is_order_not_found_during_cancelation_error(self, cancelation_exception: Exception) -> bool:
        # TODO: implement this method correctly for the connector
        # The default implementation was added when the functionality to detect not found orders was introduced in the
        # ExchangePyBase class. Also fix the unit test test_cancel_order_not_found_in_the_exchange when replacing the
        # dummy implementation
        return False
    
    #endregion

    #region Streams
    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        d = DeribitAPIOrderBookDataSource(
            trading_pairs=self.trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory)
        return d
    
    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        d = DeribitUserStreamDataSource(
            auth=self._auth,
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
            domain=self._domain,
        )
        
        return d
        
    async def _user_stream_event_listener(self):
        """
        Listens to message in _user_stream_tracker.user_stream queue.
        """
        async for event_message in self._iter_user_event_queue():
            try:
                print("[USER EVT]")
                print(event_message)
                params = event_message.get("params")
                
                if params is None: return

                channel = params.get("channel")
                
                if "portfolio" in channel:
                    self._process_wallet_event_message(params["data"])
                        
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception("Unexpected error in user stream listener loop.")

    def _process_order_event_message(self, order_msg: Dict[str, Any]):
        """
        Updates in-flight order and triggers cancellation or failure event if needed.

        :param order_msg: The order event message payloadsto
        """
        order_status = CONSTANTS.WS_ORDER_STATE[order_msg["status"]]
        client_order_id = str(order_msg["clOrdId"])
        updatable_order = self._order_tracker.all_updatable_orders.get(client_order_id)

        if updatable_order is not None:
            new_order_update: OrderUpdate = OrderUpdate(
                trading_pair=updatable_order.trading_pair,
                update_timestamp=self.current_timestamp,
                new_state=order_status,
                client_order_id=client_order_id,
                exchange_order_id=order_msg["ordId"],
            )
            self._order_tracker.process_order_update(new_order_update)

    def _process_trade_event_message(self, trade_msg: Dict[str, Any]):
        """
        Updates in-flight order and trigger order filled event for trade message received. Triggers order completed
        event if the total executed amount equals to the specified order amount.

        :param trade_msg: The trade event message payload
        """

        client_order_id = str(trade_msg["clOrdId"])
        fillable_order = self._order_tracker.all_fillable_orders.get(client_order_id)

        if fillable_order is not None and "tradeId" in trade_msg:
            trade_update = self._parse_websocket_trade_update(trade_msg=trade_msg, tracked_order=fillable_order)
            if trade_update:
                self._order_tracker.process_trade_update(trade_update)
                
    def _process_wallet_event_message(self, wallet_msg: Dict[str, Any]):
        """
        Updates account balances.
        :param wallet_msg: The account balance update message payload
        """
        symbol = wallet_msg.get("currency", None)
        if symbol is not None:
            available = Decimal(str(wallet_msg["available_funds"]))
            self._account_available_balances[symbol] = available
            print(symbol, available)

    #endregion
    
    #region Symbols
    def _get_fee(self,
                 base_currency: str,
                 quote_currency: str,
                 order_type: OrderType,
                 order_side: TradeType,
                 amount: Decimal,
                 price: Decimal = s_decimal_NaN,
                 is_maker: Optional[bool] = None) -> TradeFeeBase:
 
        is_maker = is_maker or (order_type is OrderType.LIMIT_MAKER)
        fee = build_trade_fee(
            self.name,
            is_maker,
            base_currency=base_currency,
            quote_currency=quote_currency,
            order_type=order_type,
            order_side=order_side,
            amount=amount,
            price=price,
        )
        return fee
        
    async def _fetch_instruments(self):
        try:
            data = []
            
            for ccy in CONSTANTS.CURRENCIES:
                r = await self._api_get(
                    path_url=self.trading_pairs_request_path,
                    params={
                        "currency": ccy,
                        "extended": "true"
                    }
                )
                
                list = [item for item in r["result"] if "perpetual" in item["instrument_name"].lower()]
                data.extend(list)
            
            return data
        except Exception:
            self.logger().exception("There was an error requesting exchange info.")

    async def _initialize_trading_pair_symbol_map(self):
        # This has to be reimplemented because of multiple requests
        try:
            exchange_info = await self._fetch_instruments()
            self._initialize_trading_pair_symbols_from_exchange_info(exchange_info=exchange_info)
        except Exception:
            self.logger().exception("There was an error requesting exchange info.")
        
    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):
        data = exchange_info
        mapping = bidict()
        
        for symbol_data in data:
            base = symbol_data["base_currency"].upper()
            quote = symbol_data["quote_currency"].upper()
            name = symbol_data["instrument_name"]
            mapping[name] = combine_to_hb_trading_pair(base=base,quote=quote)
 
        self._set_trading_pair_symbol_map(mapping)
    
    async def _get_last_traded_price(self, trading_pair: str) -> float:
        exchange_symbol = await self.exchange_symbol_associated_to_pair(trading_pair)
        params = {"instrument_name": exchange_symbol}

        r = await self._api_get(
            path_url=CONSTANTS.TICKER,
            params=params,
        )

        price = float(r["result"]["last_price"])
        return price

    async def _update_trading_rules(self):
        exchange_info = await self._fetch_instruments()
        trading_rules_list = await self._format_trading_rules(exchange_info)
        self._trading_rules.clear()
        
        for trading_rule in trading_rules_list:
            self._trading_rules[trading_rule.trading_pair] = trading_rule
            
        self._initialize_trading_pair_symbols_from_exchange_info(exchange_info=exchange_info)

    async def _format_trading_rules(self, raw_trading_pair_info: List[Dict[str, Any]]) -> List[TradingRule]:
        trading_rules = []

        for info in raw_trading_pair_info:
            try:
                trading_rules.append(
                    TradingRule(
                        trading_pair=await self.trading_pair_associated_to_exchange_symbol(symbol=info["instrument_name"]),
                        min_order_size=Decimal(info["contract_size"]),
                        min_price_increment=Decimal(info["tick_size"]),
                        min_base_amount_increment=Decimal("1"),
                    )
                )
                
            except Exception as e:
                self.logger().exception(f"Error parsing the trading pair rule {info}. Skipping.")
                self.logger().exception(e)
             
        return trading_rules

    async def _update_trading_fees(self):
        """
        Update fees information from the exchange
        """
        pass

    #endregion
    
    #region orders
    async def _place_order(self,
                           order_id: str,
                           trading_pair: str,
                           amount: Decimal,
                           trade_type: TradeType,
                           order_type: OrderType,
                           price: Decimal,
                           **kwargs) -> Tuple[str, float]:
        
        data = {
            "instrument_name": await self.exchange_symbol_associated_to_pair(trading_pair),
            "amount": str(amount),
            "type": order_type.name.lower(),
            "label": order_id,
        }
        
        url = CONSTANTS.BUY if trade_type == TradeType.BUY else CONSTANTS.SELL
        
        if order_type.is_limit_type():
            data["price"] = str(price)

        r = await self._api_post(
            path_url=url,
            params=data,
            is_auth_required=True,
        )
        
        error = r.get("error")
        result = r.get("result")
        
        if error:
            code = error.get("code", "[NO CODE]")
            message = error.get("message", "[UKNOWN ERROR]")
            raise IOError(f"Error submitting order {order_id}: {code} - {message}")
        
        else:
            order = result.get("order", {})
            id = order.get("order_id", None)
            if id is None:
                raise IOError(f"Error submitting order {order_id}: Order missing in response!")
            return id


    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
        """
        This implementation specific function is called by _cancel, and returns True if successful
        """
        try:
            data = {
                "symbol": await self.exchange_symbol_associated_to_pair(tracked_order.trading_pair),
                "orderId": tracked_order.exchange_order_id
            }
            
            res = await self._api_post(
                path_url=CONSTANTS.CANCEL_ORDER,
                data=data,
                is_auth_required=True,
            )
            
            response_code = res["code"]

            if response_code != CONSTANTS.RET_CODE_OK:
                if response_code == CONSTANTS.RET_CODE_ORDER_NOT_EXISTS:
                    await self._order_tracker.process_order_not_found(order_id)
                    
                raise IOError(f"{res['code']} - {res['msg']}")

            return True
        except Exception as ex:
            api_err = get_api_error(str(ex))

            if api_err:
                if api_err["code"] == CONSTANTS.RET_CODE_ORDER_NOT_EXISTS:
                    await self._order_tracker.process_order_not_found(order_id)
                    self.logger().info(f"{order_id} not found, now marked as cancelled !")
                    return True
                    # raise IOError(f"{api_err['code']} - {api_err['msg']}")
                
            raise ex

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
        trade_updates = []

        if order.exchange_order_id is not None:
            try:
                all_fills_response = await self._request_order_fills(order=order)
                fills_data = all_fills_response.get("data", [])

                for fill_data in fills_data:
                    trade_update = self._parse_trade_update(trade_msg=fill_data, tracked_order=order)
                    trade_updates.append(trade_update)
            except IOError as ex:
                if not self._is_request_exception_related_to_time_synchronizer(request_exception=ex):
                    raise

        return trade_updates

    async def _request_order_fills(self, order: InFlightOrder) -> Dict[str, Any]:
        exchange_symbol = await self.exchange_symbol_associated_to_pair(order.trading_pair)
        data = {
            "orderId": order.exchange_order_id,
            "symbol": exchange_symbol,
        }
        
        res = await self._api_post(
            path_url=CONSTANTS.ORDER_FILLS,
            data=data,
            is_auth_required=True,
        )

        return res

    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        try:
            order_status_data = await self._request_order_status_data(tracked_order=tracked_order)
            order_msg = order_status_data["data"][0]
            client_order_id = str(order_msg["clientOrderId"])

            order_update: OrderUpdate = OrderUpdate(
                trading_pair=tracked_order.trading_pair,
                update_timestamp=self.current_timestamp,
                new_state=CONSTANTS.ORDER_STATE[order_msg["status"]],
                client_order_id=client_order_id,
                exchange_order_id=order_msg["orderId"],
            )

            return order_update

        except IOError as ex:
            if self._is_request_exception_related_to_time_synchronizer(request_exception=ex):
                order_update = OrderUpdate(
                    client_order_id=tracked_order.client_order_id,
                    trading_pair=tracked_order.trading_pair,
                    update_timestamp=self.current_timestamp,
                    new_state=tracked_order.current_state,
                )
            else:
                self.logger().error(ex)
                raise

        return order_update

    async def _request_order_status_data(self, tracked_order: InFlightOrder) -> Dict:
        exchange_symbol = await self.exchange_symbol_associated_to_pair(tracked_order.trading_pair)
        data = {
            "symbol": exchange_symbol,
            "clientOrderId": tracked_order.client_order_id
        }
        
        if tracked_order.exchange_order_id is not None:
            data["orderId"] = tracked_order.exchange_order_id

        resp = await self._api_post(
            path_url=CONSTANTS.ORDER_DETAILS,
            data=data,
            is_auth_required=True,
        )

        return resp
    
    def _parse_websocket_trade_update(self, trade_msg: Dict, tracked_order: InFlightOrder) -> TradeUpdate:
        # print("[WS TRADE]")
        # print(trade_msg)

        trade_id: str = str(trade_msg["tradeId"])

        fee = TradeFeeBase.new_spot_fee(
            fee_schema=self.trade_fee_schema(),
            trade_type=tracked_order.trade_type,
            percent_token= trade_msg["fillFeeCcy"],
            flat_fees=[TokenAmount(amount=Decimal(trade_msg["fillFee"]), token=trade_msg["fillFeeCcy"])]
        )
        
        exec_price = Decimal(trade_msg["fillPx"])
        exec_amt = Decimal(trade_msg["fillSz"])
        exec_time = int(trade_msg["fillTime"]) * 1e-3

        trade_update: TradeUpdate = TradeUpdate(
            trade_id=trade_id,
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=str(trade_msg["ordId"]),
            trading_pair=tracked_order.trading_pair,
            fill_timestamp=exec_time,
            fill_price=exec_price,
            fill_base_amount=Decimal(trade_msg["fillSz"]),
            fill_quote_amount=Decimal(exec_price * exec_amt),
            fee=fee,
        )

        return trade_update

    def _parse_trade_update(self, trade_msg: Dict, tracked_order: InFlightOrder) -> TradeUpdate:
        trade_id: str = str(trade_msg["fillId"])

        fee = TradeFeeBase.new_spot_fee(
            fee_schema=self.trade_fee_schema(),
            trade_type=tracked_order.trade_type,
            percent_token=trade_msg["feeCcy"],
            flat_fees=[TokenAmount(amount=Decimal(trade_msg["fees"]), token=trade_msg["feeCcy"])]
        )

        exec_price = Decimal(trade_msg["fillPrice"])
        exec_time = int(trade_msg["cTime"]) * 1e-3

        trade_update: TradeUpdate = TradeUpdate(
            trade_id=trade_id,
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=str(trade_msg["orderId"]),
            trading_pair=tracked_order.trading_pair,
            fill_timestamp=exec_time,
            fill_price=exec_price,
            fill_base_amount=Decimal(trade_msg["fillQuantity"]),
            fill_quote_amount=Decimal(trade_msg["fillTotalAmount"]),
            fee=fee,
        )

        return trade_update

    #endregion
    
    #region Balance
    async def _update_balances(self):
        self._account_available_balances.clear()
        self._account_balances.clear()
        
        for trading_pair in self._trading_pairs:
            print(trading_pair)
        
        for ccy in CONSTANTS.CURRENCIES:
            r = await self._api_request(
                    path_url=CONSTANTS.ACCOUNT,
                    is_auth_required=True,
                    params={
                        "currency": ccy
                    }
                )
            
            b = r["result"]

            self._update_balance_from_details(ccy, b)

    def _update_balance_from_details(self, name, details):
        total = Decimal(details["balance"])
        available = Decimal(details["available_funds"])

        self._account_balances[name] = total
        self._account_available_balances[name] = available
        
    #endregion
    
    #region Utils

    #endregion