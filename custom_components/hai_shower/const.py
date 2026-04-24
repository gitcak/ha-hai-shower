"""Constants for the Hai Shower integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

DOMAIN = "hai_shower"

CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_KEY = "device_key"
CONF_DEVICE_CODE = "device_code"

DEFAULT_NAME = "Hai Smart Shower"
HAI_LOCAL_NAME = "haiSmartShower"

PLATFORMS: list[str] = ["sensor", "button", "number", "select", "switch"]
STORAGE_VERSION = 1
USAGE_RECORDS_STORAGE_KEY = f"{DOMAIN}_usage_records"
MAX_STORED_USAGE_RECORDS = 250
ENTITY_KEYS: tuple[str, ...] = (
    "history_sync",
    "water_alert_threshold",
    "temp_alert_threshold",
    "water_led_color",
    "temp_led_color",
    "water_alert_enabled",
    "temp_alert_enabled",
    "status",
    "last_seen",
    "current_temperature",
    "current_flow",
    "total_water_usage",
    "shower_count",
    "last_session_duration",
    "last_session_volume",
    "last_session_avg_temp",
    "session_duration",
    "session_volume",
    "battery",
    "firmware_version",
)

ALERT_COLORS: list[str] = [
    "Ruby", "Orange", "Sun", "Grass", "Leaf", "Sky", "Plum", "Pink", "White",
]
OPTION_WATER_ALERT_THRESHOLD_LITERS = "water_alert_threshold_liters"
OPTION_TEMP_ALERT_THRESHOLD_CELSIUS = "temp_alert_threshold_celsius"
OPTION_WATER_ALERT_ENABLED = "water_alert_enabled"
OPTION_TEMP_ALERT_ENABLED = "temp_alert_enabled"
OPTION_WATER_LED_COLOR = "water_led_color"
OPTION_TEMP_LED_COLOR = "temp_led_color"
ALERT_OPTION_KEYS: tuple[str, ...] = (
    OPTION_WATER_ALERT_THRESHOLD_LITERS,
    OPTION_TEMP_ALERT_THRESHOLD_CELSIUS,
    OPTION_WATER_ALERT_ENABLED,
    OPTION_TEMP_ALERT_ENABLED,
    OPTION_WATER_LED_COLOR,
    OPTION_TEMP_LED_COLOR,
)


@dataclass(frozen=True, slots=True)
class GattCharacteristic:
    """Representation of a recovered GATT characteristic."""

    service: str
    characteristic: str


BASE_UUID_SUFFIX = "-E12F-40F2-B0F5-AAA011C0AA8D"

SHOWER_DATA_SERVICE = f"E6221400{BASE_UUID_SUFFIX}"
SHOWER_HISTORY_SERVICE = f"E6221600{BASE_UUID_SUFFIX}"
DEVICE_CONFIG_SERVICE = f"E6221500{BASE_UUID_SUFFIX}"
HAI_SERVICE_UUIDS: set[str] = {
    SHOWER_DATA_SERVICE,
    SHOWER_HISTORY_SERVICE,
    DEVICE_CONFIG_SERVICE,
}

UUIDS: dict[str, GattCharacteristic] = {
    "session_id": GattCharacteristic(SHOWER_DATA_SERVICE, f"E6221401{BASE_UUID_SUFFIX}"),
    "water_temp": GattCharacteristic(SHOWER_DATA_SERVICE, f"E6221402{BASE_UUID_SUFFIX}"),
    "water_temp_old": GattCharacteristic(SHOWER_DATA_SERVICE, f"E6221403{BASE_UUID_SUFFIX}"),
    "session_volume": GattCharacteristic(SHOWER_DATA_SERVICE, f"E6221404{BASE_UUID_SUFFIX}"),
    "water_flow": GattCharacteristic(SHOWER_DATA_SERVICE, f"E6221405{BASE_UUID_SUFFIX}"),
    "session_duration": GattCharacteristic(SHOWER_DATA_SERVICE, f"E6221406{BASE_UUID_SUFFIX}"),
    "session_time": GattCharacteristic(SHOWER_DATA_SERVICE, f"E6221407{BASE_UUID_SUFFIX}"),
    "shower_end": GattCharacteristic(SHOWER_DATA_SERVICE, f"E622140A{BASE_UUID_SUFFIX}"),
    "product_id": GattCharacteristic(SHOWER_DATA_SERVICE, f"E622140B{BASE_UUID_SUFFIX}"),
    "battery_level": GattCharacteristic(SHOWER_DATA_SERVICE, f"E622140C{BASE_UUID_SUFFIX}"),
    "trigger_download": GattCharacteristic(SHOWER_HISTORY_SERVICE, f"E6221601{BASE_UUID_SUFFIX}"),
    "record_count": GattCharacteristic(SHOWER_HISTORY_SERVICE, f"E6221602{BASE_UUID_SUFFIX}"),
    "usage_record": GattCharacteristic(SHOWER_HISTORY_SERVICE, f"E6221603{BASE_UUID_SUFFIX}"),
    "water_threshold": GattCharacteristic(DEVICE_CONFIG_SERVICE, f"E6221503{BASE_UUID_SUFFIX}"),
    "rtc_sync": GattCharacteristic(DEVICE_CONFIG_SERVICE, f"E6221504{BASE_UUID_SUFFIX}"),
    "water_led_color_old": GattCharacteristic(DEVICE_CONFIG_SERVICE, f"E6221507{BASE_UUID_SUFFIX}"),
    "water_led_color": GattCharacteristic(DEVICE_CONFIG_SERVICE, f"E6221508{BASE_UUID_SUFFIX}"),
    "temp_led_color": GattCharacteristic(DEVICE_CONFIG_SERVICE, f"E6221509{BASE_UUID_SUFFIX}"),
    "version": GattCharacteristic(DEVICE_CONFIG_SERVICE, f"E622150B{BASE_UUID_SUFFIX}"),
    "led_config": GattCharacteristic(DEVICE_CONFIG_SERVICE, f"E622150D{BASE_UUID_SUFFIX}"),
    "reset_device": GattCharacteristic(DEVICE_CONFIG_SERVICE, f"E622150E{BASE_UUID_SUFFIX}"),
}


def short_id(value: str | None, width: int = 12) -> str:
    """Return a shortened identifier for safe logs."""
    if not value:
        return "<none>"
    return value[:width]


def key_summary(key: Iterable[int] | str | None) -> str:
    """Return a non-secret summary of device key material."""
    if key is None:
        return "missing"
    if isinstance(key, str):
        return f"str:{len(key)}"
    try:
        return f"len:{len(list(key))}"
    except TypeError:
        return "unknown"


def stable_device_identity(device_id: str | None, address: str) -> str:
    """Return a stable per-device identifier.

    The cloud ``device_id`` remains stable across BLE address changes, so it is
    preferred for entity unique IDs, recorder statistic IDs, and persisted
    storage keys.  Older entries can fall back to the address until they are
    reconfigured.
    """
    return str(device_id or address)


def usage_storage_key(device_id: str | None, address: str) -> str:
    """Return the storage key for persisted usage records."""
    return f"device_id:{stable_device_identity(device_id, address)}"


def payload_preview(payload: bytes | bytearray | None, width: int = 8) -> str:
    """Return a short hex preview for protocol debugging."""
    if not payload:
        return "<empty>"
    preview = bytes(payload[:width]).hex()
    if len(payload) > width:
        return f"{preview}..."
    return preview
