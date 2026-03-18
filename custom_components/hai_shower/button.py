"""Button platform for Hai Shower."""

from __future__ import annotations
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS, CONF_DEVICE_ID, DOMAIN, stable_device_identity
from .entity import HaiShowerEntity


@dataclass(frozen=True, slots=True)
class HaiShowerButtonDescription(ButtonEntityDescription):
    """Describes a Hai Shower button."""


BUTTONS: tuple[HaiShowerButtonDescription, ...] = (
    HaiShowerButtonDescription(
        key="history_sync",
        translation_key="history_sync",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hai Shower buttons from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    address = entry.data[CONF_ADDRESS]
    device_identifier = stable_device_identity(entry.data.get(CONF_DEVICE_ID), address)
    async_add_entities(
        HaiShowerButton(coordinator, device_identifier, address, description)
        for description in BUTTONS
    )


class HaiShowerButton(HaiShowerEntity, ButtonEntity):
    """Representation of a Hai Shower action button."""

    entity_description: HaiShowerButtonDescription

    def __init__(
        self,
        coordinator,
        device_identifier: str,
        address: str,
        description: HaiShowerButtonDescription,
    ) -> None:
        super().__init__(coordinator, device_identifier, address)
        self.entity_description = description
        self._attr_unique_id = f"{device_identifier}_{description.key}"

    async def async_press(self) -> None:
        """Run the requested Hai action."""
        if self.entity_description.key == "history_sync":
            await self.coordinator.async_trigger_history_sync()
