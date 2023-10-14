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
    ws: WSAssistant = None


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
        self.loop = asyncio.get_event_loop()
        self.loop.create_task(
            self._refresh_auth()
        )

    async def _authenticate(self, ws: WSAssistant):
        now = time.time()
        payload = self._auth.get_ws_auth_payload()
        login_request: WSJSONRequest = WSJSONRequest(payload=payload)
        print("[WS AUTH] Logging in...")
        await ws.send(login_request)
        response: WSResponse = await ws.receive()

        now = time.time()
        self.cred = response.data["result"]
        self.cred["expires_at"] = now + self.cred["expires_in"]
        print("[WS AUTH] Logged in!")
        
    async def _refresh_auth(self):
        while True:
            now = time.time()
            expires_at = self.cred["expires_at"]
            
            if self.ws is not None and expires_at > 0 and now > expires_at:
                payload = self._auth.get_ws_auth_refresh_payload(self.cred["refresh_token"])
                login_request: WSJSONRequest = WSJSONRequest(payload=payload)
                await self.ws.send(login_request)
                response: WSResponse = await self.ws.receive()

                self.cred = response.data["result"]
                self.cred["expires_at"] = now + self.cred["expires_in"]

            await asyncio.sleep(150)
        
    async def _process_websocket_messages(self, websocket_assistant: WSAssistant, queue: asyncio.Queue):
        while True:
            try:
                await asyncio.wait_for(
                    super()._process_websocket_messages(websocket_assistant=websocket_assistant, queue=queue),
                    timeout=CONSTANTS.SECONDS_TO_WAIT_TO_RECEIVE_MESSAGE)
            except asyncio.TimeoutError:
                await self.heartbeat_response(websocket_assistant)

    async def _process_event_message(self, event_message: Dict[str, Any], queue: asyncio.Queue):
        params = event_message.get("params")
        method = event_message.get("method")
        
        if method == "heartbeat":
            await self.heartbeat_response(self.ws)
            return
        
        print("[USER EV]", event_message)
        
        if "jsonrpc" in event_message:
            queue.put_nowait(event_message)

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
        print("[US PONG]")

    async def _subscribe_channels(self, websocket_assistant: WSAssistant):
        try:
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
        self.ws = await self._api_factory.get_ws_assistant()
        await self.ws.connect(
            ws_url=CONSTANTS.WSS_BASE_URL,
            message_timeout=CONSTANTS.SECONDS_TO_WAIT_TO_RECEIVE_MESSAGE)
        await self._authenticate(self.ws)
        await self.establish_hearbeat(self.ws)
        return self.ws
