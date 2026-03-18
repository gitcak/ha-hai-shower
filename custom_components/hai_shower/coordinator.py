"""Coordinator for Hai Shower BLE state."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .ble import HaiShowerBleClient
from .const import (
    ALERT_COLORS,
    ALERT_OPTION_KEYS,
    MAX_STORED_USAGE_RECORDS,
    OPTION_TEMP_ALERT_ENABLED,
    OPTION_TEMP_ALERT_THRESHOLD_CELSIUS,
    OPTION_TEMP_LED_COLOR,
    OPTION_WATER_ALERT_ENABLED,
    OPTION_WATER_ALERT_THRESHOLD_LITERS,
    OPTION_WATER_LED_COLOR,
    stable_device_identity,
    usage_storage_key,
)
from .models import HaiLifecycleState, HaiShowerState, HaiUsageRecord
from .statistics import async_import_usage_records
from .usage_store import HaiUsageRecordStore

_LOGGER = logging.getLogger(__name__)
POST_HISTORY_SYNC_REFRESH_COOLDOWN_SECONDS = 20.0


class HaiShowerCoordinator(DataUpdateCoordinator[HaiShowerState]):
    """Owns BLE state retrieval for a configured showerhead."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        address: str,
        device_key: list[int],
        device_id: str | None = None,
        device_name: str | None = None,
    ) -> None:
        self._entry = entry
        self._address = address
        self._device_id = stable_device_identity(device_id, address)
        self._usage_storage_key = usage_storage_key(device_id, address)
        self._device_name = device_name or "Hai Smart Shower"
        self.client = HaiShowerBleClient(hass, address, device_key)
        self.client.state.device_name = self._device_name
        self._apply_persisted_alert_settings(getattr(entry, "options", {}))
        self._usage_store = HaiUsageRecordStore(hass)
        self._stored_usage_records: list[HaiUsageRecord] = []
        self._last_logged_error: str | None = None
        self._history_sync_task: asyncio.Task[None] | None = None
        self._suspend_refresh_until: float = 0.0
        super().__init__(
            hass,
            _LOGGER,
            name=f"hai_shower_{address}",
            update_interval=timedelta(seconds=30),
        )

    async def async_setup(self) -> None:
        """Initialize the underlying BLE client."""

        await self.client.async_initialize()
        await self._async_restore_usage_records()
        await self._async_subscribe_runtime_updates()

    async def async_shutdown(self) -> None:
        """Tear down the underlying BLE client."""

        if self._history_sync_task is not None:
            self._history_sync_task.cancel()
            self._history_sync_task = None
        await self.client.async_shutdown()

    async def _async_update_data(self) -> HaiShowerState:
        """Fetch fresh state from the showerhead."""
        now = self.hass.loop.time()
        if now < self._suspend_refresh_until:
            _LOGGER.debug(
                "Skipping refresh for %s during post-history-sync cooldown (%.1fs remaining)",
                self._address,
                self._suspend_refresh_until - now,
            )
            return self.client.state

        state = await self.client.async_refresh()
        if state.lifecycle_state is HaiLifecycleState.ERROR:
            detail_label = state.lifecycle_detail.value if state.lifecycle_detail else "unknown"
            error_key = f"{detail_label}:{state.last_error}"
            if error_key != self._last_logged_error:
                _LOGGER.warning(
                    "Hai shower %s entered %s (%s)",
                    self._address,
                    state.lifecycle_detail.value if state.lifecycle_detail else "error",
                    state.last_error or "unknown error",
                )
                self._last_logged_error = error_key
        elif self._last_logged_error is not None:
            _LOGGER.info("Hai shower %s recovered and is monitoring again", self._address)
            self._last_logged_error = None
        return state

    async def _async_subscribe_runtime_updates(self) -> None:
        """Subscribe to runtime BLE notifications that back exposed sensors.

        Subscriptions are skipped when the device is not currently connected,
        avoiding noisy warnings on every HA restart when the shower is out of
        range.  The first successful ``async_refresh()`` will re-attempt
        subscriptions once the device is reachable.
        """
        self.client.set_temperature_callback(self._handle_temperature_update)
        self.client.set_shower_end_callback(self._handle_shower_end_trigger)
        if not self.client.is_connected:
            _LOGGER.debug(
                "Deferring notification subscriptions for %s (not connected)",
                self._address,
            )
            return

        try:
            await self.client.async_subscribe_temperature()
        except Exception as err:
            _LOGGER.warning(
                "Unable to subscribe to temperature updates for %s: %s",
                self._address,
                err,
            )

        try:
            await self.client.async_subscribe_shower_end()
        except Exception as err:
            _LOGGER.warning(
                "Unable to subscribe to shower-end updates for %s: %s",
                self._address,
                err,
            )

    def _handle_temperature_update(self, _value: int) -> None:
        """Push live temperature updates into Home Assistant."""
        self.hass.loop.call_soon_threadsafe(
            self.async_set_updated_data, self.client.state
        )

    def _handle_shower_end_trigger(self, record: HaiUsageRecord | None) -> None:
        """Publish the latest completed session and then schedule sync."""
        self.hass.loop.call_soon_threadsafe(self._process_shower_end_trigger, record)

    def _process_shower_end_trigger(self, record: HaiUsageRecord | None) -> None:
        """Apply a shower-end record preview before running full sync."""
        if record is not None:
            self._apply_shower_end_record(record)
            self.async_set_updated_data(self.client.state)
        self._schedule_history_sync()

    def _schedule_history_sync(self) -> None:
        """Schedule a history sync if one is not already running."""
        state = self.client.state
        state.last_history_sync_requested_at = datetime.now(UTC)
        state.last_history_sync_trigger = "automatic"
        if self._history_sync_task is not None and not self._history_sync_task.done():
            _LOGGER.debug(
                "History sync already running for %s; skipping duplicate trigger",
                self._address,
            )
            state.last_history_sync_result = "already_running"
            return
        self._history_sync_task = self.hass.async_create_task(
            self._async_handle_history_sync(trigger="automatic")
        )

    async def _async_handle_history_sync(self, *, trigger: str) -> None:
        """Fetch latest session data after a shower-end trigger."""
        state = self.client.state
        now = datetime.now(UTC)
        state.last_history_sync_requested_at = state.last_history_sync_requested_at or now
        state.last_history_sync_started_at = now
        state.last_history_sync_trigger = trigger
        state.last_history_sync_result = "running"
        state.last_history_sync_error = None
        state.last_history_sync_records = 0
        existing_records = list(self._stored_usage_records)
        synced_records: list[HaiUsageRecord] = []
        newly_synced_records: list[HaiUsageRecord] = []
        try:
            synced_records = await self.client.async_trigger_history_sync()
            if synced_records:
                newly_synced_records = self._new_usage_records(
                    existing_records, synced_records
                )
                self._stored_usage_records = self._merge_usage_records(
                    existing_records, synced_records
                )
                await self._usage_store.async_save(
                    self._usage_storage_key, self._stored_usage_records
                )
                if trigger == "automatic" and newly_synced_records:
                    latest_record = max(
                        newly_synced_records,
                        key=self._usage_record_key,
                    )
                    self.hass.bus.async_fire(
                        "hai_shower_session_complete",
                        {
                            "address": self._address,
                            "session_id": latest_record.session_id,
                            "duration_seconds": latest_record.duration_seconds,
                            "volume_ml": latest_record.volume_milliliters,
                            "avg_temp_cc": latest_record.average_temp_centicelsius,
                            "start_time": latest_record.start_time.isoformat(),
                        },
                    )
            if self.client.state.lifecycle_state is HaiLifecycleState.ERROR:
                state.last_history_sync_result = "error"
                state.last_history_sync_error = self.client.state.last_error
            elif synced_records:
                state.last_history_sync_result = "success"
                state.last_history_sync_records = len(synced_records)
            else:
                state.last_history_sync_result = "no_records"
        except asyncio.CancelledError:
            state.last_history_sync_result = "cancelled"
            raise
        except Exception as err:
            _LOGGER.warning(
                "Automatic history sync failed for %s: %s", self._address, err
            )
            state.last_history_sync_result = "exception"
            state.last_history_sync_error = str(err)
        finally:
            self._suspend_refresh_until = (
                self.hass.loop.time() + POST_HISTORY_SYNC_REFRESH_COOLDOWN_SECONDS
            )
            state.last_history_sync_completed_at = datetime.now(UTC)
            if trigger == "automatic":
                await self.client.async_reset_runtime_monitoring()
            if self._stored_usage_records:
                self._apply_usage_records(self._stored_usage_records)
            if synced_records:
                try:
                    await async_import_usage_records(
                        self.hass,
                        self._address,
                        synced_records,
                        statistic_identity=self._device_id,
                    )
                except Exception as stats_err:
                    _LOGGER.debug(
                        "Statistics import skipped for %s: %s",
                        self._address,
                        stats_err,
                    )
            self.async_set_updated_data(self.client.state)

    async def async_trigger_history_sync(self) -> None:
        """Run a user-requested history sync immediately."""
        state = self.client.state
        state.last_history_sync_requested_at = datetime.now(UTC)
        state.last_history_sync_trigger = "manual"
        if self._history_sync_task is not None and not self._history_sync_task.done():
            _LOGGER.debug(
                "Manual history sync ignored for %s because one is already running",
                self._address,
            )
            state.last_history_sync_result = "already_running"
            await self._history_sync_task
            return
        self._history_sync_task = self.hass.async_create_task(
            self._async_handle_history_sync(trigger="manual")
        )
        await self._history_sync_task

    async def _async_restore_usage_records(self) -> None:
        """Restore persisted usage records into runtime state."""
        self._stored_usage_records = await self._usage_store.async_load(
            self._usage_storage_key,
            legacy_keys=(self._address,),
        )
        if self._stored_usage_records:
            _LOGGER.debug(
                "Restored %d usage records for %s",
                len(self._stored_usage_records),
                self._address,
            )
            self._apply_usage_records(self._stored_usage_records)

    def _apply_persisted_alert_settings(self, options: Mapping[str, object] | None) -> None:
        """Seed alert-setting state from config entry options."""
        if not options:
            return

        state = self.client.state

        water_threshold = options.get(OPTION_WATER_ALERT_THRESHOLD_LITERS)
        if isinstance(water_threshold, (int, float)) and not isinstance(water_threshold, bool):
            state.water_alert_threshold_liters = float(water_threshold)

        temp_threshold = options.get(OPTION_TEMP_ALERT_THRESHOLD_CELSIUS)
        if isinstance(temp_threshold, (int, float)) and not isinstance(temp_threshold, bool):
            state.temp_alert_threshold_celsius = float(temp_threshold)

        water_enabled = options.get(OPTION_WATER_ALERT_ENABLED)
        if isinstance(water_enabled, bool):
            state.water_alert_enabled = water_enabled

        temp_enabled = options.get(OPTION_TEMP_ALERT_ENABLED)
        if isinstance(temp_enabled, bool):
            state.temp_alert_enabled = temp_enabled

        water_color = options.get(OPTION_WATER_LED_COLOR)
        if isinstance(water_color, str) and water_color in ALERT_COLORS:
            state.water_led_color = water_color

        temp_color = options.get(OPTION_TEMP_LED_COLOR)
        if isinstance(temp_color, str) and temp_color in ALERT_COLORS:
            state.temp_led_color = temp_color

    def _alert_settings_options(self) -> dict[str, object]:
        """Serialize known alert-setting state for config entry persistence."""
        state = self.client.state
        options: dict[str, object] = {}
        if state.water_alert_threshold_liters is not None:
            options[OPTION_WATER_ALERT_THRESHOLD_LITERS] = state.water_alert_threshold_liters
        if state.temp_alert_threshold_celsius is not None:
            options[OPTION_TEMP_ALERT_THRESHOLD_CELSIUS] = state.temp_alert_threshold_celsius
        if state.water_alert_enabled is not None:
            options[OPTION_WATER_ALERT_ENABLED] = state.water_alert_enabled
        if state.temp_alert_enabled is not None:
            options[OPTION_TEMP_ALERT_ENABLED] = state.temp_alert_enabled
        if state.water_led_color is not None:
            options[OPTION_WATER_LED_COLOR] = state.water_led_color
        if state.temp_led_color is not None:
            options[OPTION_TEMP_LED_COLOR] = state.temp_led_color
        return options

    async def _async_persist_alert_settings(self) -> None:
        """Persist known alert-setting state into config entry options."""
        existing = dict(getattr(self._entry, "options", {}) or {})
        updated = {key: value for key, value in existing.items() if key not in ALERT_OPTION_KEYS}
        updated.update(self._alert_settings_options())
        if updated == existing:
            return
        self.hass.config_entries.async_update_entry(self._entry, options=updated)

    def _apply_usage_records(self, records: list[HaiUsageRecord]) -> None:
        """Apply a merged usage-record view to coordinator state."""
        state = self.client.state
        state.usage_records = list(records)
        if not records:
            state.last_usage_record = None
            return
        latest = records[-1]
        self._apply_shower_end_record(latest)
        # Cumulative fields for dashboard sensors
        state.shower_count = len(records)
        state.total_water_usage_ml = sum(r.volume_milliliters for r in records)

    def _apply_shower_end_record(self, record: HaiUsageRecord) -> None:
        """Apply a single completed-session record to summary state."""
        state = self.client.state
        state.last_usage_record = record
        state.active_session_id = record.session_id
        state.session_duration_seconds = record.duration_seconds
        state.session_volume_milliliters = record.volume_milliliters
        state.last_session_duration_seconds = record.duration_seconds
        state.last_session_volume_ml = record.volume_milliliters
        state.last_session_avg_temp_cc = record.average_temp_centicelsius

    def _merge_usage_records(
        self,
        existing: list[HaiUsageRecord],
        incoming: list[HaiUsageRecord],
    ) -> list[HaiUsageRecord]:
        """Merge persisted and newly synced records without duplicates.

        Keyed and sorted by session_id only — the start_time field is
        unreliable (device timestamps are offset from Unix epoch; H32) so
        using it for ordering risks misclassifying the latest session as
        older than stale records and trimming it under the storage cap.
        session_id is a monotonically increasing device counter and is the
        correct ordering key.
        """
        merged: dict[int, HaiUsageRecord] = {
            self._usage_record_key(record): record for record in existing
        }
        for record in incoming:
            merged[self._usage_record_key(record)] = record
        records = sorted(merged.values(), key=lambda record: record.session_id)
        if len(records) > MAX_STORED_USAGE_RECORDS:
            records = records[-MAX_STORED_USAGE_RECORDS:]
        return records

    def _new_usage_records(
        self,
        existing: list[HaiUsageRecord],
        incoming: list[HaiUsageRecord],
    ) -> list[HaiUsageRecord]:
        """Return synced records that were not already known locally."""
        existing_keys = {
            self._usage_record_key(record) for record in existing
        }
        new_records: list[HaiUsageRecord] = []
        seen_new_keys: set[int] = set()
        for record in incoming:
            key = self._usage_record_key(record)
            if key in existing_keys or key in seen_new_keys:
                continue
            seen_new_keys.add(key)
            new_records.append(record)
        return new_records

    def _usage_record_key(self, record: HaiUsageRecord) -> int:
        """Stable dedupe key for a usage record — session_id is authoritative."""
        return record.session_id

    def recent_sessions(self, count: int = 10) -> list[dict[str, object]]:
        """Return the most recent usage records as serializable dicts."""
        records = self.client.state.usage_records[-count:]
        return [
            {
                "session_id": r.session_id,
                "start_time": r.start_time.isoformat(),
                "duration_seconds": r.duration_seconds,
                "volume_liters": round(r.volume_milliliters / 1000, 2),
                "avg_temp_celsius": round(r.average_temp_centicelsius / 100, 1),
            }
            for r in reversed(records)
        ]

    async def async_set_water_alert_threshold(self, value_liters: float) -> None:
        """Write and publish a new water alert threshold."""
        await self.client.async_write_water_threshold(value_liters)
        await self._async_persist_alert_settings()
        self.async_set_updated_data(self.client.state)

    async def async_set_temp_alert_threshold(self, value_celsius: float) -> None:
        """Write and publish a new temperature alert threshold."""
        await self.client.async_write_temp_threshold(value_celsius)
        await self._async_persist_alert_settings()
        self.async_set_updated_data(self.client.state)

    async def async_set_led_color(self, target: str, color_name: str) -> None:
        """Write and publish a new LED color."""
        await self.client.async_write_led_color(target, color_name)
        await self._async_persist_alert_settings()
        self.async_set_updated_data(self.client.state)

    async def async_set_alert_enabled(self, target: str, enabled: bool) -> None:
        """Write and publish a new alert enable state."""
        await self.client.async_write_alert_enable(target, enabled)
        await self._async_persist_alert_settings()
        self.async_set_updated_data(self.client.state)
