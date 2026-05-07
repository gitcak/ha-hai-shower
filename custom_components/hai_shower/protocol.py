"""Recovered protocol helpers for Hai Shower BLE payloads."""

from __future__ import annotations

from datetime import UTC, datetime

from .const import UUIDS
from .models import HaiUsageRecord

# Characteristic byte-width classes from GattSchema.dataLengthForChar.
# Class 2: 2-byte payload (waterTemp, batteryLevel, sessionDuration)
# Class 3: 3-byte payload (LED color characteristics)
# Class 4: 4-byte payload (version, waterFlow, recordCount, sessionId, etc.)
_DATA_LENGTH: dict[str, int] = {
    UUIDS["water_temp"].characteristic: 2,
    UUIDS["water_temp_old"].characteristic: 2,
    # battery_level (E622140C) is handled as a plaintext UInt16LE in the
    # current implementation. Omitted here intentionally;
    # decrypt_characteristic must not be called on it.
    UUIDS["session_duration"].characteristic: 2,
    UUIDS["water_led_color_old"].characteristic: 3,
    UUIDS["water_led_color"].characteristic: 3,
    UUIDS["temp_led_color"].characteristic: 3,
    UUIDS["version"].characteristic: 4,
    UUIDS["water_flow"].characteristic: 4,
    UUIDS["record_count"].characteristic: 4,
    UUIDS["rtc_sync"].characteristic: 4,
    UUIDS["session_id"].characteristic: 4,
    UUIDS["session_time"].characteristic: 4,
    UUIDS["session_volume"].characteristic: 4,
    UUIDS["water_threshold"].characteristic: 4,  # UInt32LE, milliliters
}

# Characteristics where byte order is reversed after decrypt.
_REVERSE_CHARS: set[str] = set()  # None observed; placeholder if needed.


def _encrypt_decrypt(data: bytearray, key: list[int]) -> bytearray:
    """XOR cipher matching GattSchema.encryptDecrypt."""
    if not key:
        return data
    result = bytearray(len(data))
    for i, byte in enumerate(data):
        result[i] = byte ^ key[i % len(key)]
    return result


def decrypt_characteristic(
    char_uuid: str, raw: bytes, key: list[int]
) -> int | None:
    """Decrypt a BLE characteristic value using the device key.

    Mirrors GattSchema.decryptData: get byte width from dataLengthForChar,
    XOR with key via encryptDecrypt, optionally reverse, then reconstruct
    a little-endian integer.

    Returns ``None`` when the raw payload is all zeros — the device sends
    zero-filled characteristics while idle, and XOR-decrypting those just
    produces the key bytes, yielding bogus sensor values.
    """
    width = _DATA_LENGTH.get(char_uuid.upper(), 0)
    if width == 0 or len(raw) < width:
        return None

    sliced = raw[:width]
    if all(b == 0 for b in sliced):
        return None

    buf = bytearray(sliced)
    buf = _encrypt_decrypt(buf, key)

    if char_uuid.upper() in _REVERSE_CHARS:
        buf.reverse()

    return int.from_bytes(buf, "little")


def decrypt_characteristic_debug(
    char_uuid: str, raw: bytes, key: list[int]
) -> dict[str, object]:
    """Return debug details for an encrypted characteristic read.

    This mirrors :func:`decrypt_characteristic` but preserves intermediate
    values so runtime validation can distinguish:
    - zero-like proxy payloads (idle device returning all zeros)
    - width mismatches
    - XOR output that simply mirrors the device key
    """
    upper_uuid = char_uuid.upper()
    width = _DATA_LENGTH.get(upper_uuid, 0)
    sliced = bytes(raw[:width]) if width and len(raw) >= width else b""

    if width == 0:
        return {
            "width": 0,
            "raw_len": len(raw),
            "sliced_raw_hex": "",
            "decrypted_hex": "",
            "reversed": False,
            "idle_zeros": False,
            "value": None,
        }

    if len(raw) < width:
        return {
            "width": width,
            "raw_len": len(raw),
            "sliced_raw_hex": sliced.hex(),
            "decrypted_hex": "",
            "reversed": upper_uuid in _REVERSE_CHARS,
            "idle_zeros": False,
            "value": None,
        }

    idle_zeros = all(b == 0 for b in sliced)

    buf = bytearray(sliced)
    buf = _encrypt_decrypt(buf, key)
    reversed_bytes = upper_uuid in _REVERSE_CHARS
    if reversed_bytes:
        buf.reverse()

    return {
        "width": width,
        "raw_len": len(raw),
        "sliced_raw_hex": sliced.hex(),
        "decrypted_hex": bytes(buf).hex(),
        "reversed": reversed_bytes,
        "idle_zeros": idle_zeros,
        "value": None if idle_zeros else int.from_bytes(buf, "little"),
    }


# ---------------------------------------------------------------
# Phase 2: BLE write encoding helpers (confirmed from RE artifacts)
# ---------------------------------------------------------------
# The Hai app writes device settings to led_config (E622150D) as a
# composite encrypted buffer.  Individual characteristics like
# water_threshold (E6221503), water_led_color (E6221508), and
# temp_led_color (E6221509) are read-back mirrors.
#
# Buffer layout (before XOR encryption):
#   [0:4]  UInt32LE  — zero padding / device-id echo
#   [4:8]  UInt32LE  — zero padding
#   [8:12] UInt32LE  — waterThresholdMl (milliliters)
#   [12:14] UInt16LE — tempThresholdCC (centi-Celsius)
#   [14]   UInt8     — enable bitmask: bit 0 = water color present,
#                       bit 1 = temp color present
#   [15:19] UInt32LE — zero padding
#   [19:23] UInt32LE — zero padding
#   [23]   UInt8     — zero padding
#   [24]   UInt8     — waterColorRGB >> 16 & 0xFF (R)
#   [25]   UInt8     — waterColorRGB >> 8 & 0xFF (G)
#   [26]   UInt8     — waterColorRGB & 0xFF (B)
#   [27]   UInt8     — tempColorRGB >> 16 & 0xFF (R)
#   [28]   UInt8     — tempColorRGB >> 8 & 0xFF (G)
#   [29]   UInt8     — tempColorRGB & 0xFF (B)
# The buffer is then encrypted per-characteristic-slice using
# GattSchema.encryptDecrypt before BLE write.

# Named color → 24-bit RGB mapping (from Hai app UI).
COLOR_RGB: dict[str, int] = {
    "Ruby": 0xFF0000,
    "Orange": 0xFF8000,
    "Sun": 0xFFFF00,
    "Grass": 0x00FF00,
    "Leaf": 0x008000,
    "Sky": 0x0080FF,
    "Plum": 0x800080,
    "Pink": 0xFF80FF,
    "White": 0xFFFFFF,
}

RGB_COLOR: dict[int, str] = {rgb: name for name, rgb in COLOR_RGB.items()}


def encode_water_threshold(value_ml: int, key: list[int]) -> bytes:
    """Encode a water-use alert threshold for BLE write to E6221503.

    The value is UInt32LE in milliliters, then XOR-encrypted with the device key.
    """
    buf = bytearray(value_ml.to_bytes(4, "little"))
    return bytes(_encrypt_decrypt(buf, key))


def encode_rtc_sync(epoch: int, key: list[int]) -> bytes:
    """Encode the RTC sync epoch for BLE write to E6221504."""
    buf = bytearray(epoch.to_bytes(4, "little", signed=False))
    return bytes(_encrypt_decrypt(buf, key))


def encode_temp_threshold(value_cc: int, key: list[int]) -> bytes:
    """Encode a temperature alert threshold for BLE write.

    The value is UInt16LE in centi-Celsius, then XOR-encrypted.
    Note: the temp threshold is written as part of the led_config composite
    buffer, not to a standalone characteristic.
    """
    buf = bytearray(value_cc.to_bytes(2, "little"))
    return bytes(_encrypt_decrypt(buf, key))


def encode_led_color(rgb: int, key: list[int]) -> bytes:
    """Encode a 3-byte RGB LED color for BLE write to E6221508/E6221509.

    The color is packed as [R, G, B] then XOR-encrypted.
    """
    buf = bytearray([
        (rgb >> 16) & 0xFF,
        (rgb >> 8) & 0xFF,
        rgb & 0xFF,
    ])
    return bytes(_encrypt_decrypt(buf, key))


def encode_led_config(
    *,
    water_threshold_ml: int,
    temp_threshold_cc: int,
    water_alert_enabled: bool,
    temp_alert_enabled: bool,
    water_color_rgb: int,
    temp_color_rgb: int,
    key: list[int],
) -> bytes:
    """Encode the composite encrypted led_config payload for E622150D.

    The recovered buffer layout is 30 bytes long and carries both alert
    thresholds, enable bits, and RGB colors. Unused bytes are zero-filled
    before the whole payload is XOR-encrypted with the device key.
    """
    enable_mask = 0
    if water_alert_enabled:
        enable_mask |= 0x01
    if temp_alert_enabled:
        enable_mask |= 0x02

    buf = bytearray(30)
    buf[8:12] = water_threshold_ml.to_bytes(4, "little", signed=False)
    buf[12:14] = temp_threshold_cc.to_bytes(2, "little", signed=False)
    buf[14] = enable_mask
    buf[24:27] = bytes(
        [
            (water_color_rgb >> 16) & 0xFF,
            (water_color_rgb >> 8) & 0xFF,
            water_color_rgb & 0xFF,
        ]
    )
    buf[27:30] = bytes(
        [
            (temp_color_rgb >> 16) & 0xFF,
            (temp_color_rgb >> 8) & 0xFF,
            temp_color_rgb & 0xFF,
        ]
    )
    return bytes(_encrypt_decrypt(buf, key))


def parse_usage_record(
    payload: bytes, key: list[int] | None = None
) -> HaiUsageRecord | None:
    """Parse a usage record notification from E6221603.

    Live runtime validation proved that usage records are XOR-encrypted with
    the device key, contrary to the initial reverse-engineering finding that
    classified them as plaintext.  When *key* is provided, the first 18 bytes
    are decrypted before field extraction.  The all-zero terminator check runs
    **before** decryption so the end-of-sync marker is still detected.
    """

    if not payload:
        return None
    if len(payload) < 18:
        raise ValueError(f"Usage record payload too short: {len(payload)}")
    if payload[:18] == b"\x00" * 18:
        return None

    data = bytearray(payload[:18])
    if key:
        # Runtime validation shows usage records are mixed-format: the first
        # 12 bytes (session id, average temp, duration, volume) are XOR-
        # encrypted, but the trailing timestamp + initial temperature bytes
        # are already plaintext.
        encrypted = bytearray(data)
        encrypted[0:12] = _encrypt_decrypt(encrypted[0:12], key)
        data = encrypted

    session_id = int.from_bytes(data[0:4], "little")
    average_temp_centicelsius = int.from_bytes(data[4:6], "little")
    duration_seconds = int.from_bytes(data[6:8], "little")
    volume_milliliters = int.from_bytes(data[8:12], "little")
    start_timestamp = int.from_bytes(data[12:16], "little")
    initial_temp_centicelsius = int.from_bytes(data[16:18], "little")

    return HaiUsageRecord(
        session_id=session_id,
        average_temp_centicelsius=average_temp_centicelsius,
        duration_seconds=duration_seconds,
        volume_milliliters=volume_milliliters,
        start_time=datetime.fromtimestamp(start_timestamp, UTC),
        initial_temp_centicelsius=initial_temp_centicelsius,
    )


def centicelsius_to_celsius(value: int | None) -> float | None:
    """Convert centi-Celsius to Celsius."""

    if value is None:
        return None
    return value / 100


def milliliters_to_liters(value: int | None) -> float | None:
    """Convert milliliters to liters."""

    if value is None:
        return None
    return value / 1000
