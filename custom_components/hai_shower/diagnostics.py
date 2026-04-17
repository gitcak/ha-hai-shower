"""Diagnostics support for Hai Shower."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_CODE,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    DOMAIN,
)
from .coordinator import HaiShowerCoordinator

# Physical-device identifiers are redacted so users can safely share
# diagnostics in bug reports. device_key is sensitive keying material and
# must never leave the host. Raw BLE payloads (last_shower_end_payload_hex)
# are intentionally kept visible — they are protocol debug data, not PII,
# and are the primary reason anyone captures diagnostics for this
# integration.
REDACT_CONFIG = {CONF_ADDRESS, CONF_DEVICE_ID, CONF_DEVICE_CODE, CONF_DEVICE_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    coordinator: HaiShowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    state = coordinator.client.state

    config_entry = async_redact_data(
        {
            CONF_ADDRESS: entry.data.get(CONF_ADDRESS),
            CONF_DEVICE_ID: entry.data.get(CONF_DEVICE_ID),
            CONF_DEVICE_CODE: entry.data.get(CONF_DEVICE_CODE),
        },
        REDACT_CONFIG,
    )

    state_data = {
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
    }

    return {"config_entry": config_entry, "state": state_data}
