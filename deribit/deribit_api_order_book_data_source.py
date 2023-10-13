import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hummingbot.connector.exchange.deribit import deribit_constants as CONSTANTS, deribit_web_utils as web_utils
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.order_book_message import OrderBookMessage, OrderBookMessageType
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, WSJSONRequest, WSPlainTextRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger
import time

if TYPE_CHECKING:
    from hummingbot.connector.exchange.deribit.deribit_exchange import DeribitExchange
    
class DeribitAPIOrderBookDataSource(OrderBookTrackerDataSource):

    _logger: Optional[HummingbotLogger] = None

    def __init__(self,
                 trading_pairs: List[str],
                 connector: 'DeribitExchange',
                 api_factory: WebAssistantsFactory):
        super().__init__(trading_pairs)
        self._connector = connector
        self._api_factory = api_factory
        self._pong_response_event = None

    async def get_last_traded_prices(self,
                                     trading_pairs: List[str],
                                     domain: Optional[str] = None) -> Dict[str, float]:
        return await self._connector.get_last_traded_prices(trading_pairs=trading_pairs)

    async def _order_book_snapshot(self, trading_pair: str) -> OrderBookMessage:
        data = await self._request_order_book_snapshot(trading_pair)
        update_id: int = int(data["timestamp"])
        snapshot_timestamp: float = float(update_id)

        order_book_message_content = {
            "trading_pair": trading_pair,
            "update_id": update_id,
            "bids": [(price, amount) for price, amount in data.get("bids", [])],
            "asks": [(price, amount) for price, amount in data.get("asks", [])],
        }

        snapshot_msg: OrderBookMessage = OrderBookMessage(
            OrderBookMessageType.SNAPSHOT,
            order_book_message_content,
            snapshot_timestamp)

        return snapshot_msg

    async def _request_order_book_snapshot(self, trading_pair: str) -> Dict[str, Any]:
        rest_assistant = await self._api_factory.get_rest_assistant()
        symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
        
        r = await rest_assistant.execute_request(
            url=web_utils.public_rest_url(path_url=CONSTANTS.ORDER_BOOK),
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.ORDER_BOOK,
            params = {
                "depth": "10",
                "instrument_name": symbol
            }
        )
        
        # print("[BOOK]", r["result"]["timestamp"])
        
        return r["result"]
 
    async def _parse_order_book_diff_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        raise "[Deribit Method] (parse_order_book_diff_message) Not Implemented!"

    async def _parse_order_book_snapshot_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        params = raw_message["params"]
        data = params["data"]
        symbol = data["instrument_name"]
        timestamp = data["timestamp"]
        update_id = data["change_id"]
        trading_pair = await self._connector.trading_pair_associated_to_exchange_symbol(symbol=symbol)
        
        order_book_message_content = {
            "trading_pair": trading_pair,
            "update_id": update_id,
            "bids": [(bid[0], bid[1]) for bid in data["bids"]],
            "asks": [(ask[0], ask[1]) for ask in data["asks"]],
        }
        
        snapshot_msg: OrderBookMessage = OrderBookMessage(
            message_type=OrderBookMessageType.SNAPSHOT,
            content=order_book_message_content,
            timestamp=timestamp
        )
        
        # print("[SNAP]", snapshot_msg)
        message_queue.put_nowait(snapshot_msg)

    async def _parse_trade_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        print("[Deribit Method] (Trades) Not Implemented!")

    def _channel_originating_message(self, event_message: Dict[str, Any]) -> str:
        params = event_message.get("params")
        result = event_message.get("result")
        
        if params:
            if "book" in params["channel"]:
                print("[BOOK EVT]")
                return self._snapshot_messages_queue_key
                
        if result:
            if "trades" in result[0]:
                print("[TRADES EVT]")
                return self._trade_messages_queue_key
            
        self.logger().info("[UNKOWN OB EVT]")
        self.logger().info(event_message)
        return ""
 
    async def _process_websocket_messages(self, websocket_assistant: WSAssistant):
        while True:
            try:
                await asyncio.wait_for(
                    super()._process_websocket_messages(websocket_assistant=websocket_assistant),
                    timeout=CONSTANTS.SECONDS_TO_WAIT_TO_RECEIVE_MESSAGE)
            except asyncio.TimeoutError:
                if self._pong_response_event and not self._pong_response_event.is_set():
                    # The PONG response for the previous PING request was never received
                    raise IOError("The user stream channel is unresponsive (pong response not received)")
                self._pong_response_event = asyncio.Event()
                await self._send_ping(websocket_assistant=websocket_assistant)
    
    async def _subscribe_channels(self, ws: WSAssistant):
        try:

            for trading_pair in self._trading_pairs:
                symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)

                payload = {
                    "jsonrpc": "2.0",
                    "method": "public/subscribe",
                    "id": 42,
                    "params": {
                        "channels": [
                            f"trades.{symbol}.100ms",
                            f"book.{symbol}.none.20.100ms"
                        ]
                    }
                }

            subscribe_request = WSJSONRequest(payload=payload)
            await ws.send(subscribe_request)
            self.logger().info(f"Subscribed to Deribit public order book and trades...")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().exception("Unexpected error occurred subscribing to Bitget public streams...")
            raise
        
    async def _send_ping(self, websocket_assistant: WSAssistant):
        raise "[Deribit Method] Not Implemented!"

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws: WSAssistant = await self._api_factory.get_ws_assistant()
        await ws.connect(
            ws_url=CONSTANTS.WSS_BASE_URL, message_timeout=CONSTANTS.SECONDS_TO_WAIT_TO_RECEIVE_MESSAGE
        )
        return ws
