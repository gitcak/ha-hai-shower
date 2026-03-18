"""One-time Hai cloud login for device key bootstrap.

Authenticates against AWS Cognito with USER_SRP_AUTH, then uses the resulting
session to fetch device key material from the Hai API. No ongoing cloud
dependency is required after setup.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
try:
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config as BotoConfig
except ImportError:  # pragma: no cover - patched in local unit tests
    boto3 = None
    UNSIGNED = None
    BotoConfig = None
try:
    from pycognito.aws_srp import AWSSRP
except ImportError:  # pragma: no cover - patched in local unit tests
    AWSSRP = None

from .const import key_summary, short_id

_LOGGER = logging.getLogger(__name__)

AWS_REGION = "us-east-1"
COGNITO_USER_POOL_ID = "us-east-1_WiAXuWlAU"
COGNITO_CLIENT_ID = "46o2a737hlpeo07q8fqv4oab2v"
COGNITO_ENDPOINT = f"https://cognito-idp.{AWS_REGION}.amazonaws.com/"
HAI_API_BASE = "https://ky6j6t7uhe.execute-api.us-east-1.amazonaws.com/prod"

AUTH_ERROR_HINTS = (
    "incorrect username or password",
    "not authorized",
    "notauthorizedexception",
    "invalid password",
    "invalid username",
    "user does not exist",
    "user not found",
    "usernot found",
    "credential",
    "secret hash",
    "unable to verify secret hash",
)


class HaiCloudClient:
    """Lightweight Cognito + Hai REST client for one-time key bootstrap."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._id_token: str | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def authenticate(self, username: str, password: str) -> None:
        """Authenticate with Cognito SRP and capture the id token."""
        await self._ensure_session()
        _LOGGER.debug("Authenticating to Hai cloud for user %s", short_id(username))
        if AWSSRP is None or boto3 is None or UNSIGNED is None or BotoConfig is None:
            raise HaiCloudAuthError(
                "SRP dependency missing. Install pycognito/boto3 for Hai cloud login."
            )
        try:
            result = await asyncio.to_thread(
                self._authenticate_srp_sync, username, password
            )
        except Exception as err:
            message = str(err)
            message_lower = message.lower()
            _LOGGER.debug(
                "Hai Cognito SRP auth failed for %s with %s: %s",
                short_id(username),
                type(err).__name__,
                message or "<no message>",
            )
            if any(hint in message_lower for hint in AUTH_ERROR_HINTS):
                raise HaiCloudAuthError(f"Authentication failed: {message}") from err
            raise HaiCloudConnectionError(
                "Unable to complete Cognito SRP auth"
            ) from err

        authentication_result = result.get("AuthenticationResult")
        if not isinstance(authentication_result, dict):
            raise HaiCloudResponseError(
                "Cognito SRP auth response omitted AuthenticationResult"
            )

        self._id_token = authentication_result.get("IdToken")
        if not self._id_token:
            raise HaiCloudResponseError("Cognito auth response omitted IdToken")
        _LOGGER.debug("Hai cloud authentication successful for %s", short_id(username))

    def _authenticate_srp_sync(self, username: str, password: str) -> dict[str, Any]:
        """Run the blocking AWSSRP setup and auth flow outside the event loop."""
        cognito_client = boto3.client(
            "cognito-idp",
            region_name=AWS_REGION,
            config=BotoConfig(signature_version=UNSIGNED),
        )
        auth = AWSSRP(
            username,
            password,
            COGNITO_USER_POOL_ID,
            COGNITO_CLIENT_ID,
            client=cognito_client,
        )
        return auth.authenticate_user()

    async def get_devices(self) -> list[dict[str, Any]]:
        """Fetch all devices for the authenticated user."""
        headers = self._auth_headers()
        data = await self._request_json(
            "get",
            f"{HAI_API_BASE}/devices",
            headers=headers,
            operation="fetch device list",
        )

        if isinstance(data, list):
            _LOGGER.debug("Hai cloud returned %d devices", len(data))
            return data
        if isinstance(data, dict) and "devices" in data:
            devices = data["devices"]
            if not isinstance(devices, list):
                raise HaiCloudResponseError(
                    "Device list response contained a non-list 'devices' value"
                )
            _LOGGER.debug("Hai cloud returned %d devices", len(devices))
            return devices
        if isinstance(data, dict):
            _LOGGER.debug("Hai cloud returned a single device object from /devices")
            return [data]
        raise HaiCloudResponseError("Device list response was not a list or object")

    async def get_device(self, device_id: str) -> dict[str, Any]:
        """Fetch a single device's full config including key material."""
        headers = self._auth_headers()
        _LOGGER.debug("Fetching Hai device details for %s", short_id(device_id))
        data = await self._request_json(
            "get",
            f"{HAI_API_BASE}/devices/{device_id}",
            headers=headers,
            operation=f"fetch device {short_id(device_id)}",
        )
        if not isinstance(data, dict):
            raise HaiCloudResponseError("Device details response was not an object")

        _LOGGER.debug(
            "Hai device details fetched for %s (keys: %s, key_summary: %s)",
            short_id(device_id),
            ",".join(sorted(data.keys())) if isinstance(data, dict) else type(data).__name__,
            key_summary(data.get("key")) if isinstance(data, dict) else "n/a",
        )
        return data

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an HTTP request and return decoded JSON with typed errors."""
        session = await self._ensure_session()
        try:
            async with session.request(
                method, url, headers=headers, json=json
            ) as resp:
                if resp.status in (401, 403):
                    raise HaiCloudAuthError(
                        f"Hai cloud rejected {operation} with HTTP {resp.status}"
                    )
                if resp.status >= 400:
                    raise HaiCloudResponseError(
                        f"Hai cloud {operation} failed with HTTP {resp.status}"
                    )
                try:
                    return await resp.json(content_type=None)
                except (
                    aiohttp.ContentTypeError,
                    ValueError,
                ) as err:
                    raise HaiCloudResponseError(
                        f"Hai cloud {operation} returned invalid JSON"
                    ) from err
        except HaiCloudAuthError:
            raise
        except HaiCloudResponseError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HaiCloudConnectionError(
                f"Unable to {operation} against Hai cloud"
            ) from err

    def _auth_headers(self) -> dict[str, str]:
        if not self._id_token:
            raise HaiCloudAuthError("Not authenticated")
        return {
            # The Hai API expects the Cognito id token, not the access token.
            # The native app sends "Bearer " + idToken; the API accepts both
            # bare and prefixed forms, but we match the app for safety.
            "Authorization": f"Bearer {self._id_token}",
            "Content-Type": "application/json",
        }


    async def get_shower_history(self, device_id: str) -> list[dict[str, Any]]:
        """Fetch shower session history from the cloud API.

        Phase 2 stub: the ``showers/{id}`` endpoint structure is not yet
        confirmed.  Returns an empty list until the request/response contract
        is traced and live-validated.
        """
        _LOGGER.warning(
            "Phase 2 stub: cloud history import for %s not implemented",
            short_id(device_id),
        )
        return []


class HaiCloudAuthError(Exception):
    """Raised when Hai cloud authentication fails."""


class HaiCloudConnectionError(Exception):
    """Raised when the Hai cloud cannot be reached."""


class HaiCloudResponseError(Exception):
    """Raised when the Hai cloud returns an unexpected response."""
