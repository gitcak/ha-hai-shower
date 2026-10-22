"""Hai Shower integration."""

from __future__ import annotations

from bleak import BleakError

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)

from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_NAME,
    PLATFORMS,
)
from .coordinator import HaiShowerCoordinator
from .migrations import async_migrate_entity_unique_ids


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hai Shower from a config entry."""

    address = entry.data[CONF_ADDRESS]
    raw_key = entry.data.get(CONF_DEVICE_KEY, [])
    if not isinstance(raw_key, list) or not raw_key or not all(
        isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 255
        for item in raw_key
    ):
        # Reauth re-runs the one-time cloud login and stores a fresh key.
        raise ConfigEntryAuthFailed(
            "Hai device key is missing or invalid. Re-authenticate the integration."
        )

    device_key = raw_key
    await async_migrate_entity_unique_ids(hass, entry)
    coordinator = HaiShowerCoordinator(
        hass,
        entry,
        entry.data[CONF_ADDRESS],
        device_key,
        entry.data.get(CONF_DEVICE_ID),
        entry.data.get(CONF_NAME),
    )
    try:
        await coordinator.async_setup()
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryError:
        await coordinator.async_shutdown()
        raise
    except BleakError as err:
        await coordinator.async_shutdown()
        # Distinguish "not visible" (retry quickly) from "connect failed"
        # (retry with normal backoff).
        if not bluetooth.async_address_present(hass, address):
            raise ConfigEntryNotReady(
                f"Hai shower {address} is not visible via Bluetooth"
            ) from err
        raise ConfigEntryNotReady(
            f"Hai shower {address} BLE connection failed: {err}"
        ) from err
    except Exception as err:
        await coordinator.async_shutdown()
        raise ConfigEntryNotReady(
            f"Hai shower {address} is not ready yet: {err}"
        ) from err

    entry.runtime_data = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.async_shutdown()
        entry.runtime_data = None
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: HaiShowerCoordinator = entry.runtime_data
        await coordinator.async_shutdown()
        # Cleared so diagnostics reports the entry as not loaded while a
        # reload is retrying setup, instead of the shut-down coordinator.
        entry.runtime_data = None
    return unload_ok
