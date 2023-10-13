from typing import Callable, Optional
from urllib.parse import urljoin

import hummingbot.connector.exchange.deribit.deribit_constants as CONSTANTS
from hummingbot.connector.time_synchronizer import TimeSynchronizer
from hummingbot.connector.utils import TimeSynchronizerRESTPreProcessor
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_post_processors import WSPostProcessorBase, WSResponse
from hummingbot.core.web_assistant.rest_pre_processors import RESTPreProcessorBase
from hummingbot.core.web_assistant.rest_post_processors import RESTPostProcessorBase
from hummingbot.core.web_assistant.connections.data_types import RESTRequest, RESTResponse
from datetime import datetime, timezone, timedelta
import time
import json
import zlib

class DeribitRESTPreProcessor(RESTPreProcessorBase):

    async def pre_process(self, request: RESTRequest) -> RESTRequest:
        return request
    
class DeribitRESTPostProcessor(RESTPostProcessorBase):

    async def post_process(self, response: RESTResponse) -> RESTResponse:
        return response

class GZipCompressionWSPostProcessor(WSPostProcessorBase):
    """
    Performs the necessary response processing from both public and private websocket streams.
    """

    async def post_process(self, response: WSResponse) -> WSResponse:
        if not isinstance(response.data, bytes):
            # Unlike Market WebSocket, the return data of Account and Order Websocket are not compressed by GZIP.
            return response
        msg = decode_ws_data(response.data)
        json_msg = json.loads(msg)

        return WSResponse(data=json_msg)

def decode_ws_data(data):
    inflated_data = zlib.decompress(data, zlib.MAX_WBITS | 32)
    result = inflated_data.decode('utf-8')
    return result

def public_rest_url(path_url: str, domain: str = CONSTANTS.DEFAULT_DOMAIN) -> str:
    """
    Creates a full URL for provided REST endpoint

    :param path_url: a public REST endpoint
    :param domain: not required. Added only for compatibility.

    :return: the full URL to the endpoint
    """

    return urljoin(CONSTANTS.API_BASE_URL, path_url)


def private_rest_url(path_url: str, domain: str = CONSTANTS.DEFAULT_DOMAIN) -> str:
    return public_rest_url(path_url, domain)

def build_api_factory(
        throttler: Optional[AsyncThrottler] = None,
        time_synchronizer: Optional[TimeSynchronizer] = None,
        time_provider: Optional[Callable] = None,
        auth: Optional[AuthBase] = None, ) -> WebAssistantsFactory:
    
    throttler = throttler or create_throttler()
    time_synchronizer = time_synchronizer or TimeSynchronizer()
    time_provider = time_provider or (lambda: get_current_server_time(throttler=throttler))
    api_factory = WebAssistantsFactory(
            throttler=throttler,
            ws_post_processors=[GZipCompressionWSPostProcessor()],
            rest_pre_processors=[DeribitRESTPreProcessor()],
            rest_post_processors=[DeribitRESTPostProcessor()],
            auth=auth
        )
    return api_factory

def build_api_factory_without_time_synchronizer_pre_processor(throttler: AsyncThrottler) -> WebAssistantsFactory:
    api_factory = WebAssistantsFactory(
        throttler=throttler,
        rest_pre_processors=[DeribitRESTPreProcessor()],
        rest_post_processors=[DeribitRESTPostProcessor()]
        )
    return api_factory

def create_throttler() -> AsyncThrottler:
    return AsyncThrottler(CONSTANTS.RATE_LIMITS)

async def get_current_server_time(
    throttler: Optional[AsyncThrottler] = None,
    domain: str = CONSTANTS.DEFAULT_DOMAIN,
) -> float:
    ts = int(time.time() * 1e3)
    return ts
