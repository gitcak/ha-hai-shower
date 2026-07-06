"""Persistent storage for Hai usage records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from collections.abc import Iterable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_VERSION, USAGE_RECORDS_STORAGE_KEY
from .models import HaiUsageRecord

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HaiUsageSnapshot:
    """Persisted usage records plus lifetime counters for Energy totals."""

    records: list[HaiUsageRecord]
    lifetime_total_water_ml: int
    lifetime_shower_count: int
    lifetime_last_session_id: int | None
    last_seen_at: datetime | None = None

    @classmethod
    def from_records(cls, records: list[HaiUsageRecord]) -> "HaiUsageSnapshot":
        """Build a conservative snapshot from the available records."""
        if not records:
            return cls([], 0, 0, None)
        return cls(
            records=list(records),
            lifetime_total_water_ml=sum(record.volume_milliliters for record in records),
            lifetime_shower_count=len(records),
            lifetime_last_session_id=max(record.session_id for record in records),
            last_seen_at=None,
        )


class HaiUsageRecordStore:
    """Persist decoded usage records by device address."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, USAGE_RECORDS_STORAGE_KEY
        )

    async def async_load(
        self, storage_key: str, *, legacy_keys: Iterable[str] = ()
    ) -> list[HaiUsageRecord]:
        """Load persisted usage records for a device.

        Compatibility wrapper for callers that only need the capped records.
        """
        snapshot = await self.async_load_snapshot(
            storage_key, legacy_keys=legacy_keys
        )
        return snapshot.records

    async def async_load_snapshot(
        self, storage_key: str, *, legacy_keys: Iterable[str] = ()
    ) -> HaiUsageSnapshot:
        """Load persisted usage snapshot for a device.

        The preferred key is the stable per-device storage key.  Older
        installations stored records directly under the BLE address, so this
        loader can fall back to legacy keys and migrate the data forward.
        """
        data = await self._store.async_load() or {}
        if not isinstance(data, dict):
            _LOGGER.warning("Hai usage storage had invalid root data; ignoring it")
            return HaiUsageSnapshot.from_records([])
        source_key = storage_key if storage_key in data else None
        if source_key is None:
            for legacy_key in legacy_keys:
                if legacy_key in data:
                    source_key = legacy_key
                    break
        if source_key is None:
            return HaiUsageSnapshot.from_records([])
        else:
            raw_snapshot = data.get(source_key, [])
        snapshot, migrated = _snapshot_from_storage_value(
            raw_snapshot, source_key or storage_key
        )
        if source_key != storage_key or migrated:
            data[storage_key] = _snapshot_to_dict(snapshot)
            if source_key != storage_key:
                data.pop(source_key, None)
            await self._store.async_save(data)
        return snapshot

    async def async_save(self, storage_key: str, records: list[HaiUsageRecord]) -> None:
        """Persist usage records for a device."""
        await self.async_save_snapshot(
            storage_key, HaiUsageSnapshot.from_records(records)
        )

    async def async_save_snapshot(
        self, storage_key: str, snapshot: HaiUsageSnapshot
    ) -> None:
        """Persist a usage snapshot for a device."""
        data = await self._store.async_load() or {}
        if not isinstance(data, dict):
            data = {}
        data[storage_key] = _snapshot_to_dict(snapshot)
        await self._store.async_save(data)


def _snapshot_from_storage_value(
    value: object, storage_key: str
) -> tuple[HaiUsageSnapshot, bool]:
    """Deserialize a storage value, returning whether it needs migration."""
    if isinstance(value, list):
        records = _records_from_list(value, storage_key)
        return HaiUsageSnapshot.from_records(records), True
    if not isinstance(value, dict):
        _LOGGER.warning(
            "Hai usage storage had invalid snapshot for %s; ignoring it",
            storage_key,
        )
        return HaiUsageSnapshot.from_records([]), False

    raw_records = value.get("records", [])
    if not isinstance(raw_records, list):
        _LOGGER.warning(
            "Hai usage storage had invalid record list for %s; ignoring it",
            storage_key,
        )
        raw_records = []

    records = _records_from_list(raw_records, storage_key)
    derived = HaiUsageSnapshot.from_records(records)
    lifetime_total_water_ml = max(
        _nonnegative_int(
            value.get("lifetime_total_water_ml"),
            derived.lifetime_total_water_ml,
        ),
        derived.lifetime_total_water_ml,
    )
    lifetime_shower_count = max(
        _nonnegative_int(
            value.get("lifetime_shower_count"),
            derived.lifetime_shower_count,
        ),
        derived.lifetime_shower_count,
    )
    lifetime_last_session_id = _optional_nonnegative_int(
        value.get("lifetime_last_session_id"),
        derived.lifetime_last_session_id,
    )
    if (
        derived.lifetime_last_session_id is not None
        and (
            lifetime_last_session_id is None
            or lifetime_last_session_id < derived.lifetime_last_session_id
        )
    ):
        lifetime_last_session_id = derived.lifetime_last_session_id

    last_seen_at = _optional_utc_datetime(value.get("last_seen_at"))

    snapshot = HaiUsageSnapshot(
        records=records,
        lifetime_total_water_ml=lifetime_total_water_ml,
        lifetime_shower_count=lifetime_shower_count,
        lifetime_last_session_id=lifetime_last_session_id,
        last_seen_at=last_seen_at,
    )
    return snapshot, _snapshot_to_dict(snapshot) != value


def _records_from_list(raw_records: list[object], storage_key: str) -> list[HaiUsageRecord]:
    """Deserialize valid records from a raw list."""
    records: list[HaiUsageRecord] = []
    for raw_record in raw_records:
        try:
            if not isinstance(raw_record, dict):
                raise TypeError("record is not an object")
            records.append(_record_from_dict(raw_record))
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Skipping invalid stored usage record for %s: %s",
                storage_key,
                err,
            )
    return records


def _snapshot_to_dict(snapshot: HaiUsageSnapshot) -> dict[str, Any]:
    """Serialize a usage snapshot for storage."""
    payload: dict[str, Any] = {
        "records": [_record_to_dict(record) for record in snapshot.records],
        "lifetime_total_water_ml": snapshot.lifetime_total_water_ml,
        "lifetime_shower_count": snapshot.lifetime_shower_count,
        "lifetime_last_session_id": snapshot.lifetime_last_session_id,
    }
    if snapshot.last_seen_at is not None:
        payload["last_seen_at"] = snapshot.last_seen_at.isoformat()
    return payload


def _optional_utc_datetime(value: object) -> datetime | None:
    """Parse an optional ISO 8601 datetime string from storage."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        return None
    try:
        return _parse_utc_datetime(value)
    except ValueError:
        return None


def _nonnegative_int(value: object, default: int) -> int:
    """Parse a non-negative integer, falling back on invalid values."""
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _optional_nonnegative_int(value: object, default: int | None) -> int | None:
    """Parse an optional non-negative integer."""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _record_to_dict(record: HaiUsageRecord) -> dict[str, Any]:
    """Serialize a usage record for storage."""
    return {
        "session_id": record.session_id,
        "average_temp_centicelsius": record.average_temp_centicelsius,
        "duration_seconds": record.duration_seconds,
        "volume_milliliters": record.volume_milliliters,
        "start_time": record.start_time.isoformat(),
        "initial_temp_centicelsius": record.initial_temp_centicelsius,
    }


def _parse_utc_datetime(value: str) -> datetime:
    """Parse an ISO 8601 datetime string, ensuring the result is UTC-aware.

    Records are always stored as UTC (produced by ``datetime.isoformat()`` on
    a UTC-aware datetime).  Older store entries or manually edited files may
    lack the ``+00:00`` suffix, producing a naive datetime from
    ``fromisoformat``.  Treat any naive result as UTC rather than letting
    callers accidentally interpret it as local time.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _record_from_dict(data: dict[str, Any]) -> HaiUsageRecord:
    """Deserialize a usage record from storage."""
    return HaiUsageRecord(
        session_id=int(data["session_id"]),
        average_temp_centicelsius=int(data["average_temp_centicelsius"]),
        duration_seconds=int(data["duration_seconds"]),
        volume_milliliters=int(data["volume_milliliters"]),
        start_time=_parse_utc_datetime(str(data["start_time"])),
        initial_temp_centicelsius=int(data["initial_temp_centicelsius"]),
    )
