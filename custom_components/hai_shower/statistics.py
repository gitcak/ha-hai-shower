"""Historical statistics backfill for Hai Shower usage records.

Imports device usage records into Home Assistant's long-term statistics
database via ``async_add_external_statistics``.  This gives users immediate
historical charts in the Energy > Water dashboard and Statistics panel,
even for showers taken before the integration was installed.

Imports are **incremental**: closed hourly buckets newer than the last
imported statistic are added, and a late-synced session (one not yet in the
caller's imported-session tracking) is added to its own hour. Recorded buckets
are only ever grown, preventing double-counting on restarts and syncs.

Statistic IDs are **per-device** and should be based on a stable device
identifier so BLE address corrections do not fork long-term statistics.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re
from typing import NamedTuple

from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    get_instance,
    statistics_during_period,
)
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMetaData,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import VolumeConverter

from .const import DOMAIN, MAX_TRACKED_IMPORTED_SESSIONS
from .models import HaiImportedSessions, HaiUsageRecord

_LOGGER = logging.getLogger(__name__)
_VOLUME_UNIT_CLASS = getattr(VolumeConverter, "UNIT_CLASS", "volume")
try:
    from homeassistant.components.recorder.models import StatisticMeanType

    _NO_MEAN_TYPE = StatisticMeanType.NONE
except ImportError:
    _NO_MEAN_TYPE = 0
# Recorder floats are compared against freshly summed litres.
_VOLUME_TOLERANCE = 1e-6


class _RecordedBucket(NamedTuple):
    """State and running sums of one recorded hourly bucket."""

    water: float
    count: float
    water_sum: float
    count_sum: float


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


async def _async_load_recorded_buckets(
    hass: HomeAssistant,
    water_id: str,
    count_id: str,
    first_hour_ts: float,
    last_hour_ts: float,
) -> dict[float, _RecordedBucket] | None:
    """Load the recorded hourly buckets between two hours, inclusive.

    Returns ``None`` when the water and count series cannot be paired up hour
    by hour, in which case already-imported hours are left untouched.
    """
    instance = get_instance(hass)
    rows = await instance.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime.fromtimestamp(first_hour_ts, timezone.utc),
        datetime.fromtimestamp(last_hour_ts, timezone.utc) + timedelta(hours=1),
        {water_id, count_id},
        "hour",
        None,
        {"state", "sum"},
    )
    water_rows = {
        _as_utc_timestamp(row.get("start")): row for row in rows.get(water_id, [])
    }
    count_rows = {
        _as_utc_timestamp(row.get("start")): row for row in rows.get(count_id, [])
    }
    if not water_rows or water_rows.keys() != count_rows.keys():
        return None
    buckets: dict[float, _RecordedBucket] = {}
    for hour_ts, water_row in water_rows.items():
        count_row = count_rows[hour_ts]
        values = (
            water_row.get("state"),
            count_row.get("state"),
            water_row.get("sum"),
            count_row.get("sum"),
        )
        if any(value is None for value in values):
            return None
        buckets[hour_ts] = _RecordedBucket(*(float(value) for value in values))
    return buckets


def _sums_before(
    buckets: dict[float, _RecordedBucket], hour_ts: float
) -> tuple[float, float]:
    """Return the running water and count sums just before ``hour_ts``."""
    earlier = [ts for ts in buckets if ts < hour_ts]
    if earlier:
        bucket = buckets[max(earlier)]
        return bucket.water_sum, bucket.count_sum
    # No recorded bucket in the window precedes the hour, so the first one
    # at or after it carries the total from before the window.
    bucket = buckets[min(ts for ts in buckets if ts >= hour_ts)]
    return bucket.water_sum - bucket.water, bucket.count_sum - bucket.count


async def async_import_usage_records(
    hass: HomeAssistant,
    address: str,
    records: list[HaiUsageRecord],
    *,
    statistic_identity: str | None = None,
    ignore_last_imported: bool = False,
    imported_sessions: HaiImportedSessions | None = None,
) -> HaiImportedSessions:
    """Import usage records as external statistics for the Water dashboard.

    Closed hours newer than the last imported bucket are appended from
    ``records``. A late session, one in an already-imported hour that is not
    in ``imported_sessions``, is added to its hour's recorded bucket and every
    later bucket is rewritten with its shifted running sum. That stays exact
    when the record cache has since been trimmed. Without tracking (first run
    after upgrade) an already-imported hour is instead rebuilt when
    ``records`` holds more sessions for it than the recorder does.

    Returns the updated tracking for the caller to persist; it is seeded from
    ``records`` when ``imported_sessions`` is None or on a repair rebuild.

    Home Assistant's external statistics API requires top-of-hour timestamps.
    We therefore import closed hourly buckets only. The current in-progress
    hour is left to the live entities and will be imported once the hour rolls
    over.
    """
    current_hour = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )

    # ---- Group closed-hour records ------------------------------------------
    closed: dict[int, tuple[float, HaiUsageRecord]] = {}
    for record in sorted(records, key=lambda r: (r.start_time, r.session_id)):
        hour = _as_utc_datetime(record.start_time).replace(
            minute=0, second=0, microsecond=0
        )
        if hour < current_hour:
            closed[record.session_id] = (hour.timestamp(), record)
    hourly_water: dict[float, float] = {}
    hourly_count: dict[float, float] = {}
    for hour_ts, record in closed.values():
        hourly_water[hour_ts] = hourly_water.get(hour_ts, 0.0) + (
            record.volume_milliliters / 1000
        )
        hourly_count[hour_ts] = hourly_count.get(hour_ts, 0.0) + 1

    seed = ignore_last_imported or imported_sessions is None
    if not closed:
        if imported_sessions is not None:
            return imported_sessions
        return HaiImportedSessions(since=current_hour.timestamp())

    identity = statistic_identity or address
    water_id = _stat_id(identity, "total_water_usage")
    count_id = _stat_id(identity, "shower_count")

    # ---- Retrieve last imported state to resume from -----------------------
    instance = get_instance(hass)

    existing_water = await instance.async_add_executor_job(
        get_last_statistics, hass, 1, water_id, True, {"sum", "state", "start"}
    )
    existing_count = await instance.async_add_executor_job(
        get_last_statistics, hass, 1, count_id, True, {"sum", "state", "start"}
    )

    last_sum_water = 0.0
    last_sum_count = 0.0
    last_imported_ts: float = 0.0

    stats = existing_water.get(water_id)
    if stats:
        # ``get_last_statistics`` can return a row whose ``sum`` key exists but
        # has a ``None`` value (e.g. buckets written by a non-sum writer or
        # transiently missing). ``dict.get(key, default)`` only honors the
        # default when the key is absent, so coerce explicitly to avoid a
        # ``TypeError`` on the ``running_water += liters`` arithmetic below.
        last_sum_water = float(stats[0].get("sum") or 0.0)
        last_imported_ts = _as_utc_timestamp(stats[0].get("start"))

    stats_count = existing_count.get(count_id)
    if stats_count:
        last_sum_count = float(stats_count[0].get("sum") or 0.0)

    if ignore_last_imported:
        # Repair backfill rebuilds closed-hour buckets from the full passed
        # record set. Seeding from recorder sums would double-count sessions
        # whose volumes were already imported under mis-decoded timestamps.
        last_sum_water = 0.0
        last_sum_count = 0.0
        last_imported_ts = 0.0

    # ---- Find late additions to already-imported hours ----------------------
    # Recorded buckets are only ever grown: shrinking one would make the
    # cumulative sum go backwards.
    late_water: dict[float, float] = {}
    late_count: dict[float, float] = {}
    late_session_ids: list[int] = []
    stored: dict[float, _RecordedBucket] | None = None
    if last_imported_ts > 0 and imported_sessions is not None:
        for session_id, (hour_ts, record) in closed.items():
            if (
                imported_sessions.since <= hour_ts <= last_imported_ts
                and session_id not in imported_sessions.sessions
            ):
                late_water[hour_ts] = late_water.get(hour_ts, 0.0) + (
                    record.volume_milliliters / 1000
                )
                late_count[hour_ts] = late_count.get(hour_ts, 0.0) + 1
                late_session_ids.append(session_id)
        if late_water:
            stored = await _async_load_recorded_buckets(
                hass, water_id, count_id, min(late_water), last_imported_ts
            )
            if stored is None:
                # Retried on the next import; the sessions stay untracked.
                _LOGGER.debug(
                    "Recorded buckets for %s could not be read; "
                    "deferring %d late sessions",
                    address,
                    len(late_session_ids),
                )
                late_water.clear()
                late_count.clear()
                late_session_ids.clear()
    elif last_imported_ts > 0:
        imported_hours = sorted(ts for ts in hourly_water if ts <= last_imported_ts)
        if imported_hours:
            stored = await _async_load_recorded_buckets(
                hass, water_id, count_id, imported_hours[0], last_imported_ts
            )
        for hour_ts in imported_hours if stored is not None else ():
            bucket = stored.get(hour_ts)  # type: ignore[union-attr]
            stored_water = bucket.water if bucket else 0.0
            stored_count = bucket.count if bucket else 0.0
            if (
                hourly_count[hour_ts] > stored_count
                and hourly_water[hour_ts] >= stored_water - _VOLUME_TOLERANCE
            ):
                late_water[hour_ts] = hourly_water[hour_ts] - stored_water
                late_count[hour_ts] = hourly_count[hour_ts] - stored_count

    # ---- Build hourly StatisticData entries --------------------------------
    buckets: list[tuple[float, float, float]] = []
    if late_water and stored is not None:
        rebuild_from = min(late_water)
        running_water, running_count = _sums_before(stored, rebuild_from)
        for hour_ts in sorted(
            {ts for ts in stored if ts >= rebuild_from} | set(late_water)
        ):
            bucket = stored.get(hour_ts)
            buckets.append(
                (
                    hour_ts,
                    (bucket.water if bucket else 0.0) + late_water.get(hour_ts, 0.0),
                    (bucket.count if bucket else 0.0) + late_count.get(hour_ts, 0.0),
                )
            )
    else:
        running_water, running_count = last_sum_water, last_sum_count
    new_hours = sorted(ts for ts in hourly_water if ts > last_imported_ts)
    buckets.extend(
        (hour_ts, hourly_water[hour_ts], hourly_count[hour_ts])
        for hour_ts in new_hours
    )

    tracked = _track_imported_sessions(
        imported_sessions,
        closed,
        seed=seed,
        session_ids=late_session_ids
        + [
            session_id
            for session_id, (hour_ts, _record) in closed.items()
            if hour_ts > last_imported_ts
        ],
    )

    if not buckets:
        _LOGGER.debug(
            "No new usage records to import for %s (all %d already in statistics)",
            address,
            len(records),
        )
        return tracked

    water_stats: list[StatisticData] = []
    count_stats: list[StatisticData] = []
    for hour_ts, liters, count in buckets:
        running_water += liters
        running_count += count
        hour = datetime.fromtimestamp(hour_ts, timezone.utc)
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
        unit_of_measurement=UnitOfVolume.LITERS,
        unit_class=_VOLUME_UNIT_CLASS,
        has_sum=True,
        mean_type=_NO_MEAN_TYPE,
        name=f"Hai Shower {clean_name} Water Usage",
    )
    count_meta = StatisticMetaData(
        source=DOMAIN,
        statistic_id=count_id,
        unit_of_measurement="showers",
        unit_class=None,
        has_sum=True,
        mean_type=_NO_MEAN_TYPE,
        name=f"Hai Shower {clean_name} Count",
    )

    async_add_external_statistics(hass, water_meta, water_stats)
    async_add_external_statistics(hass, count_meta, count_stats)

    _LOGGER.info(
        "Imported %d usage records into HA statistics for %s "
        "(%d hourly buckets, %.1f L cumulative, %d showers cumulative)",
        int(sum(late_count.values())) + int(sum(hourly_count[ts] for ts in new_hours)),
        address,
        len(water_stats),
        running_water,
        int(running_count),
    )
    return tracked


def _track_imported_sessions(
    imported_sessions: HaiImportedSessions | None,
    closed: dict[int, tuple[float, HaiUsageRecord]],
    *,
    seed: bool,
    session_ids: list[int],
) -> HaiImportedSessions:
    """Record newly imported sessions, keeping the newest tracked ones.

    When seeding, every closed record counts as imported: the recorder now
    holds it, or it predates tracking and cannot be told apart.
    """
    if seed or imported_sessions is None:
        sessions = {session_id: hour_ts for session_id, (hour_ts, _r) in closed.items()}
        since = min(sessions.values())
    else:
        sessions = dict(imported_sessions.sessions)
        sessions.update((session_id, closed[session_id][0]) for session_id in session_ids)
        since = imported_sessions.since
    if len(sessions) > MAX_TRACKED_IMPORTED_SESSIONS:
        cutoff = sorted(sessions.values())[-MAX_TRACKED_IMPORTED_SESSIONS]
        sessions = {
            session_id: hour_ts
            for session_id, hour_ts in sessions.items()
            if hour_ts >= cutoff
        }
        since = max(since, cutoff)
    return HaiImportedSessions(since=since, sessions=sessions)
