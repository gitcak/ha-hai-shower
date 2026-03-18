"""Historical statistics backfill for Hai Shower usage records.

Imports device usage records into Home Assistant's long-term statistics
database via ``async_add_external_statistics``.  This gives users immediate
historical charts in the Energy > Water dashboard and Statistics panel,
even for showers taken before the integration was installed.

Imports are **incremental**: only closed hourly buckets newer than the last
imported statistic are processed, preventing double-counting on restarts and
syncs.

Statistic IDs are **per-device** and should be based on a stable device
identifier so BLE address corrections do not fork long-term statistics.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re

from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    get_instance,
)
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMetaData,
)
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import VolumeConverter

from .const import DOMAIN
from .models import HaiUsageRecord

_LOGGER = logging.getLogger(__name__)
_VOLUME_UNIT_CLASS = getattr(VolumeConverter, "UNIT_CLASS", "volume")
try:
    from homeassistant.components.recorder.models import StatisticMeanType

    _NO_MEAN_TYPE = StatisticMeanType.NONE
except ImportError:
    _NO_MEAN_TYPE = 0


def _stat_id(device_identity: str, suffix: str) -> str:
    """Build a per-device external statistic ID.

    Format: ``hai_shower:d4ead0566c45_total_water_usage``
    The device identity is lowercased and stripped to alphanumerics for HA
    compatibility.
    """
    clean = re.sub(r"[^a-zA-Z0-9]", "", device_identity).lower()
    return f"{DOMAIN}:{clean}_{suffix}"


def _as_utc_timestamp(value: object) -> float:
    """Normalize recorder start values to a UTC timestamp."""
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        return _as_utc_datetime(value).timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    raise TypeError(f"Unsupported statistics start value: {value!r}")


def _as_utc_datetime(value: datetime) -> datetime:
    """Normalize a record timestamp to a UTC-aware datetime.

    Usage records are expected to be UTC-aware, but older storage entries or
    direct callers can still supply naive datetimes. Treat naive values as UTC
    rather than letting Python reinterpret them in the host local timezone.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def async_import_usage_records(
    hass: HomeAssistant,
    address: str,
    records: list[HaiUsageRecord],
    *,
    statistic_identity: str | None = None,
) -> None:
    """Import usage records as external statistics for the Water dashboard.

    Only records whose ``start_time`` is after the last already-imported
    statistic are processed, making this safe to call on every sync and
    restart without double-counting.

    Home Assistant's external statistics API requires top-of-hour timestamps.
    We therefore import closed hourly buckets only. The current in-progress
    hour is left to the live entities and will be imported once the hour rolls
    over, which keeps the recorder path incremental and duplicate-safe.
    """
    if not records:
        return

    identity = statistic_identity or address
    water_id = _stat_id(identity, "total_water_usage")
    count_id = _stat_id(identity, "shower_count")

    # ---- Retrieve last imported state to resume from -----------------------
    instance = get_instance(hass)

    existing_water = await instance.async_add_executor_job(
        get_last_statistics, hass, 1, water_id, True, {"sum", "start"}
    )
    existing_count = await instance.async_add_executor_job(
        get_last_statistics, hass, 1, count_id, True, {"sum", "start"}
    )

    last_sum_water = 0.0
    last_sum_count = 0.0
    last_imported_ts: float = 0.0

    stats = existing_water.get(water_id)
    if stats:
        last_sum_water = stats[0].get("sum", 0.0)
        last_imported_ts = _as_utc_timestamp(stats[0].get("start"))

    stats_count = existing_count.get(count_id)
    if stats_count:
        last_sum_count = stats_count[0].get("sum", 0.0)

    current_hour = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )

    # ---- Filter to only new records from closed hours ----------------------
    sorted_records = sorted(records, key=lambda r: (r.start_time, r.session_id))
    new_records = []
    for record in sorted_records:
        hour = _as_utc_datetime(record.start_time).replace(
            minute=0, second=0, microsecond=0
        )
        if hour >= current_hour:
            continue
        if hour.timestamp() <= last_imported_ts:
            continue
        new_records.append(record)

    if not new_records:
        _LOGGER.debug(
            "No new usage records to import for %s (all %d already in statistics)",
            address,
            len(records),
        )
        return

    # ---- Build hourly StatisticData entries --------------------------------
    hourly_water: dict[datetime, float] = {}
    hourly_count: dict[datetime, int] = {}
    for record in new_records:
        hour = _as_utc_datetime(record.start_time).replace(
            minute=0, second=0, microsecond=0
        )
        hourly_water[hour] = hourly_water.get(hour, 0.0) + (
            record.volume_milliliters / 1000
        )
        hourly_count[hour] = hourly_count.get(hour, 0) + 1

    water_stats: list[StatisticData] = []
    count_stats: list[StatisticData] = []
    running_water = last_sum_water
    running_count = last_sum_count

    for hour in sorted(hourly_water):
        liters = hourly_water[hour]
        count = hourly_count.get(hour, 0)
        running_water += liters
        running_count += count
        water_stats.append(
            StatisticData(
                start=hour,
                state=liters,
                sum=running_water,
            )
        )
        count_stats.append(
            StatisticData(
                start=hour,
                state=count,
                sum=running_count,
            )
        )

    # ---- Submit to recorder -------------------------------------------------
    clean_name = address.replace(":", "")
    water_meta = StatisticMetaData(
        source=DOMAIN,
        statistic_id=water_id,
        unit_of_measurement="L",
        unit_class=_VOLUME_UNIT_CLASS,
        has_sum=True,
        has_mean=False,
        mean_type=_NO_MEAN_TYPE,
        name=f"Hai Shower {clean_name} Water Usage",
    )
    count_meta = StatisticMetaData(
        source=DOMAIN,
        statistic_id=count_id,
        unit_of_measurement="showers",
        unit_class=None,
        has_sum=True,
        has_mean=False,
        mean_type=_NO_MEAN_TYPE,
        name=f"Hai Shower {clean_name} Count",
    )

    async_add_external_statistics(hass, water_meta, water_stats)
    async_add_external_statistics(hass, count_meta, count_stats)

    _LOGGER.info(
        "Imported %d new usage records into HA statistics for %s "
        "(%d hourly buckets, %.1f L cumulative, %d showers cumulative)",
        len(new_records),
        address,
        len(water_stats),
        running_water,
        int(running_count),
    )
