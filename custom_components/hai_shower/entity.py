"""Base entity for Hai Shower."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HaiShowerCoordinator


class HaiShowerEntity(CoordinatorEntity[HaiShowerCoordinator]):
    """Base class for Hai Shower entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HaiShowerCoordinator,
        device_identifier: str,
        address: str,
    ) -> None:
        super().__init__(coordinator)
        self._address = address
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_identifier)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer="Hai",
            model="Smart Shower",
            name=coordinator.data.device_name or "Hai Smart Shower",
            sw_version=coordinator.data.firmware_version,
        )

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        """Expose shared diagnostic attributes across Hai entities."""
        attributes: dict[str, str | int] = {}
        if self.coordinator.data.product_id:
            attributes["product_id"] = self.coordinator.data.product_id
        if self.coordinator.data.active_session_id is not None:
            attributes["active_session_id"] = self.coordinator.data.active_session_id
        return attributes
