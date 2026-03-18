"""Migration helpers for Hai Shower config entries."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS, CONF_DEVICE_ID, ENTITY_KEYS

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entity_unique_ids(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    old_address: str | None = None,
) -> None:
    """Move legacy address-based entity unique IDs to the stable device ID.

    Older builds used the BLE address as the unique-ID prefix. That breaks
    entity continuity when the address changes. New builds use the stable cloud
    ``device_id`` prefix instead.
    """
    device_id = str(entry.data.get(CONF_DEVICE_ID, "")).strip()
    address = (old_address or entry.data.get(CONF_ADDRESS) or "").strip()
    if not device_id or not address:
        return

    try:
        from homeassistant.helpers import entity_registry as er
    except ImportError:
        return

    entity_registry = er.async_get(hass)
    migrated = 0
    legacy_prefix = f"{address}_"
    valid_keys = set(ENTITY_KEYS)

    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if not entity_entry.unique_id.startswith(legacy_prefix):
            continue
        entity_key = entity_entry.unique_id[len(legacy_prefix):]
        if entity_key not in valid_keys:
            continue
        entity_registry.async_update_entity(
            entity_entry.entity_id,
            new_unique_id=f"{device_id}_{entity_key}",
        )
        migrated += 1

    if migrated:
        _LOGGER.info(
            "Migrated %d Hai entity unique IDs from address %s to device %s",
            migrated,
            address,
            device_id,
        )
