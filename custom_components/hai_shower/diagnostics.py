"""Diagnostics support for Hai Shower."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS, CONF_DEVICE_CODE, CONF_DEVICE_ID, DOMAIN
from .coordinator import HaiShowerCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    coordinator: HaiShowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    state = coordinator.client.state

    return {
        "config_entry": {
            "address": entry.data.get(CONF_ADDRESS),
            "device_id": entry.data.get(CONF_DEVICE_ID),
            "device_code": entry.data.get(CONF_DEVICE_CODE),
            # device_key intentionally omitted — secret material
        },
        "state": {
            "available": state.available,
            "lifecycle_state": state.lifecycle_state,
            "lifecycle_detail": state.lifecycle_detail,
            "last_error": state.last_error,
            "product_id": state.product_id,
            "battery_level_mv": state.battery_level_mv,
            "firmware_version": state.firmware_version,
            "current_temp_centicelsius": state.current_temp_centicelsius,
            "current_flow_ml_per_sec": state.current_flow_ml_per_sec,
            "session_duration_seconds": state.session_duration_seconds,
            "session_volume_milliliters": state.session_volume_milliliters,
            "active_session_id": state.active_session_id,
            "usage_record_count": len(state.usage_records),
            "last_history_sync_requested_at": (
                state.last_history_sync_requested_at.isoformat()
                if state.last_history_sync_requested_at
                else None
            ),
            "last_history_sync_started_at": (
                state.last_history_sync_started_at.isoformat()
                if state.last_history_sync_started_at
                else None
            ),
            "last_history_sync_completed_at": (
                state.last_history_sync_completed_at.isoformat()
                if state.last_history_sync_completed_at
                else None
            ),
            "last_history_sync_trigger": state.last_history_sync_trigger,
            "last_history_sync_result": state.last_history_sync_result,
            "last_history_sync_error": state.last_history_sync_error,
            "last_history_sync_records": state.last_history_sync_records,
            "last_shower_end_notified_at": (
                state.last_shower_end_notified_at.isoformat()
                if state.last_shower_end_notified_at
                else None
            ),
            "last_shower_end_payload_hex": state.last_shower_end_payload_hex,
            "last_shower_end_payload_len": state.last_shower_end_payload_len,
            "recent_sessions": coordinator.recent_sessions(5),
        },
    }
