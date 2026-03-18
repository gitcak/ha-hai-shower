"""Persistent storage for Hai usage records."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from collections.abc import Iterable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_VERSION, USAGE_RECORDS_STORAGE_KEY
from .models import HaiUsageRecord

_LOGGER = logging.getLogger(__name__)


class HaiUsageRecordStore:
    """Persist decoded usage records by device address."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store[dict[str, list[dict[str, Any]]]](
            hass, STORAGE_VERSION, USAGE_RECORDS_STORAGE_KEY
        )

    async def async_load(
        self, storage_key: str, *, legacy_keys: Iterable[str] = ()
    ) -> list[HaiUsageRecord]:
        """Load persisted usage records for a device.

        The preferred key is the stable per-device storage key.  Older
        installations stored records directly under the BLE address, so this
        loader can fall back to legacy keys and migrate the data forward.
        """
        data = await self._store.async_load() or {}
        if not isinstance(data, dict):
            _LOGGER.warning("Hai usage storage had invalid root data; ignoring it")
            return []
        source_key = storage_key if storage_key in data else None
        if source_key is None:
            for legacy_key in legacy_keys:
                if legacy_key in data:
                    source_key = legacy_key
                    break
        if source_key is None:
            raw_records = []
        else:
            raw_records = data.get(source_key, [])
        if not isinstance(raw_records, list):
            _LOGGER.warning(
                "Hai usage storage had invalid record list for %s; ignoring it",
                source_key or storage_key,
            )
            return []
        records: list[HaiUsageRecord] = []
        for raw_record in raw_records:
            try:
                records.append(_record_from_dict(raw_record))
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.warning(
                    "Skipping invalid stored usage record for %s: %s",
                    source_key or storage_key,
                    err,
                )
        if source_key is not None and source_key != storage_key:
            data[storage_key] = data.pop(source_key)
            await self._store.async_save(data)
        return records

    async def async_save(self, storage_key: str, records: list[HaiUsageRecord]) -> None:
        """Persist usage records for a device."""
        data = await self._store.async_load() or {}
        if not isinstance(data, dict):
            data = {}
        data[storage_key] = [_record_to_dict(record) for record in records]
        await self._store.async_save(data)


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
