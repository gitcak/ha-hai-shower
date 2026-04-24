"""Data models for Hai Shower BLE state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class HaiLifecycleState(StrEnum):
    """High-level runtime lifecycle for a shower device."""

    SETUP = "setup"
    MONITORING = "monitoring"
    SYNCING = "syncing"
    ERROR = "error"


class HaiLifecycleDetail(StrEnum):
    """Standardized lifecycle detail reasons.

    Each value describes *why* the device entered its current lifecycle state.
    Grouped by the operation that produces them.
    """

    # --- Initialization / teardown ---
    INITIALIZING_BLE_VISIBILITY = "initializing_ble_visibility"
    DISCONNECTED = "disconnected"
    DISCONNECT_CALLBACK = "disconnect_callback"
    SHUTDOWN_REQUESTED = "shutdown_requested"
    SHUTDOWN_COMPLETE = "shutdown_complete"

    # --- Refresh cycle ---
    REFRESH_CONNECTING = "refresh_connecting"
    REFRESH_READING = "refresh_reading"
    REFRESH_COMPLETE = "refresh_complete"
    REFRESH_BLE_ERROR = "refresh_ble_error"
    REFRESH_UNEXPECTED_ERROR = "refresh_unexpected_error"

    # --- Temperature subscription ---
    SUBSCRIBE_TEMPERATURE_CONNECTING = "subscribe_temperature_connecting"
    TEMPERATURE_SUBSCRIPTION_ACTIVE = "temperature_subscription_active"
    TEMPERATURE_NOTIFY = "temperature_notify"
    SUBSCRIBE_TEMPERATURE_BLE_ERROR = "subscribe_temperature_ble_error"

    # --- Shower-end subscription ---
    SUBSCRIBE_SHOWER_END_CONNECTING = "subscribe_shower_end_connecting"
    SHOWER_END_SUBSCRIPTION_ACTIVE = "shower_end_subscription_active"
    SHOWER_END_TRIGGER = "shower_end_trigger"
    SUBSCRIBE_SHOWER_END_BLE_ERROR = "subscribe_shower_end_ble_error"

    # --- History sync ---
    HISTORY_SYNC_CONNECTING = "history_sync_connecting"
    HISTORY_SYNC_START = "history_sync_start"
    HISTORY_SYNC_COMPLETE = "history_sync_complete"
    HISTORY_SYNC_CONNECT_BLE_ERROR = "history_sync_connect_ble_error"
    HISTORY_SYNC_BLE_ERROR = "history_sync_ble_error"
    HISTORY_SYNC_TIMEOUT = "history_sync_timeout"
    HISTORY_SYNC_UNEXPECTED_ERROR = "history_sync_unexpected_error"


# Valid lifecycle state transitions.  Any transition not in this map is logged
# as a warning (but still applied) so that bugs surface during development
# without crashing the integration at runtime.
VALID_TRANSITIONS: dict[HaiLifecycleState, frozenset[HaiLifecycleState]] = {
    HaiLifecycleState.SETUP: frozenset({
        HaiLifecycleState.SETUP,
        HaiLifecycleState.MONITORING,
        HaiLifecycleState.SYNCING,
        HaiLifecycleState.ERROR,
    }),
    HaiLifecycleState.MONITORING: frozenset({
        HaiLifecycleState.SETUP,
        HaiLifecycleState.MONITORING,
        HaiLifecycleState.SYNCING,
        HaiLifecycleState.ERROR,
    }),
    HaiLifecycleState.SYNCING: frozenset({
        HaiLifecycleState.MONITORING,
        HaiLifecycleState.ERROR,
    }),
    HaiLifecycleState.ERROR: frozenset({
        HaiLifecycleState.SETUP,
        HaiLifecycleState.MONITORING,
        HaiLifecycleState.ERROR,
    }),
}


@dataclass(slots=True)
class HaiUsageRecord:
    """Decoded shower usage record."""

    session_id: int
    average_temp_centicelsius: int
    duration_seconds: int
    volume_milliliters: int
    start_time: datetime
    initial_temp_centicelsius: int


@dataclass(slots=True)
class HaiShowerState:
    """Current coordinator state."""

    available: bool = False
    device_name: str | None = None
    lifecycle_state: HaiLifecycleState = HaiLifecycleState.SETUP
    last_error: str | None = None
    lifecycle_detail: HaiLifecycleDetail | None = None
    current_temp_centicelsius: int | None = None
    current_flow_ml_per_sec: int | None = None
    session_duration_seconds: int | None = None
    session_volume_milliliters: int | None = None
    battery_level_mv: int | None = None
    firmware_version: str | None = None
    product_id: str | None = None
    active_session_id: int | None = None
    last_seen_at: datetime | None = None
    shower_count: int | None = None
    total_water_usage_ml: int = 0
    last_session_duration_seconds: int | None = None
    last_session_volume_ml: int | None = None
    last_session_avg_temp_cc: int | None = None
    last_usage_record: HaiUsageRecord | None = None
    usage_records: list[HaiUsageRecord] = field(default_factory=list)
    # Phase 2: device settings (read from BLE when protocol is validated)
    water_alert_threshold_liters: float | None = None
    temp_alert_threshold_celsius: float | None = None
    water_alert_enabled: bool | None = None
    temp_alert_enabled: bool | None = None
    water_led_color: str | None = None
    temp_led_color: str | None = None
    hardware_model: str | None = None
    last_history_sync_requested_at: datetime | None = None
    last_history_sync_started_at: datetime | None = None
    last_history_sync_completed_at: datetime | None = None
    last_history_sync_trigger: str | None = None
    last_history_sync_result: str | None = None
    last_history_sync_error: str | None = None
    last_history_sync_records: int | None = None
    last_shower_end_notified_at: datetime | None = None
    last_shower_end_payload_hex: str | None = None
    last_shower_end_payload_len: int | None = None
