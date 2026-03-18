"""Number platform for Hai Shower writable settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS, CONF_DEVICE_ID, DOMAIN, stable_device_identity
from .entity import HaiShowerEntity
from .models import HaiShowerState

@dataclass(frozen=True, slots=True, kw_only=True)
class HaiShowerNumberDescription(NumberEntityDescription):
    """Describes a Hai Shower number entity."""

    value_fn: Callable[[HaiShowerState], float | None]


NUMBERS: tuple[HaiShowerNumberDescription, ...] = (
    HaiShowerNumberDescription(
        key="water_alert_threshold",
        translation_key="water_alert_threshold",
        device_class=NumberDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        native_min_value=10.0,
        native_max_value=100.0,
        native_step=1.0,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.water_alert_threshold_liters,
    ),
    HaiShowerNumberDescription(
        key="temp_alert_threshold",
        translation_key="temp_alert_threshold",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=30.0,
        native_max_value=50.0,
        native_step=0.5,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.temp_alert_threshold_celsius,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hai Shower number entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    address = entry.data[CONF_ADDRESS]
    device_identifier = stable_device_identity(entry.data.get(CONF_DEVICE_ID), address)
    async_add_entities(
        HaiShowerNumber(coordinator, device_identifier, address, desc)
        for desc in NUMBERS
    )


class HaiShowerNumber(HaiShowerEntity, NumberEntity):
    """Writable number entity for Hai Shower settings."""

    entity_description: HaiShowerNumberDescription

    def __init__(
        self,
        coordinator,
        device_identifier: str,
        address: str,
        description: HaiShowerNumberDescription,
    ) -> None:
        super().__init__(coordinator, device_identifier, address)
        self.entity_description = description
        self._attr_unique_id = f"{device_identifier}_{description.key}"

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        """Write the value to the device."""
        if self.entity_description.key == "water_alert_threshold":
            await self.coordinator.async_set_water_alert_threshold(value)
            return
        await self.coordinator.async_set_temp_alert_threshold(value)
