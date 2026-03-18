"""Switch platform for Hai Shower alert enable/disable toggles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS, CONF_DEVICE_ID, DOMAIN, stable_device_identity
from .entity import HaiShowerEntity
from .models import HaiShowerState

@dataclass(frozen=True, slots=True, kw_only=True)
class HaiShowerSwitchDescription(SwitchEntityDescription):
    """Describes a Hai Shower switch entity."""

    value_fn: Callable[[HaiShowerState], bool | None]


SWITCHES: tuple[HaiShowerSwitchDescription, ...] = (
    HaiShowerSwitchDescription(
        key="water_alert_enabled",
        translation_key="water_alert_enabled",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.water_alert_enabled,
    ),
    HaiShowerSwitchDescription(
        key="temp_alert_enabled",
        translation_key="temp_alert_enabled",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.temp_alert_enabled,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hai Shower switch entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    address = entry.data[CONF_ADDRESS]
    device_identifier = stable_device_identity(entry.data.get(CONF_DEVICE_ID), address)
    async_add_entities(
        HaiShowerSwitch(coordinator, device_identifier, address, desc)
        for desc in SWITCHES
    )


class HaiShowerSwitch(HaiShowerEntity, SwitchEntity):
    """Writable switch entity for Hai Shower alert toggles."""

    entity_description: HaiShowerSwitchDescription

    def __init__(
        self,
        coordinator,
        device_identifier: str,
        address: str,
        description: HaiShowerSwitchDescription,
    ) -> None:
        super().__init__(coordinator, device_identifier, address)
        self.entity_description = description
        self._attr_unique_id = f"{device_identifier}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the alert."""
        target = "water" if self.entity_description.key == "water_alert_enabled" else "temp"
        await self.coordinator.async_set_alert_enabled(target, True)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the alert."""
        target = "water" if self.entity_description.key == "water_alert_enabled" else "temp"
        await self.coordinator.async_set_alert_enabled(target, False)
