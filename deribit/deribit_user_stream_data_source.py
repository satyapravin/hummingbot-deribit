import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional
import time

from hummingbot.connector.exchange.deribit import deribit_constants as CONSTANTS
from hummingbot.connector.exchange.deribit.deribit_auth import DeribitAuth
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import WSJSONRequest, WSPlainTextRequest, WSResponse
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.exchange.deribit.deribit_exchange import DeribitExchange

class DeribitUserStreamDataSource(UserStreamTrackerDataSource):
    _logger: Optional[HummingbotLogger] = None
    cred = { "expires_at": 0 }

    def __init__(
        self,
        auth: DeribitAuth,
        trading_pairs: List[str],
        connector: 'DeribitExchange',
        api_factory: WebAssistantsFactory,
        domain: str = None,
    ):
        super().__init__()
        self._domain = domain
        self._api_factory = api_factory
        self._auth = auth
        self._trading_pairs = trading_pairs
        self._connector = connector
        self._pong_response_event = None

    async def _authenticate(self, ws: WSAssistant):
        """
        Authenticates user to websocket
        """
        await self._get_credentials(ws)
        
    async def _get_credentials(self, ws: WSAssistant):
        now = time.time()
        expires_at = self.cred["expires_at"]
        
        if "access_token" in self.cred and now < expires_at:
            print("cached...")
            return self.cred
        
        if "refresh_token" in self.cred and now > expires_at:
            print("refreshed...")
            payload = self._auth.get_ws_auth_refresh_payload(self.cred["refresh_token"])
            login_request: WSJSONRequest = WSJSONRequest(payload=payload)
            await ws.send(login_request)
            response: WSResponse = await ws.receive()
            m = response.data
            
            now = time.time()
            self.cred = m["result"]
            self.cred["expires_at"] = now + self.cred["expires_in"]
            return self.cred
            
        print("new...")
        payload = self._auth.get_ws_auth_payload()
        login_request: WSJSONRequest = WSJSONRequest(payload=payload)
        await ws.send(login_request)
        response: WSResponse = await ws.receive()
        m = response.data
        
        now = time.time()
        self.cred = m["result"]
        self.cred["expires_at"] = now + self.cred["expires_in"]
        
        return self.cred

    async def _process_websocket_messages(self, websocket_assistant: WSAssistant, queue: asyncio.Queue):
        while True:
            try:
                await asyncio.wait_for(
                    super()._process_websocket_messages(websocket_assistant=websocket_assistant, queue=queue),
                    timeout=CONSTANTS.SECONDS_TO_WAIT_TO_RECEIVE_MESSAGE)
            except asyncio.TimeoutError:
                if self._pong_response_event and not self._pong_response_event.is_set():
                    # The PONG response for the previous PING request was never received
                    raise IOError("The user stream channel is unresponsive (pong response not received)")
                self._pong_response_event = asyncio.Event()
                await self._send_ping(websocket_assistant=websocket_assistant)

    async def _process_event_message(self, event_message: Dict[str, Any], queue: asyncio.Queue):
        self.logger().info("[USER EVT]")
        # self.logger().info(event_message)

    async def _send_ping(self, websocket_assistant: WSAssistant):
        pass
    
    async def establish_hearbeat(self, websocket_assistant: WSAssistant):
        payload = {
            "jsonrpc": "2.0",
            "id": 9098,
            "method": "public/set_heartbeat",
            "params": {
                        "interval": 10
                        }
        }

        req: WSJSONRequest = WSJSONRequest(payload=payload)
        await websocket_assistant.send(req)
        
    async def heartbeat_response(self, websocket_assistant: WSAssistant) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": 8212,
            "method": "public/test",
            "params": {}
        }

        req: WSJSONRequest = WSJSONRequest(payload=payload)
        await websocket_assistant.send(req)

    async def _subscribe_channels(self, websocket_assistant: WSAssistant):
        try:
            cred = await self._get_credentials(websocket_assistant)
            
            channels = [
                "user.portfolio.btc",
                "user.portfolio.eth",
                "user.portfolio.usdc"
            ]
            
            payload = {
                "jsonrpc": "2.0",
                "method": "private/subscribe",
                "id": 42,
                "params": {
                    "channels": channels
                }
            }
            
            subscribe_request = WSJSONRequest(payload=payload)
            await websocket_assistant.send(subscribe_request)
            self.logger().info(f"Subscribed to Deribit user stream...")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().exception("Unexpected error occurred subscribing to Deribit user stream...")
            raise

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws: WSAssistant = await self._api_factory.get_ws_assistant()
        await ws.connect(
            ws_url=CONSTANTS.WSS_BASE_URL,
            message_timeout=CONSTANTS.SECONDS_TO_WAIT_TO_RECEIVE_MESSAGE)
        await self._authenticate(ws)
        await self.establish_hearbeat(ws)
        return ws
