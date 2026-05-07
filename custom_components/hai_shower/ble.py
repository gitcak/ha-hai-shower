"""Active BLE client for Hai Shower."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

from bleak import BleakClient, BleakError
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import UUIDS, payload_preview
from .models import (
    VALID_TRANSITIONS,
    HaiLifecycleDetail,
    HaiLifecycleState,
    HaiShowerState,
    HaiUsageRecord,
)
from .protocol import (
    COLOR_RGB,
    decrypt_characteristic,
    decrypt_characteristic_debug,
    encode_led_color,
    encode_led_config,
    encode_rtc_sync,
    encode_temp_threshold,
    encode_water_threshold,
    parse_usage_record,
)

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 15.0
# Brief pause before reconnect so BLE proxies (e.g. ESPHome) can finish GATT
# teardown; immediate reconnect often yields ESP_GATTC_OPEN_EVT status=133.
POST_DISCONNECT_RECONNECT_DELAY = 0.35


def decode_product_id(raw: bytes | bytearray) -> str:
    """Decode the plaintext product identifier into the cloud serial format."""
    payload = bytes(raw).rstrip(b"\x00")
    if not payload:
        return ""
    return payload.hex().upper()


async def async_read_product_id(
    hass: HomeAssistant, address: str
) -> str | None:
    """Connect to a device and read the plaintext product identifier."""
    device = bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
    )
    if device is None:
        return None

    try:
        client = await establish_connection(
            BleakClient, device, address, max_attempts=3,
        )
    except BleakError:
        return None

    try:
        raw = await client.read_gatt_char(UUIDS["product_id"].characteristic)
    except BleakError:
        return None
    finally:
        await _safe_disconnect(client)

    if not raw:
        return None
    return decode_product_id(raw)


async def _safe_disconnect(client: BleakClient | None) -> None:
    """Disconnect a client while ignoring teardown errors."""
    if client is None or not client.is_connected:
        return
    try:
        await client.disconnect()
    except Exception:
        return


class HaiShowerBleClient:
    """Active BLE client that connects, subscribes, reads, and decrypts."""

    def __init__(
        self, hass: HomeAssistant, address: str, device_key: list[int]
    ) -> None:
        self.hass = hass
        self.address = address
        self._key = device_key
        self._client: BleakClient | None = None
        self._temperature_callback: Callable[[int], None] | None = None
        self._shower_end_callback: Callable[[HaiUsageRecord | None], None] | None = None
        self._temperature_subscribed = False
        self._shower_end_subscribed = False
        self._state = HaiShowerState()
        self._usage_records: list[HaiUsageRecord] = []
        self._history_done: asyncio.Event = asyncio.Event()
        self._operation_lock: asyncio.Lock = asyncio.Lock()
        self._skip_version_reads = False
        self._shutting_down = False
        self._expected_disconnect_client: BleakClient | None = None
        self._post_disconnect_wait_until: float = 0.0
        self._pending_alert_config_write = False

    @property
    def state(self) -> HaiShowerState:
        return self._state

    @property
    def is_connected(self) -> bool:
        """Return whether the BLE client has an active connection."""
        return self._client is not None and self._client.is_connected

    @property
    def has_pending_alert_config_write(self) -> bool:
        """Return whether HA-managed alert settings still need device sync."""
        return self._pending_alert_config_write

    def _transition_state(
        self,
        lifecycle_state: HaiLifecycleState,
        *,
        detail: HaiLifecycleDetail | None = None,
        error: str | None = None,
        available: bool | None = None,
    ) -> None:
        """Record a lifecycle transition for the device.

        ``last_error`` is only updated when an explicit *error* value is
        provided (including ``None`` to clear it).  Non-error transitions
        preserve the previous ``last_error`` so diagnostic context survives
        recovery.

        Invalid transitions (per ``VALID_TRANSITIONS``) are logged as
        warnings but still applied so the integration never crashes from a
        state-machine bug.
        """
        prev = self._state.lifecycle_state
        allowed = VALID_TRANSITIONS.get(prev, frozenset())
        if lifecycle_state not in allowed:
            _LOGGER.warning(
                "Invalid lifecycle transition on %s: %s -> %s (detail=%s)",
                self.address,
                prev.value,
                lifecycle_state.value,
                detail,
            )
        self._state.lifecycle_state = lifecycle_state
        self._state.lifecycle_detail = detail
        if error is not None or lifecycle_state is HaiLifecycleState.ERROR:
            self._state.last_error = error
        if available is not None:
            self._state.available = available
            if available:
                self._state.last_seen_at = datetime.now(UTC)
        _LOGGER.debug(
            "Lifecycle on %s -> %s (detail=%s error=%s available=%s)",
            self.address,
            lifecycle_state.value,
            detail,
            error,
            self._state.available,
        )

    def _set_error_state(
        self, error: str, *, detail: HaiLifecycleDetail | None = None, available: bool = False
    ) -> None:
        """Move the device into the error lifecycle state."""
        self._transition_state(
            HaiLifecycleState.ERROR,
            detail=detail,
            error=error,
            available=available,
        )

    async def async_initialize(self) -> None:
        """Check BLE visibility."""
        self._transition_state(
            HaiLifecycleState.SETUP,
            detail=HaiLifecycleDetail.INITIALIZING_BLE_VISIBILITY,
            available=False,
        )
        if bluetooth.async_address_present(self.hass, self.address):
            _LOGGER.debug("Hai shower %s is visible", self.address)
        else:
            _LOGGER.debug("Hai shower %s is not currently visible", self.address)

    async def _ensure_connected(self) -> BleakClient:
        """Connect or return existing connection."""
        if self._client and self._client.is_connected:
            return self._client
        self._shutting_down = False

        gap_end = self._post_disconnect_wait_until
        if gap_end > 0:
            wait_s = gap_end - time.monotonic()
            if wait_s > 0:
                await asyncio.sleep(wait_s)

        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise BleakError(f"Device {self.address} not found")

        try:
            client = await establish_connection(
                BleakClient,
                device,
                self.address,
                max_attempts=3,
                disconnected_callback=self._handle_disconnect,
            )
        except Exception:
            raise
        _LOGGER.debug("Connected to %s", self.address)
        self._post_disconnect_wait_until = 0.0
        self._client = client
        await self._sync_rtc(client)
        return client

    def _handle_disconnect(self, _client: BleakClient) -> None:
        """Handle disconnect callbacks from Bleak."""
        _LOGGER.debug("Disconnected from %s", self.address)
        self._post_disconnect_wait_until = max(
            self._post_disconnect_wait_until,
            time.monotonic() + POST_DISCONNECT_RECONNECT_DELAY,
        )
        self._client = None
        self._temperature_subscribed = False
        self._shower_end_subscribed = False
        if self._shutting_down:
            self._transition_state(
                HaiLifecycleState.SETUP,
                detail=HaiLifecycleDetail.SHUTDOWN_COMPLETE,
                available=False,
            )
            return
        if self._expected_disconnect_client is _client:
            self._expected_disconnect_client = None
            _LOGGER.debug("Expected disconnect completed on %s", self.address)
            return
        self._set_error_state(
            "ble_disconnected",
            detail=HaiLifecycleDetail.DISCONNECT_CALLBACK,
            available=False,
        )

    async def _safe_disconnect(self, client: BleakClient | None) -> None:
        """Disconnect a client while ignoring teardown errors."""
        had_live = client is not None and client.is_connected
        if had_live:
            self._expected_disconnect_client = client
        try:
            await _safe_disconnect(client)
        except Exception as err:
            _LOGGER.debug("Disconnect cleanup failed for %s: %s", self.address, err)
        if had_live:
            self._post_disconnect_wait_until = max(
                self._post_disconnect_wait_until,
                time.monotonic() + POST_DISCONNECT_RECONNECT_DELAY,
            )

    async def _reset_connection(self) -> None:
        """Drop the current client so the next operation reconnects."""
        client = self._client
        self._client = None
        self._temperature_subscribed = False
        self._shower_end_subscribed = False
        await self._safe_disconnect(client)

    async def async_reset_runtime_monitoring(self) -> None:
        """Clear runtime notify state and disconnect after a completed shower.

        After an automatic shower-end sync, the connection should return to the
        normal idle poll model. Keeping the subscribed connection alive into the
        next idle refresh can trip ESPHome proxy `status=133` failures on the
        first post-shower poll.
        """
        async with self._operation_lock:
            client = self._client
            if client and client.is_connected:
                try:
                    if self._temperature_subscribed:
                        await client.stop_notify(UUIDS["water_temp"].characteristic)
                except Exception as err:
                    _LOGGER.debug(
                        "Failed to stop temperature notifications on %s: %s",
                        self.address,
                        err,
                    )
                try:
                    if self._shower_end_subscribed:
                        await client.stop_notify(UUIDS["shower_end"].characteristic)
                except Exception as err:
                    _LOGGER.debug(
                        "Failed to stop shower-end notifications on %s: %s",
                        self.address,
                        err,
                    )
            self._temperature_subscribed = False
            self._shower_end_subscribed = False
            await self._safe_disconnect(client)
            self._client = None

    async def _sync_rtc(self, client: BleakClient) -> None:
        """Write the current UTC epoch to the shower's RTC sync characteristic.

        The Hai app performs this on every connect to keep usage-record
        timestamps accurate.  The payload is a 4-byte little-endian UTC epoch
        encrypted with the same XOR key as other 4-byte config writes.
        """
        char = UUIDS["rtc_sync"]
        epoch = int(time.time())
        payload = encode_rtc_sync(epoch, self._key)
        try:
            await client.write_gatt_char(char.characteristic, payload, response=True)
            _LOGGER.debug(
                "RTC synced on %s to epoch %d (payload=%s)",
                self.address,
                epoch,
                payload_preview(payload),
            )
        except BleakError as err:
            _LOGGER.debug("RTC sync failed on %s: %s", self.address, err)

    async def async_disconnect(self) -> None:
        """Disconnect from the device."""
        async with self._operation_lock:
            await self._reset_connection()
            self._transition_state(
                HaiLifecycleState.SETUP,
                detail=HaiLifecycleDetail.DISCONNECTED,
                available=False,
            )

    async def async_shutdown(self) -> None:
        """Release BLE resources during integration unload."""
        async with self._operation_lock:
            self._shutting_down = True
            self._transition_state(
                HaiLifecycleState.SETUP,
                detail=HaiLifecycleDetail.SHUTDOWN_REQUESTED,
                available=False,
            )
            await self._reset_connection()

    async def async_refresh(self) -> HaiShowerState:
        """Read current state from the device.

        If the device is already connected and monitoring, the refresh stays
        in MONITORING so HA entities are not briefly marked unavailable every
        poll cycle.
        """
        async with self._operation_lock:
            already_connected = self._client is not None and self._client.is_connected
            if not already_connected:
                self._transition_state(
                    HaiLifecycleState.SETUP,
                    detail=HaiLifecycleDetail.REFRESH_CONNECTING,
                    available=False,
                )
            else:
                self._transition_state(
                    HaiLifecycleState.MONITORING,
                    detail=HaiLifecycleDetail.REFRESH_READING,
                )
            try:
                client = await self._ensure_connected()
                await self._read_battery(client)
                self._raise_if_disconnected(client)
                await self._read_temperature(client)
                self._raise_if_disconnected(client)
                await self._read_flow_rate(client)
                self._raise_if_disconnected(client)
                if not self._skip_version_reads:
                    await self._read_version(client)
                    self._raise_if_disconnected(client)
                await self._read_product_id(client)
                self._raise_if_disconnected(client)
                if (
                    (self._temperature_callback or self._shower_end_callback)
                    and not (self._temperature_subscribed or self._shower_end_subscribed)
                    and (
                        self._state.current_temp_centicelsius is not None
                        or self._state.current_flow_ml_per_sec is not None
                    )
                ):
                    await self._maybe_activate_runtime_subscriptions(client)
                # Re-push alert/LED config on every fresh idle connection.  The
                # device stores this config in volatile RAM and loses it on
                # firmware restart, but runtime evidence shows led_config writes
                # during active notifications can crash the shower head.
                if not already_connected or self._pending_alert_config_write:
                    wrote_alert_config = await self._write_alert_config_when_safe(
                        "refresh"
                    )
                    if not wrote_alert_config:
                        _LOGGER.debug(
                            "Alert config sync deferred on %s during refresh",
                            self.address,
                        )
                    else:
                        self._raise_if_disconnected(client)
                self._transition_state(
                    HaiLifecycleState.MONITORING,
                    detail=HaiLifecycleDetail.REFRESH_COMPLETE,
                    available=True,
                )
                # Disconnect after each poll when no notify subscriptions are
                # active.  Holding an idle connection between polls causes the
                # ESPHome proxy to drop it with status=133, which fires a
                # spurious disconnect_callback and briefly marks entities
                # unavailable.  When subscriptions are active (shower running)
                # the connection is intentionally kept alive for notifications.
                if not (self._temperature_subscribed or self._shower_end_subscribed):
                    await self._safe_disconnect(client)
                    self._client = None
            except BleakError as err:
                _LOGGER.debug("BLE read failed for %s: %s", self.address, err)
                await self._reset_connection()
                self._set_error_state(
                    str(err),
                    detail=HaiLifecycleDetail.REFRESH_BLE_ERROR,
                    available=False,
                )
            except Exception as err:
                _LOGGER.debug("Unexpected BLE error for %s: %s", self.address, err)
                await self._reset_connection()
                self._set_error_state(
                    str(err),
                    detail=HaiLifecycleDetail.REFRESH_UNEXPECTED_ERROR,
                    available=False,
                )
        return self._state

    async def _read_battery(self, client: BleakClient) -> None:
        """Read battery level as a plaintext UInt16LE millivolt value."""
        char = UUIDS["battery_level"]
        try:
            raw = await client.read_gatt_char(char.characteristic)
            if raw and len(raw) >= 2:
                level_mv = int.from_bytes(raw[:2], "little")
                self._state.battery_level_mv = level_mv
                _LOGGER.debug(
                    "Battery read on %s: %s mV (payload=%s)",
                    self.address,
                    level_mv,
                    payload_preview(raw),
                )
        except BleakError as err:
            _LOGGER.debug("Battery read failed: %s", err)

    async def _read_temperature(self, client: BleakClient) -> None:
        """Read and decrypt water temperature."""
        char = UUIDS["water_temp"]
        self._state.current_temp_centicelsius = None
        try:
            raw = await client.read_gatt_char(char.characteristic)
            debug = decrypt_characteristic_debug(char.characteristic, raw, self._key)
            value = debug["value"]
            if value is not None:
                self._state.current_temp_centicelsius = value
                _LOGGER.debug(
                    "Temperature read on %s: value=%s cC raw_len=%s sliced_raw=%s decrypted=%s reversed=%s idle_zeros=%s payload=%s",
                    self.address,
                    value,
                    debug["raw_len"],
                    debug["sliced_raw_hex"] or "<none>",
                    debug["decrypted_hex"] or "<none>",
                    debug["reversed"],
                    debug["idle_zeros"],
                    payload_preview(raw),
                )
            else:
                _LOGGER.debug(
                    "Temperature read on %s yielded no decoded value raw_len=%s sliced_raw=%s decrypted=%s reversed=%s idle_zeros=%s payload=%s",
                    self.address,
                    debug["raw_len"],
                    debug["sliced_raw_hex"] or "<none>",
                    debug["decrypted_hex"] or "<none>",
                    debug["reversed"],
                    debug["idle_zeros"],
                    payload_preview(raw),
                )
        except BleakError as err:
            _LOGGER.debug("Temperature read failed: %s", err)

    async def _read_flow_rate(self, client: BleakClient) -> None:
        """Read and decrypt water flow rate."""
        char = UUIDS["water_flow"]
        self._state.current_flow_ml_per_sec = None
        try:
            raw = await client.read_gatt_char(char.characteristic)
            debug = decrypt_characteristic_debug(char.characteristic, raw, self._key)
            value = debug["value"]
            if value is not None:
                self._state.current_flow_ml_per_sec = value
                _LOGGER.debug(
                    "Flow read on %s: value=%s mL/s raw_len=%s sliced_raw=%s decrypted=%s reversed=%s idle_zeros=%s payload=%s",
                    self.address,
                    value,
                    debug["raw_len"],
                    debug["sliced_raw_hex"] or "<none>",
                    debug["decrypted_hex"] or "<none>",
                    debug["reversed"],
                    debug["idle_zeros"],
                    payload_preview(raw),
                )
            else:
                _LOGGER.debug(
                    "Flow read on %s yielded no decoded value raw_len=%s sliced_raw=%s decrypted=%s reversed=%s idle_zeros=%s payload=%s",
                    self.address,
                    debug["raw_len"],
                    debug["sliced_raw_hex"] or "<none>",
                    debug["decrypted_hex"] or "<none>",
                    debug["reversed"],
                    debug["idle_zeros"],
                    payload_preview(raw),
                )
        except BleakError as err:
            _LOGGER.debug("Flow rate read failed: %s", err)

    async def _read_version(self, client: BleakClient) -> None:
        """Read and decrypt firmware version."""
        char = UUIDS["version"]
        try:
            raw = await client.read_gatt_char(char.characteristic)
            debug = decrypt_characteristic_debug(char.characteristic, raw, self._key)
            value = debug["value"]
            if value is not None:
                self._state.firmware_version = str(value)
                self._skip_version_reads = False
                _LOGGER.debug(
                    "Version read on %s: value=%s raw_len=%s sliced_raw=%s decrypted=%s reversed=%s idle_zeros=%s payload=%s",
                    self.address,
                    value,
                    debug["raw_len"],
                    debug["sliced_raw_hex"] or "<none>",
                    debug["decrypted_hex"] or "<none>",
                    debug["reversed"],
                    debug["idle_zeros"],
                    payload_preview(raw),
                )
        except BleakError as err:
            message = str(err)
            if "authorization" in message.lower():
                if not self._skip_version_reads:
                    _LOGGER.debug(
                        "Version read disabled on %s after authorization failure: %s",
                        self.address,
                        err,
                    )
                self._skip_version_reads = True
            _LOGGER.debug("Version read failed: %s", err)

    async def _read_product_id(self, client: BleakClient) -> None:
        """Read the plaintext product identifier."""
        char = UUIDS["product_id"]
        try:
            raw = await client.read_gatt_char(char.characteristic)
            if raw:
                product_id = self._decode_product_id(raw)
                self._state.product_id = product_id
                _LOGGER.debug(
                    "Product ID read on %s: %s (payload=%s)",
                    self.address,
                    product_id,
                    payload_preview(raw),
                )
        except BleakError as err:
            _LOGGER.debug("Product ID read failed: %s", err)

    def _decode_product_id(self, raw: bytes | bytearray) -> str:
        """Decode a readable product identifier into a stable string."""
        return decode_product_id(raw)

    def set_temperature_callback(
        self, callback: Callable[[int], None] | None
    ) -> None:
        """Persist the desired temperature callback across reconnects."""
        self._temperature_callback = callback

    def set_shower_end_callback(
        self, callback: Callable[[HaiUsageRecord | None], None] | None
    ) -> None:
        """Persist the desired shower-end callback across reconnects."""
        self._shower_end_callback = callback

    async def async_subscribe_temperature(
        self, callback: Callable[[int], None] | None = None
    ) -> None:
        """Subscribe to live temperature notifications."""
        async with self._operation_lock:
            if callback is not None:
                self._temperature_callback = callback
            if not (self._client and self._client.is_connected):
                self._transition_state(
                    HaiLifecycleState.SETUP,
                    detail=HaiLifecycleDetail.SUBSCRIBE_TEMPERATURE_CONNECTING,
                    available=False,
                )
            else:
                self._transition_state(
                    HaiLifecycleState.MONITORING,
                    detail=HaiLifecycleDetail.SUBSCRIBE_TEMPERATURE_CONNECTING,
                )
            try:
                client = await self._ensure_connected()
                await self._start_temperature_notify(client)
                self._transition_state(
                    HaiLifecycleState.MONITORING,
                    detail=HaiLifecycleDetail.TEMPERATURE_SUBSCRIPTION_ACTIVE,
                    available=True,
                )
                _LOGGER.debug(
                    "Subscribed to temperature notifications on %s", self.address
                )
            except BleakError as err:
                await self._reset_connection()
                self._set_error_state(
                    str(err),
                    detail=HaiLifecycleDetail.SUBSCRIBE_TEMPERATURE_BLE_ERROR,
                    available=False,
                )
                raise

    async def async_subscribe_shower_end(
        self, callback: Callable[[HaiUsageRecord | None], None] | None = None
    ) -> None:
        """Subscribe to shower-end trigger notifications."""
        async with self._operation_lock:
            if callback is not None:
                self._shower_end_callback = callback
            if not (self._client and self._client.is_connected):
                self._transition_state(
                    HaiLifecycleState.SETUP,
                    detail=HaiLifecycleDetail.SUBSCRIBE_SHOWER_END_CONNECTING,
                    available=False,
                )
            else:
                self._transition_state(
                    HaiLifecycleState.MONITORING,
                    detail=HaiLifecycleDetail.SUBSCRIBE_SHOWER_END_CONNECTING,
                )
            try:
                client = await self._ensure_connected()
                await self._start_shower_end_notify(client)
                self._transition_state(
                    HaiLifecycleState.MONITORING,
                    detail=HaiLifecycleDetail.SHOWER_END_SUBSCRIPTION_ACTIVE,
                    available=True,
                )
                _LOGGER.debug("Subscribed to shower-end on %s", self.address)
            except BleakError as err:
                await self._reset_connection()
                self._set_error_state(
                    str(err),
                    detail=HaiLifecycleDetail.SUBSCRIBE_SHOWER_END_BLE_ERROR,
                    available=False,
                )
                raise

    async def _start_temperature_notify(self, client: BleakClient) -> None:
        """Start or restore temperature notifications."""
        if self._temperature_subscribed:
            return
        char = UUIDS["water_temp"]

        def _on_notify(
            _sender: BleakGATTCharacteristic, data: bytearray
        ) -> None:
            debug = decrypt_characteristic_debug(
                char.characteristic, bytes(data), self._key
            )
            value = debug["value"]
            if value is not None:
                self._state.current_temp_centicelsius = value
                self._transition_state(
                    HaiLifecycleState.MONITORING,
                    detail=HaiLifecycleDetail.TEMPERATURE_NOTIFY,
                    available=True,
                )
                _LOGGER.debug(
                    "Temperature notify on %s: value=%s cC raw_len=%s sliced_raw=%s decrypted=%s reversed=%s idle_zeros=%s payload=%s",
                    self.address,
                    value,
                    debug["raw_len"],
                    debug["sliced_raw_hex"] or "<none>",
                    debug["decrypted_hex"] or "<none>",
                    debug["reversed"],
                    debug["idle_zeros"],
                    payload_preview(data),
                )
                if self._temperature_callback:
                    self._temperature_callback(value)

        await client.start_notify(char.characteristic, _on_notify)
        self._temperature_subscribed = True

    async def _maybe_activate_runtime_subscriptions(self, client: BleakClient) -> None:
        """Best-effort activation of runtime notifies once live telemetry appears."""
        if self._temperature_callback is not None and not self._temperature_subscribed:
            try:
                await self._start_temperature_notify(client)
                _LOGGER.debug(
                    "Activated temperature notifications on %s during refresh",
                    self.address,
                )
            except BleakError as err:
                _LOGGER.debug(
                    "Temperature notify activation failed on %s during refresh: %s",
                    self.address,
                    err,
                )
        if self._shower_end_callback is not None and not self._shower_end_subscribed:
            try:
                await self._start_shower_end_notify(client)
                _LOGGER.debug(
                    "Activated shower-end notifications on %s during refresh",
                    self.address,
                )
            except BleakError as err:
                _LOGGER.debug(
                    "Shower-end notify activation failed on %s during refresh: %s",
                    self.address,
                    err,
                )

    async def _start_shower_end_notify(self, client: BleakClient) -> None:
        """Start or restore shower-end notifications."""
        if self._shower_end_subscribed:
            return
        char = UUIDS["shower_end"]

        def _on_notify(
            _sender: BleakGATTCharacteristic, data: bytearray
        ) -> None:
            payload = bytes(data)
            self._state.last_shower_end_notified_at = datetime.now(UTC)
            self._state.last_shower_end_payload_hex = payload.hex()
            self._state.last_shower_end_payload_len = len(payload)
            record: HaiUsageRecord | None = None
            try:
                record = parse_usage_record(payload, key=self._key)
            except ValueError as err:
                _LOGGER.debug(
                    "Shower-end payload parse failed on %s: %s payload=%s",
                    self.address,
                    err,
                    payload_preview(payload),
                )
            if record is not None:
                self._state.last_usage_record = record
                self._state.active_session_id = record.session_id
                self._state.session_duration_seconds = record.duration_seconds
                self._state.session_volume_milliliters = record.volume_milliliters
                self._state.last_session_duration_seconds = record.duration_seconds
                self._state.last_session_volume_ml = record.volume_milliliters
                self._state.last_session_avg_temp_cc = (
                    record.average_temp_centicelsius
                )
            self._transition_state(
                HaiLifecycleState.MONITORING,
                detail=HaiLifecycleDetail.SHOWER_END_TRIGGER,
                available=True,
            )
            _LOGGER.info(
                "Shower-end trigger received on %s: payload_len=%s payload=%s",
                self.address,
                len(payload),
                payload_preview(payload),
            )
            if self._shower_end_callback:
                self._shower_end_callback(record)

        await client.start_notify(char.characteristic, _on_notify)
        self._shower_end_subscribed = True

    async def async_trigger_history_sync(self) -> list[HaiUsageRecord]:
        """Execute the history sync handshake and collect usage records."""
        async with self._operation_lock:
            previous_usage_records = list(self._state.usage_records)
            previous_last_usage_record = self._state.last_usage_record
            self._usage_records = []
            self._history_done.clear()
            if not (self._client and self._client.is_connected):
                self._transition_state(
                    HaiLifecycleState.SETUP,
                    detail=HaiLifecycleDetail.HISTORY_SYNC_CONNECTING,
                    available=False,
                )
            else:
                self._transition_state(
                    HaiLifecycleState.MONITORING,
                    detail=HaiLifecycleDetail.HISTORY_SYNC_CONNECTING,
                )

            # Always establish a fresh GATT session before writing the trigger.
            # Runtime evidence (H26) shows the trigger write fails with
            # "Insufficient authorization" on stale connections but succeeds
            # on fresh ones.  Dropping and reconnecting here is cheap relative
            # to a failed sync and ensures the security context is clean.
            if self._client and self._client.is_connected:
                await self._reset_connection()

            client: BleakClient | None = None
            try:
                client = await self._ensure_connected()
            except BleakError as err:
                await self._reset_connection()
                self._set_error_state(
                    str(err),
                    detail=HaiLifecycleDetail.HISTORY_SYNC_CONNECT_BLE_ERROR,
                    available=False,
                )
                raise

            trigger = UUIDS["trigger_download"]
            usage = UUIDS["usage_record"]
            self._transition_state(
                HaiLifecycleState.SYNCING,
                detail=HaiLifecycleDetail.HISTORY_SYNC_START,
                available=True,
            )

            def _on_record(
                _sender: BleakGATTCharacteristic, data: bytearray
            ) -> None:
                if not data:
                    _LOGGER.debug("History sync end marker received")
                    self._history_done.set()
                    return
                try:
                    record = parse_usage_record(bytes(data), key=self._key)
                    if record:
                        self._usage_records.append(record)
                        # Do NOT write session_duration_seconds/volume here.
                        # These are *historical* records from the device's log;
                        # populating live-session fields from them produces a
                        # phantom "current session" in HA that never clears.
                        _LOGGER.debug(
                            "Usage record on %s: session=%d duration=%ds volume=%dmL payload=%s",
                            self.address,
                            record.session_id,
                            record.duration_seconds,
                            record.volume_milliliters,
                            payload_preview(data),
                        )
                    else:
                        self._history_done.set()
                except ValueError as err:
                    _LOGGER.warning("Bad usage record: %s", err)

            try:
                await client.start_notify(usage.characteristic, _on_record)
                await client.write_gatt_char(
                    trigger.characteristic, b"\x00", response=True
                )
                _LOGGER.debug("History sync triggered on %s", self.address)
                await asyncio.wait_for(self._history_done.wait(), timeout=30.0)
            except TimeoutError:
                _LOGGER.warning("History sync timed out on %s", self.address)
                self._usage_records = []
                self._set_error_state(
                    "history_sync_timeout",
                    detail=HaiLifecycleDetail.HISTORY_SYNC_TIMEOUT,
                    available=client.is_connected,
                )
            except BleakError as err:
                _LOGGER.debug("History sync failed for %s: %s", self.address, err)
                await self._reset_connection()
                self._set_error_state(
                    str(err),
                    detail=HaiLifecycleDetail.HISTORY_SYNC_BLE_ERROR,
                    available=False,
                )
            except Exception as err:
                _LOGGER.debug(
                    "Unexpected history sync error for %s: %s", self.address, err
                )
                await self._reset_connection()
                self._set_error_state(
                    str(err),
                    detail=HaiLifecycleDetail.HISTORY_SYNC_UNEXPECTED_ERROR,
                    available=False,
                )
            finally:
                try:
                    if client and client.is_connected:
                        await client.stop_notify(usage.characteristic)
                except Exception as err:
                    _LOGGER.debug(
                        "Failed to stop history notifications on %s: %s",
                        self.address,
                        err,
                    )

            if self._usage_records:
                self._state.usage_records = list(self._usage_records)
                self._state.last_usage_record = self._usage_records[-1]
            else:
                self._state.usage_records = previous_usage_records
                self._state.last_usage_record = previous_last_usage_record
            if self._state.lifecycle_state is HaiLifecycleState.SYNCING:
                self._transition_state(
                    HaiLifecycleState.MONITORING,
                    detail=HaiLifecycleDetail.HISTORY_SYNC_COMPLETE,
                    available=bool(client and client.is_connected),
                )
            elif self._state.lifecycle_state is HaiLifecycleState.ERROR:
                _LOGGER.debug(
                    "History sync left %s in error state (detail=%s)",
                    self.address,
                    self._state.lifecycle_detail,
                )
            return self._usage_records

    def _raise_if_disconnected(self, client: BleakClient) -> None:
        """Stop a refresh as soon as the client drops mid-cycle."""
        if not client.is_connected:
            raise BleakError("ble_disconnected_during_refresh")

    def _alert_defaults(self) -> dict[str, object]:
        """Build a full alert-config snapshot for composite writes.

        Some settings are not yet readable from the device, so composite writes
        use in-memory fallbacks until readback is validated.

        Enable states default to ``True`` when they have never been set
        (``None``), matching the official Hai app's factory defaults
        (``isWaterUseLightEnabled: true``, ``isWaterTempLightEnabled: true``).
        This ensures composite writes do not accidentally disable the shower
        LED when the user has only changed a threshold or color.
        """
        water_enabled = (
            self._state.water_alert_enabled
            if self._state.water_alert_enabled is not None
            else True
        )
        temp_enabled = (
            self._state.temp_alert_enabled
            if self._state.temp_alert_enabled is not None
            else True
        )
        return {
            "water_threshold_ml": int(
                round((self._state.water_alert_threshold_liters or 20.0) * 1000)
            ),
            "temp_threshold_cc": int(
                round((self._state.temp_alert_threshold_celsius or 38.0) * 100)
            ),
            "water_alert_enabled": water_enabled,
            "temp_alert_enabled": temp_enabled,
            "water_color_name": self._state.water_led_color or "White",
            "temp_color_name": self._state.temp_led_color or "White",
        }

    async def _write_characteristic(
        self,
        characteristic: str,
        payload: bytes,
        *,
        log_label: str,
    ) -> None:
        """Write a characteristic through the managed BLE connection."""
        client = await self._ensure_connected()
        await client.write_gatt_char(characteristic, payload, response=True)
        _LOGGER.debug(
            "Wrote %s on %s: payload=%s",
            log_label,
            self.address,
            payload_preview(payload),
        )

    def _alert_config_write_unsafe_now(self) -> bool:
        """Return whether alert writes should wait for the shower to go idle."""
        flow = self._state.current_flow_ml_per_sec
        return (
            self._temperature_subscribed
            or self._shower_end_subscribed
            or (flow is not None and flow > 0)
        )

    def _defer_alert_config_write(self, reason: str) -> None:
        """Queue one composite alert-config write for the next safe connection."""
        self._pending_alert_config_write = True
        _LOGGER.debug(
            "Deferring alert config write on %s until shower is idle (%s)",
            self.address,
            reason,
        )

    async def _write_alert_config_when_safe(self, reason: str) -> bool:
        """Write the composite alert config unless runtime telemetry is active."""
        if self._alert_config_write_unsafe_now():
            self._defer_alert_config_write(reason)
            return False
        await self._write_alert_config()
        self._pending_alert_config_write = False
        return True

    async def _write_alert_config(self, **overrides: object) -> None:
        """Write the composite led_config payload with updated settings."""
        assumed_targets: list[str] = []
        if (
            self._state.water_alert_enabled is None
            and "water_alert_enabled" not in overrides
        ):
            assumed_targets.append("water")
        if (
            self._state.temp_alert_enabled is None
            and "temp_alert_enabled" not in overrides
        ):
            assumed_targets.append("temperature")
        if assumed_targets:
            _LOGGER.debug(
                "Composite alert write on %s is assuming %s alert enable state(s) "
                "as enabled (matching Hai app factory defaults) because device-side "
                "readback is not implemented yet",
                self.address,
                ", ".join(assumed_targets),
            )
        settings = self._alert_defaults()
        settings.update(overrides)
        _LOGGER.debug(
            "Composite alert config on %s: water_threshold_ml=%d temp_threshold_cc=%d "
            "water_alert_enabled=%s temp_alert_enabled=%s water_color=%s temp_color=%s "
            "assumed_targets=%s",
            self.address,
            int(settings["water_threshold_ml"]),
            int(settings["temp_threshold_cc"]),
            bool(settings["water_alert_enabled"]),
            bool(settings["temp_alert_enabled"]),
            settings["water_color_name"],
            settings["temp_color_name"],
            ",".join(assumed_targets) or "<none>",
        )
        payload = encode_led_config(
            water_threshold_ml=int(settings["water_threshold_ml"]),
            temp_threshold_cc=int(settings["temp_threshold_cc"]),
            water_alert_enabled=bool(settings["water_alert_enabled"]),
            temp_alert_enabled=bool(settings["temp_alert_enabled"]),
            water_color_rgb=COLOR_RGB[str(settings["water_color_name"])],
            temp_color_rgb=COLOR_RGB[str(settings["temp_color_name"])],
            key=self._key,
        )
        await self._write_characteristic(
            UUIDS["led_config"].characteristic,
            payload,
            log_label="led_config",
        )

    async def async_write_water_threshold(self, value_liters: float) -> None:
        """Write water-use alert threshold to E6221503."""
        async with self._operation_lock:
            value_ml = int(round(value_liters * 1000))
            try:
                payload = encode_water_threshold(value_ml, self._key)
                if self._alert_config_write_unsafe_now():
                    self._state.water_alert_threshold_liters = value_liters
                    self._defer_alert_config_write("water_threshold")
                    return
                if self._pending_alert_config_write:
                    await self._write_alert_config(water_threshold_ml=value_ml)
                    self._pending_alert_config_write = False
                else:
                    await self._write_characteristic(
                        UUIDS["water_threshold"].characteristic,
                        payload,
                        log_label="water_threshold",
                    )
                self._state.water_alert_threshold_liters = value_liters
            except BleakError as err:
                await self._reset_connection()
                self._set_error_state(
                    str(err),
                    detail=HaiLifecycleDetail.REFRESH_BLE_ERROR,
                    available=False,
                )
                raise

    async def async_write_temp_threshold(self, value_celsius: float) -> None:
        """Write temperature alert threshold through the composite led_config."""
        async with self._operation_lock:
            try:
                value_cc = int(round(value_celsius * 100))
                _ = encode_temp_threshold(value_cc, self._key)
                if self._alert_config_write_unsafe_now():
                    self._state.temp_alert_threshold_celsius = value_celsius
                    self._defer_alert_config_write("temp_threshold")
                    return
                await self._write_alert_config(temp_threshold_cc=value_cc)
                self._pending_alert_config_write = False
                self._state.temp_alert_threshold_celsius = value_celsius
            except BleakError as err:
                await self._reset_connection()
                self._set_error_state(
                    str(err),
                    detail=HaiLifecycleDetail.REFRESH_BLE_ERROR,
                    available=False,
                )
                raise

    async def async_write_led_color(self, target: str, color_name: str) -> None:
        """Write LED alert color to E6221508 (water) or E6221509 (temp)."""
        async with self._operation_lock:
            if color_name not in COLOR_RGB:
                raise ValueError(f"Unknown Hai alert color: {color_name}")
            characteristic = UUIDS["water_led_color"] if target == "water" else UUIDS["temp_led_color"]
            try:
                payload = encode_led_color(COLOR_RGB[color_name], self._key)
                if self._alert_config_write_unsafe_now():
                    if target == "water":
                        self._state.water_led_color = color_name
                    else:
                        self._state.temp_led_color = color_name
                    self._defer_alert_config_write(f"{target}_led_color")
                    return
                if self._pending_alert_config_write:
                    override_key = (
                        "water_color_name" if target == "water" else "temp_color_name"
                    )
                    await self._write_alert_config(**{override_key: color_name})
                    self._pending_alert_config_write = False
                else:
                    await self._write_characteristic(
                        characteristic.characteristic,
                        payload,
                        log_label=f"{target}_led_color",
                    )
                if target == "water":
                    self._state.water_led_color = color_name
                else:
                    self._state.temp_led_color = color_name
            except BleakError as err:
                await self._reset_connection()
                self._set_error_state(
                    str(err),
                    detail=HaiLifecycleDetail.REFRESH_BLE_ERROR,
                    available=False,
                )
                raise

    async def async_write_alert_enable(self, target: str, enabled: bool) -> None:
        """Write alert enable/disable via the composite led_config buffer."""
        async with self._operation_lock:
            try:
                if target == "water":
                    if self._alert_config_write_unsafe_now():
                        self._state.water_alert_enabled = enabled
                        self._defer_alert_config_write("water_alert_enable")
                        return
                    await self._write_alert_config(water_alert_enabled=enabled)
                    self._pending_alert_config_write = False
                    self._state.water_alert_enabled = enabled
                else:
                    if self._alert_config_write_unsafe_now():
                        self._state.temp_alert_enabled = enabled
                        self._defer_alert_config_write("temp_alert_enable")
                        return
                    await self._write_alert_config(temp_alert_enabled=enabled)
                    self._pending_alert_config_write = False
                    self._state.temp_alert_enabled = enabled
            except BleakError as err:
                await self._reset_connection()
                self._set_error_state(
                    str(err),
                    detail=HaiLifecycleDetail.REFRESH_BLE_ERROR,
                    available=False,
                )
                raise
