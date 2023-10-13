import base64
import hmac
import time
import json
import requests

from urllib.parse import urlencode
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, Optional, List
from urllib.parse import urlencode

from hummingbot.connector.time_synchronizer import TimeSynchronizer
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTRequest, WSRequest
import hummingbot.connector.exchange.deribit.deribit_constants as CONSTANTS

class DeribitAuth(AuthBase):
    cred = { "expires_at": 0 }
    ws_cred = { "expires_at": 0 }

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    async def rest_authenticate(self, request: RESTRequest) -> RESTRequest:
        """
        Implement protected requests
        https://deribitlimited.github.io/apidoc/en/spot/#request-interaction

        :param request: the request to be configured for authenticated interaction

        :return: The RESTRequest with auth information included
        """

        request.headers = self.auth_req(request)

        return request

    async def ws_authenticate(self, request: WSRequest) -> WSRequest:
        """
        This method is intended to configure a websocket request to be authenticated.
        """
        return request  # pass-through

    def get_ws_auth_payload(self) -> List[Dict[str, Any]]:
        return {
            "jsonrpc": "2.0",
            "id": 9929,
            "method": "public/auth",
            "params": {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret
            }
        }
        
    def get_ws_auth_refresh_payload(self, refresh_token) -> List[Dict[str, Any]]:
        return {
            "jsonrpc": "2.0",
            "id": 9929,
            "method": "public/auth",
            "params": {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token
            }
        }

    def auth_req(self, r: RESTRequest):
        cred = self.get_credentials()
        token = self.cred["access_token"]
        # print(token)
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        return headers
    
    def get_credentials(self):
        now = time.time()
        expires_at = self.cred["expires_at"]
        
        if "access_token" in self.cred and now < expires_at:
            print("cached...")
            return self.cred
        
        if "refresh_token" in self.cred and now > expires_at:
            print("refreshed...")
            r = requests.get(
                url=f"{CONSTANTS.API_BASE_URL}public/auth",
                params={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": self.cred["refresh_token"]
                },
            )
            
            self.cred = r.json()["result"]
            self.cred["expires_at"] = now + self.cred["expires_in"]
            return self.cred
            
        print("new...")
        r = requests.get(
            url=f"{CONSTANTS.API_BASE_URL}public/auth",
            params={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials"
            },
        )
        
        self.cred = r.json()["result"]
        self.cred["expires_at"] = now + self.cred["expires_in"]
        
        return self.cred
