"""Config flow for Hai Shower with one-time cloud login bootstrap."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .ble import async_read_product_id
from .cloud import (
    HaiCloudAuthError,
    HaiCloudClient,
    HaiCloudConnectionError,
    HaiCloudResponseError,
)
from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_CODE,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_NAME,
    DEFAULT_NAME,
    DOMAIN,
    HAI_LOCAL_NAME,
    HAI_SERVICE_UUIDS,
    key_summary,
    short_id,
)
from .migrations import async_migrate_entity_unique_ids

_LOGGER = logging.getLogger(__name__)
MAC_ADDRESS_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


class HaiShowerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hai Shower."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._cloud_client: HaiCloudClient | None = None
        self._devices: list[dict[str, Any]] = []
        self._discovered_addresses: dict[str, str] = {}
        self._address: str = ""
        self._device_id: str = ""
        self._name: str = DEFAULT_NAME

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle a request to refresh cloud bootstrap data for an existing entry."""
        self._address = str(entry_data.get(CONF_ADDRESS, "")).upper()
        self._device_id = str(entry_data.get(CONF_DEVICE_ID, ""))
        self._name = entry_data.get(CONF_NAME) or DEFAULT_NAME
        return await self.async_step_reauth_confirm()

    async def async_step_reconfigure(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Handle a request to correct the BLE address for an existing entry."""
        self._address = str(entry_data.get(CONF_ADDRESS, "")).upper()
        self._device_id = str(entry_data.get(CONF_DEVICE_ID, ""))
        self._name = entry_data.get(CONF_NAME) or DEFAULT_NAME
        return await self.async_step_reconfigure_confirm()

    async def async_step_reconfigure_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Confirm a corrected BLE address for the existing entry."""
        errors: dict[str, str] = {}
        discovered = self._async_discovered_hai_addresses()
        self._discovered_addresses = discovered

        if user_input is not None:
            selected = self._extract_address_selection(user_input)
            errors = selected["errors"]
            if not errors:
                address = selected["address"]
                if self._address_in_use_by_other_entry(address):
                    errors["base"] = "address_in_use"
                else:
                    self._address = address
                    self._name = user_input.get(CONF_NAME) or self._name or DEFAULT_NAME
                    await self.async_set_unique_id(self._device_id)
                    self._abort_if_unique_id_mismatch()
                    entry = self._get_reconfigure_entry()
                    await async_migrate_entity_unique_ids(
                        self.hass,
                        entry,
                        old_address=str(entry.data.get(CONF_ADDRESS, "")).upper(),
                    )
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_ADDRESS: self._address,
                            CONF_NAME: self._name,
                        },
                        reason="reconfigure_successful",
                    )

        return self._show_address_form(
            step_id="reconfigure_confirm",
            discovered=discovered,
            errors=errors,
            default_name=self._name or DEFAULT_NAME,
            default_manual_address=self._address or None,
            description_placeholders={"address": self._address, "name": self._name},
        )

    async def async_step_reauth_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Confirm reauthentication and refresh the stored device key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await self._async_authenticate_and_load_devices(
                user_input["username"], user_input["password"]
            )
            if not errors:
                return await self.async_step_select_device()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("username"): str,
                    vol.Required("password"): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "address": self._address,
                "name": self._name,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Step 1: Select a discovered device or enter a fallback address."""
        errors: dict[str, str] = {}
        discovered = self._async_discovered_hai_addresses()

        _LOGGER.debug(
            "Config flow user step for %s: discovered %d Hai candidates: %s",
            short_id(self._address) if self._address else "new-flow",
            len(discovered),
            list(discovered.keys()),
        )

        if user_input is not None:
            chosen_address = (user_input.get("discovered_device") or "").upper()
            manual_address = (user_input.get(CONF_ADDRESS) or "").upper()
            _LOGGER.debug(
                "Config flow user input received: discovered_device=%s manual_address=%s name=%s",
                short_id(chosen_address) if chosen_address else "empty",
                short_id(manual_address) if manual_address else "empty",
                user_input.get(CONF_NAME) or DEFAULT_NAME,
            )
            selected = self._extract_address_selection(user_input, discovered=discovered)
            errors = selected["errors"]
            if not errors:
                self._address = selected["address"]
                self._name = user_input.get(CONF_NAME) or DEFAULT_NAME
                _LOGGER.debug(
                    "Config flow user step selected %s (%s)",
                    self._address,
                    self._name,
                )
                return await self.async_step_cloud_login()

        self._discovered_addresses = discovered
        return self._show_address_form(
            step_id="user",
            discovered=discovered,
            errors=errors,
            default_name=DEFAULT_NAME,
            default_discovered_address=next(iter(discovered)) if len(discovered) == 1 else None,
        )

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle Bluetooth discovery from Home Assistant."""
        if not discovery_info.connectable:
            _LOGGER.debug(
                "Ignoring non-connectable Bluetooth discovery for %s",
                discovery_info.address,
            )
            return self.async_abort(reason="not_supported")

        address = discovery_info.address.upper()
        if not self._is_hai_candidate(discovery_info):
            _LOGGER.debug(
                "Ignoring unsupported Bluetooth discovery for %s (%s)",
                address,
                discovery_info.name,
            )
            return self.async_abort(reason="not_supported")

        self._address = address
        self._name = discovery_info.name or DEFAULT_NAME
        _LOGGER.debug(
            "Accepted Bluetooth discovery for %s (%s)",
            self._address,
            self._name,
        )
        self._discovered_addresses = {
            address: self._format_discovery_label(discovery_info)
        }
        return await self.async_step_confirm_discovery()

    async def async_step_confirm_discovery(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Confirm a Bluetooth-discovered shower before cloud bootstrap."""
        if user_input is not None:
            self._name = user_input.get(CONF_NAME) or self._name or DEFAULT_NAME
            return await self.async_step_cloud_login()

        return self.async_show_form(
            step_id="confirm_discovery",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=self._name or DEFAULT_NAME): str,
                }
            ),
            description_placeholders={
                "address": self._address,
                "name": self._name or HAI_LOCAL_NAME,
            },
        )

    async def async_step_cloud_login(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Step 2: One-time Hai cloud login to fetch device key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await self._async_authenticate_and_load_devices(
                user_input["username"], user_input["password"]
            )
            if not errors:
                if not self._devices:
                    errors["base"] = "no_devices"
                else:
                    return await self.async_step_select_device()

        return self.async_show_form(
            step_id="cloud_login",
            data_schema=vol.Schema(
                {
                    vol.Required("username"): str,
                    vol.Required("password"): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "address": self._address,
            },
        )

    async def async_step_select_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Select which cloud device matches this showerhead."""
        errors: dict[str, str] = {}

        device_map: dict[str, dict[str, Any]] = {}
        for dev in self._devices:
            # Live API validation showed /devices returns serials and the
            # detail route accepts that serial directly.
            dev_id = (
                dev.get("id")
                or dev.get("deviceId")
                or dev.get("serial")
                or ""
            )
            if dev_id:
                device_map[dev_id] = dev

        _LOGGER.debug(
            "Device selection step built %d selectable devices for %s",
            len(device_map),
            self._address,
        )

        if not device_map:
            if self._cloud_client is not None:
                await self._cloud_client.close()
                self._cloud_client = None
            return self.async_abort(reason="no_selectable_devices")

        if self._address:
            product_id = await async_read_product_id(self.hass, self._address)
            if product_id and product_id in device_map:
                _LOGGER.info(
                    "Matched Hai BLE device %s to cloud serial %s via product ID",
                    self._address,
                    short_id(product_id),
                )
                return await self._fetch_key_and_create_entry(product_id)

        if len(device_map) == 1:
            only_id = next(iter(device_map))
            return await self._fetch_key_and_create_entry(only_id)

        if user_input is not None:
            chosen_id = user_input["device"]
            if chosen_id in device_map:
                return await self._fetch_key_and_create_entry(chosen_id)
            errors["base"] = "invalid_device"

        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(
                        {did: did[:12] for did in device_map}
                    ),
                }
            ),
            errors=errors,
        )

    def _async_discovered_hai_addresses(self) -> dict[str, str]:
        """Return nearby connectable Hai candidates from Home Assistant Bluetooth."""
        candidates: dict[str, str] = {}
        for service_info in bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        ):
            if not self._is_hai_candidate(service_info):
                continue
            candidates[service_info.address.upper()] = self._format_discovery_label(
                service_info
            )
        _LOGGER.debug(
            "Bluetooth candidate scan found %d Hai candidates: %s",
            len(candidates),
            list(candidates.keys()),
        )
        return candidates

    def _is_hai_candidate(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> bool:
        """Check whether Bluetooth discovery data looks like a Hai shower."""
        local_name = (service_info.name or "").strip()
        if local_name == HAI_LOCAL_NAME:
            return True

        service_uuids = {
            uuid.upper() for uuid in getattr(service_info, "service_uuids", []) or []
        }
        return bool(service_uuids & HAI_SERVICE_UUIDS)

    def _format_discovery_label(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> str:
        """Format a nearby Hai device for display in the config flow."""
        name = service_info.name or HAI_LOCAL_NAME
        rssi = getattr(service_info, "rssi", None)
        if isinstance(rssi, int):
            return f"{name} ({service_info.address.upper()}, RSSI {rssi})"
        return f"{name} ({service_info.address.upper()})"

    async def _fetch_key_and_create_entry(
        self, device_id: str
    ) -> FlowResult:
        """Fetch per-device key material via GET /devices/{id}, then create entry."""
        client = self._cloud_client
        if client is None:
            return self.async_abort(reason="unknown_error")

        try:
            device = await client.get_device(device_id)
            device_key = self._normalize_device_key(device.get("key"))
        except HaiCloudAuthError as err:
            _LOGGER.warning(
                "Hai cloud auth expired during device bootstrap for %s: %s",
                short_id(device_id),
                err,
            )
            return self.async_abort(reason="auth_failed")
        except HaiCloudConnectionError as err:
            _LOGGER.warning(
                "Unable to fetch key material for %s: %s",
                short_id(device_id),
                err,
            )
            return self.async_abort(reason="cannot_connect")
        except HaiCloudResponseError as err:
            _LOGGER.warning(
                "Hai cloud returned invalid device details for %s: %s",
                short_id(device_id),
                err,
            )
            return self.async_abort(reason="invalid_api_response")
        except ValueError as err:
            _LOGGER.warning(
                "Hai cloud returned unusable key material for %s: %s",
                short_id(device_id),
                err,
            )
            return self.async_abort(reason="invalid_device_key")
        except Exception:
            _LOGGER.exception(
                "Failed to fetch key material for device %s", short_id(device_id)
            )
            return self.async_abort(reason="device_fetch_failed")
        finally:
            await client.close()
            self._cloud_client = None

        device_code = str(device.get("code", ""))
        _LOGGER.info(
            "Hai cloud bootstrap fetched key metadata for %s (device %s, %s)",
            self._address,
            short_id(device_id),
            key_summary(device_key),
        )

        if self.source == "reauth":
            entry = self._get_reauth_entry()
            expected_device_id = str(entry.data.get(CONF_DEVICE_ID, ""))
            if device_id != expected_device_id:
                _LOGGER.warning(
                    "Hai reauth selected wrong device %s for entry %s (expected %s)",
                    short_id(device_id),
                    entry.entry_id,
                    short_id(expected_device_id),
                )
                return self.async_abort(reason="wrong_device")
            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    CONF_DEVICE_ID: device_id,
                    CONF_DEVICE_KEY: device_key,
                    CONF_DEVICE_CODE: device_code,
                },
                reason="reauth_successful",
            )

        await self.async_set_unique_id(device_id)
        self._device_id = device_id
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=self._name,
            data={
                CONF_ADDRESS: self._address,
                CONF_NAME: self._name,
                CONF_DEVICE_ID: device_id,
                CONF_DEVICE_KEY: device_key,
                CONF_DEVICE_CODE: device_code,
            },
        )

    async def _async_authenticate_and_load_devices(
        self, username: str, password: str
    ) -> dict[str, str]:
        """Authenticate to Hai cloud and cache the selectable devices."""
        errors: dict[str, str] = {}
        client = HaiCloudClient()
        try:
            await client.authenticate(username, password)
            self._devices = await client.get_devices()
            _LOGGER.debug(
                "Hai cloud login step discovered %d candidate devices for %s",
                len(self._devices),
                self._address,
            )
        except HaiCloudAuthError as err:
            _LOGGER.warning("Hai cloud auth failed: %s", err)
            errors["base"] = "auth_failed"
            await client.close()
        except HaiCloudConnectionError as err:
            _LOGGER.warning("Hai cloud connection failed: %s", err)
            errors["base"] = "cannot_connect"
            await client.close()
        except HaiCloudResponseError as err:
            _LOGGER.warning("Hai cloud returned invalid bootstrap data: %s", err)
            errors["base"] = "invalid_api_response"
            await client.close()
        except Exception:
            _LOGGER.exception("Unexpected error during Hai cloud login")
            errors["base"] = "unknown_error"
            await client.close()
        else:
            if not self._devices:
                await client.close()
            else:
                self._cloud_client = client
        return errors

    def _normalize_device_key(self, raw_key: Any) -> list[int]:
        """Normalize cloud key material into a byte list."""
        if isinstance(raw_key, str):
            stripped = re.sub(r"[^0-9A-Fa-f]", "", raw_key)
            if stripped and len(stripped) % 2 == 0 and len(stripped) >= 2:
                return list(bytes.fromhex(stripped))
            if raw_key:
                return [ord(char) for char in raw_key]
            raise ValueError("Device key was empty")

        if isinstance(raw_key, (list, tuple)):
            normalized: list[int] = []
            for item in raw_key:
                if isinstance(item, bool):
                    raise ValueError("Device key contained a boolean value")
                try:
                    value = int(item)
                except (TypeError, ValueError) as err:
                    raise ValueError("Device key contained a non-integer value") from err
                if not 0 <= value <= 255:
                    raise ValueError("Device key byte was outside the 0-255 range")
                normalized.append(value)
            if normalized:
                return normalized
            raise ValueError("Device key was empty")

        raise ValueError(f"Unsupported device key type: {type(raw_key).__name__}")

    def _extract_address_selection(
        self,
        user_input: dict[str, str],
        *,
        discovered: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Validate discovered/manual address input and return the selected address."""
        errors: dict[str, str] = {}
        chosen_address = (user_input.get("discovered_device") or "").upper()
        manual_address = (user_input.get(CONF_ADDRESS) or "").upper()
        single_address = None
        if discovered and len(discovered) == 1:
            single_address = next(iter(discovered))

        if chosen_address and manual_address:
            errors["base"] = "choose_one_address"
        else:
            address = chosen_address or manual_address or (single_address or "")
            if not address:
                _LOGGER.debug("Config flow address step has no selected or manual address")
                errors["base"] = "no_address"
            elif not MAC_ADDRESS_RE.match(address):
                _LOGGER.debug(
                    "Config flow address step rejected invalid address: %s",
                    address,
                )
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                return {"address": address, "errors": errors}

        return {"address": "", "errors": errors}

    def _show_address_form(
        self,
        *,
        step_id: str,
        discovered: dict[str, str],
        errors: dict[str, str],
        default_name: str,
        default_discovered_address: str | None = None,
        default_manual_address: str | None = None,
        description_placeholders: dict[str, str] | None = None,
    ) -> FlowResult:
        """Render a discovered/manual address selection form."""
        discovered_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=address, label=label)
                    for address, label in discovered.items()
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
                sort=True,
            )
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "discovered_device",
                        default=(
                            default_discovered_address
                            if default_discovered_address
                            else vol.UNDEFINED
                        ),
                    ): discovered_selector if discovered else str,
                    vol.Optional(
                        CONF_ADDRESS,
                        default=default_manual_address or vol.UNDEFINED,
                    ): str,
                    vol.Optional(CONF_NAME, default=default_name): str,
                }
            ),
            errors=errors,
            description_placeholders=description_placeholders or {},
        )

    def _address_in_use_by_other_entry(self, address: str) -> bool:
        """Check whether another Hai config entry already uses this address."""
        entries_method = getattr(self.hass.config_entries, "async_entries", None)
        if entries_method is None:
            return False
        for entry in entries_method(DOMAIN):
            if entry is self._get_reconfigure_entry():
                continue
            if str(entry.data.get(CONF_ADDRESS, "")).upper() == address:
                return True
        return False
