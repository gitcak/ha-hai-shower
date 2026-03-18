"""Select platform for Hai Shower LED color settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ALERT_COLORS,
    CONF_ADDRESS,
    CONF_DEVICE_ID,
    DOMAIN,
    stable_device_identity,
)
from .entity import HaiShowerEntity
from .models import HaiShowerState

@dataclass(frozen=True, slots=True, kw_only=True)
class HaiShowerSelectDescription(SelectEntityDescription):
    """Describes a Hai Shower select entity."""

    value_fn: Callable[[HaiShowerState], str | None]


SELECTS: tuple[HaiShowerSelectDescription, ...] = (
    HaiShowerSelectDescription(
        key="water_led_color",
        translation_key="water_led_color",
        options=ALERT_COLORS,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.water_led_color,
    ),
    HaiShowerSelectDescription(
        key="temp_led_color",
        translation_key="temp_led_color",
        options=ALERT_COLORS,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.temp_led_color,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hai Shower select entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    address = entry.data[CONF_ADDRESS]
    device_identifier = stable_device_identity(entry.data.get(CONF_DEVICE_ID), address)
    async_add_entities(
        HaiShowerSelect(coordinator, device_identifier, address, desc)
        for desc in SELECTS
    )


class HaiShowerSelect(HaiShowerEntity, SelectEntity):
    """Writable select entity for Hai Shower LED color."""

    entity_description: HaiShowerSelectDescription

    def __init__(
        self,
        coordinator,
        device_identifier: str,
        address: str,
        description: HaiShowerSelectDescription,
    ) -> None:
        super().__init__(coordinator, device_identifier, address)
        self.entity_description = description
        self._attr_unique_id = f"{device_identifier}_{description.key}"

    @property
    def current_option(self) -> str | None:
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_select_option(self, option: str) -> None:
        """Write the selected color to the device."""
        target = "water" if self.entity_description.key == "water_led_color" else "temp"
        await self.coordinator.async_set_led_color(target, option)
