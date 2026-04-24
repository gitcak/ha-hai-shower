"""Sensor platform for Hai Shower."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS, CONF_DEVICE_ID, DOMAIN, stable_device_identity
from .entity import HaiShowerEntity
from .models import HaiLifecycleState, HaiShowerState
from .protocol import centicelsius_to_celsius, milliliters_to_liters

SHOWER_STATUS_OPTIONS = ["idle", "running", "syncing", "unreachable"]


@dataclass(frozen=True, slots=True, kw_only=True)
class HaiShowerSensorDescription(SensorEntityDescription):
    """Describes a Hai Shower sensor."""

    value_fn: Callable[[HaiShowerState], object]
    locally_derived: bool = False
    requires_value_for_availability: bool = False


def _flow_ml_per_sec_to_l_per_min(state: HaiShowerState) -> float | None:
    """Convert the device's mL/s flow reading into L/min for HA."""
    val = state.current_flow_ml_per_sec
    if val is None:
        return None
    return (val * 60) / 1000


def _shower_status(state: HaiShowerState) -> str:
    """Return a user-facing lifecycle state for dashboards."""
    if (
        state.lifecycle_state is HaiLifecycleState.SYNCING
        or state.last_history_sync_result == "running"
    ):
        return "syncing"
    if state.available and (
        state.current_temp_centicelsius is not None
        or state.current_flow_ml_per_sec is not None
        or state.session_duration_seconds is not None
        or state.session_volume_milliliters is not None
    ):
        return "running"
    if state.last_seen_at is not None or state.usage_records or state.last_usage_record:
        return "idle"
    return "unreachable"


# ---------------------------------------------------------------------------
# Sensor descriptions
# ---------------------------------------------------------------------------
# HA automatically converts units based on the user's configured unit system
# when a device_class is set.  We always provide values in metric; HA handles
# the conversion to imperial (gallons, °F, etc.) in the frontend.
# ---------------------------------------------------------------------------

SENSORS: tuple[HaiShowerSensorDescription, ...] = (
    # --- Derived device status ---
    HaiShowerSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=SHOWER_STATUS_OPTIONS,
        locally_derived=True,
        value_fn=_shower_status,
    ),
    HaiShowerSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        locally_derived=True,
        value_fn=lambda state: state.last_seen_at,
    ),
    # --- Live session sensors ---
    HaiShowerSensorDescription(
        key="current_temperature",
        translation_key="current_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda state: centicelsius_to_celsius(state.current_temp_centicelsius),
    ),
    HaiShowerSensorDescription(
        key="current_flow",
        translation_key="current_flow",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_flow_ml_per_sec_to_l_per_min,
    ),
    # --- Cumulative sensors (feed HA Energy > Water dashboard) ---
    # These are locally_derived=True so they remain available even when the
    # shower is disconnected — their values come from persisted usage records.
    HaiShowerSensorDescription(
        key="total_water_usage",
        translation_key="total_water_usage",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        locally_derived=True,
        value_fn=lambda state: milliliters_to_liters(state.total_water_usage_ml) if state.total_water_usage_ml else None,
    ),
    HaiShowerSensorDescription(
        key="shower_count",
        translation_key="shower_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        locally_derived=True,
        value_fn=lambda state: state.shower_count,
    ),
    # --- Last session summary sensors ---
    HaiShowerSensorDescription(
        key="last_session_duration",
        translation_key="last_session_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        locally_derived=True,
        value_fn=lambda state: state.last_session_duration_seconds,
    ),
    HaiShowerSensorDescription(
        key="last_session_volume",
        translation_key="last_session_volume",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=1,
        locally_derived=True,
        value_fn=lambda state: milliliters_to_liters(state.last_session_volume_ml) if state.last_session_volume_ml else None,
    ),
    HaiShowerSensorDescription(
        key="last_session_avg_temp",
        translation_key="last_session_avg_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        locally_derived=True,
        value_fn=lambda state: centicelsius_to_celsius(state.last_session_avg_temp_cc),
    ),
    # --- Active session (from BLE reads, updated during shower) ---
    HaiShowerSensorDescription(
        key="session_duration",
        translation_key="session_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda state: state.session_duration_seconds,
    ),
    HaiShowerSensorDescription(
        key="session_volume",
        translation_key="session_volume",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=1,
        value_fn=lambda state: milliliters_to_liters(state.session_volume_milliliters),
    ),
    # --- Diagnostic sensors ---
    HaiShowerSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement="mV",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.battery_level_mv,
    ),
    HaiShowerSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        requires_value_for_availability=True,
        value_fn=lambda state: state.firmware_version,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hai Shower sensors from a config entry."""

    coordinator = hass.data[DOMAIN][entry.entry_id]
    address = entry.data[CONF_ADDRESS]
    device_identifier = stable_device_identity(entry.data.get(CONF_DEVICE_ID), address)
    async_add_entities(
        HaiShowerSensor(coordinator, device_identifier, address, description)
        for description in SENSORS
    )


class HaiShowerSensor(HaiShowerEntity, SensorEntity):
    """Representation of a Hai Shower sensor."""

    entity_description: HaiShowerSensorDescription

    def __init__(
        self,
        coordinator,
        device_identifier: str,
        address: str,
        description: HaiShowerSensorDescription,
    ) -> None:
        super().__init__(coordinator, device_identifier, address)
        self.entity_description = description
        self._attr_unique_id = f"{device_identifier}_{description.key}"

    @property
    def native_value(self) -> object:
        """Return the sensor value.

        Locally-derived sensors (cumulative, last-session) always return their
        value because it comes from persisted usage records, not live BLE.
        Live BLE sensors return None when the device is unavailable to prevent
        garbage values in HA history.
        """
        if not self.entity_description.locally_derived and not self.coordinator.data.available:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return entity availability.

        Locally-derived sensors stay available as long as they have a value,
        even when the shower is disconnected.
        """
        if self.entity_description.locally_derived:
            return self.entity_description.value_fn(self.coordinator.data) is not None
        if self.entity_description.requires_value_for_availability:
            return (
                self.coordinator.data.available
                and self.entity_description.value_fn(self.coordinator.data) is not None
            )
        return self.coordinator.data.available

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose shared diagnostic attributes and recent sessions."""
        attrs = super().extra_state_attributes
        if self.entity_description.key == "shower_count":
            attrs["recent_sessions"] = self.coordinator.recent_sessions()
        return attrs
