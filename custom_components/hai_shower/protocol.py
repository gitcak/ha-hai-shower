"""Recovered protocol helpers for Hai Shower BLE payloads."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

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

# Characteristics where byte order is reversed after decrypt
# (GattSchema.isReverse): the LED colors, so they read back as [R, G, B].
# encode_led_color packs [R, G, B] directly, which is the same wire order.
_REVERSE_CHARS: set[str] = {
    UUIDS["water_led_color_old"].characteristic,
    UUIDS["water_led_color"].characteristic,
    UUIDS["temp_led_color"].characteristic,
}


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
# On firmware >= 550 (devices exposing led_config, E622150D) the Hai app's
# DeviceBloc.configureDevice writes all alert settings as one 30-byte
# composite buffer and never writes water_threshold (E6221503) or the LED
# color characteristics (E6221508/09). Older firmware takes those three
# individually, with cloud-encrypted values.
#
# Buffer layout (before XOR encryption):
#   [0:8]   zero
#   [8:12]  UInt32LE — waterThresholdMl (milliliters)
#   [12:14] UInt16LE — tempThresholdCC (centi-Celsius, whatever the UI unit)
#   [14]    UInt8    — flags: 0x04 = water alert light on,
#                       0x08 = temperature alert light on
#   [15:24] zero
#   [24:27] water color R, G, B
#   [27:30] temp color R, G, B
# A disabled alert light is sent as color 0 with its flag clear. The whole
# buffer is XORed in one pass with GattSchema.encryptDecrypt.
LED_CONFIG_WATER_ALERT_FLAG = 0x04
LED_CONFIG_TEMP_ALERT_FLAG = 0x08

# Named color → 24-bit RGB value the Hai app sends to the device
# (ledColorLists ``color``; ``appColorCode`` is only the on-screen swatch).
COLOR_RGB: dict[str, int] = {
    "Ruby": 0xFF0000,
    "Orange": 0xFF2000,
    "Sun": 0xFFFB13,
    "Grass": 0x9FFF29,
    "Leaf": 0x00FF00,
    "Sky": 0x61E2FF,
    "Plum": 0x8400A2,
    "Pink": 0xFF86CC,
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
    thresholds, enable flags, and RGB colors. Unused bytes are zero-filled
    before the whole payload is XOR-encrypted with the device key. As in the
    Hai app, a disabled alert light is sent as color 0 with its flag clear.
    """
    if not water_alert_enabled:
        water_color_rgb = 0
    if not temp_alert_enabled:
        temp_color_rgb = 0
    enable_mask = 0
    if water_color_rgb:
        enable_mask |= LED_CONFIG_WATER_ALERT_FLAG
    if temp_color_rgb:
        enable_mask |= LED_CONFIG_TEMP_ALERT_FLAG

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


def parse_advertised_firmware(
    manufacturer_data: Mapping[int, bytes],
) -> tuple[int, int | None] | None:
    """Return (firmware, bootloader) versions from a Hai advertisement.

    The Hai app's _parseDeviceInfo reads the manufacturer-specific payload:
    six serial bytes, a UInt16LE firmware version (e.g. 611 for "6.11") and
    an optional bootloader byte. Home Assistant strips the 2-byte company ID,
    so the offsets here are the app's minus two. Payloads that are too short
    or carry an implausible version are ignored.
    """
    for payload in manufacturer_data.values():
        if len(payload) < 8:
            continue
        firmware = int.from_bytes(payload[6:8], "little")
        if not 100 <= firmware <= 9999:
            continue
        bootloader = payload[8] if len(payload) > 8 else None
        return firmware, bootloader
    return None


def format_firmware_version(version: int) -> str:
    """Format a Hai firmware number the way the app shows it (611 -> 6.11)."""
    return f"{version // 100}.{version % 100}"


def _xor_decrypt_at_offset(
    payload: bytes, key: list[int], offset: int
) -> bytes:
    """XOR-decrypt *payload* using key bytes starting at *offset*."""

    if not key:
        return bytes(payload)
    decrypted = bytearray(payload)
    for index in range(len(decrypted)):
        decrypted[index] ^= key[(offset + index) % len(key)]
    return bytes(decrypted)


def usage_record_start_time_likely_misdecoded(
    start_time: datetime,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether a stored start time matches the plaintext misread symptom.

    When bytes ``[12:16]`` are XOR-encrypted on the wire but interpreted as
    plaintext, the decoded epoch lands roughly two years in the future.
    """

    reference = now or datetime.now(UTC)
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=UTC)
    else:
        start_time = start_time.astimezone(UTC)
    if start_time.year >= reference.year + 2:
        return True
    return start_time > reference + timedelta(days=7)


def repair_stored_usage_record(
    record: HaiUsageRecord, key: list[int]
) -> HaiUsageRecord | None:
    """Repair a persisted record whose trailing fields were plaintext-read.

    Storage only keeps decoded scalars, not the original wire bytes.  The wrong
    ``start_time`` integer is therefore the little-endian interpretation of the
    encrypted timestamp bytes, and XOR-decrypting those four bytes recovers the
    true UTC epoch.  The same applies to ``initial_temp_centicelsius``.
    """

    if not key or not usage_record_start_time_likely_misdecoded(record.start_time):
        return None

    wrong_epoch = int(record.start_time.timestamp())
    repaired_epoch = int.from_bytes(
        _xor_decrypt_at_offset(wrong_epoch.to_bytes(4, "little"), key, 12),
        "little",
    )
    repaired_initial_temp = int.from_bytes(
        _xor_decrypt_at_offset(
            record.initial_temp_centicelsius.to_bytes(2, "little"), key, 16
        ),
        "little",
    )
    try:
        repaired_start = datetime.fromtimestamp(repaired_epoch, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    if usage_record_start_time_likely_misdecoded(repaired_start):
        return None

    return HaiUsageRecord(
        session_id=record.session_id,
        average_temp_centicelsius=record.average_temp_centicelsius,
        duration_seconds=record.duration_seconds,
        volume_milliliters=record.volume_milliliters,
        start_time=repaired_start,
        initial_temp_centicelsius=repaired_initial_temp,
    )


def repair_stored_usage_records(
    records: list[HaiUsageRecord], key: list[int]
) -> tuple[list[HaiUsageRecord], int]:
    """Return records with misdecoded timestamps repaired in-place order."""

    repaired_records: list[HaiUsageRecord] = []
    repaired_count = 0
    for record in records:
        repaired = repair_stored_usage_record(record, key)
        if repaired is not None:
            repaired_records.append(repaired)
            repaired_count += 1
        else:
            repaired_records.append(record)
    return repaired_records, repaired_count


def parse_usage_record(
    payload: bytes, key: list[int] | None = None
) -> HaiUsageRecord | None:
    """Parse a usage record notification from E6221603.

    Live runtime validation proved that usage records are XOR-encrypted with
    the device key across the full 18-byte payload.  When *key* is provided,
    all 18 bytes are decrypted before field extraction.  The all-zero
    terminator check runs **before** decryption so the end-of-sync marker is
    still detected.
    """

    if not payload:
        return None
    if len(payload) < 18:
        raise ValueError(f"Usage record payload too short: {len(payload)}")
    if payload[:18] == b"\x00" * 18:
        return None

    data = bytearray(payload[:18])
    if key:
        data = bytearray(_encrypt_decrypt(data, key))

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


def usage_record_start_time_debug(
    payload: bytes, key: list[int] | None = None
) -> str:
    """Return a debug string comparing both readings of the start_time field.

    Logs both plaintext and XOR-decrypted interpretations of the raw timestamp
    bytes.  Useful when validating parser changes against a real-device capture.
    """

    if not payload or len(payload) < 18:
        return "start_time_debug=<payload too short>"

    raw_ts = bytes(payload[12:16])
    parts = [f"raw_ts={raw_ts.hex()}"]

    def _fmt(epoch: int) -> str:
        try:
            return datetime.fromtimestamp(epoch, UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return "<invalid>"

    plain_epoch = int.from_bytes(raw_ts, "little")
    parts.append(f"plaintext_epoch={plain_epoch}")
    parts.append(f"plaintext_dt={_fmt(plain_epoch)}")

    if key:
        dec_epoch = int.from_bytes(_xor_decrypt_at_offset(raw_ts, key, 12), "little")
        dec = dec_epoch.to_bytes(4, "little")
        parts.append(f"decrypted_ts={bytes(dec).hex()}")
        parts.append(f"decrypted_epoch={dec_epoch}")
        parts.append(f"decrypted_dt={_fmt(dec_epoch)}")

    return " ".join(parts)


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
