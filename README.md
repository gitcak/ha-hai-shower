# Hai Shower for Home Assistant

A local-first Home Assistant integration for the  
[Hai Smart Shower](https://www.gethai.com/). After a one-time cloud login  
during setup, all communication with the showerhead happens locally over  
Bluetooth, so no cloud dependency at runtime.

I created this integration by reverse engineering the haiapp APK and retriveing the shower head BLE encryption keys.  
- The keys may not be unique between devices (I only have the single shower head in my possesion) so cloud login for key pairing may go away entirely. (WIP)



---

## What It Does

- Live water temperature, flow rate, and battery monitoring during showers
- Automatic session tracking with duration, volume, and average temperature
- Usage history synced from the showerhead and stored locally
- Long-term statistics for the HA Energy dashboard (water usage and shower count)
- Writable alert settings — thresholds, LED colors, and enable toggles
- Works through an ESPHome Bluetooth proxy

---

## Requirements

- Home Assistant 2024.1 or newer
- A Hai Smart Shower
- An ESPHome Bluetooth proxy within range of the shower (or host Bluetooth)
- Hai account credentials for the one-time setup

---

## Installation

### HACS (recommended)

1. Add this repository as a custom repository in HACS
2. Search for "Hai Shower" and install
3. Restart Home Assistant

### Manual

1. Copy `custom_components/hai_shower` into your Home Assistant
  `custom_components/` directory
2. Restart Home Assistant

### Setup

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for **Hai Shower**
3. Pick the discovered shower from the list, or enter its Bluetooth address
  manually
4. Sign in with your Hai account — this fetches the device encryption key and
  is only needed once
5. If your account has multiple showers, select the matching device

Your credentials are not stored after setup. The integration only keeps the
device key needed for local BLE communication.

To confirm you've selected the right physical showerhead, compare the Product
ID shown in the Hai app (My Hai > Product Info) with the one the integration
reports after setup.

---

## Entities

### Sensors


| Entity                      | Description                                                |
| --------------------------- | ---------------------------------------------------------- |
| Water temperature           | Live temperature during a shower                           |
| Water flow rate             | Live flow in L/min (auto-converts to gal/min for imperial) |
| Battery                     | Showerhead battery voltage                                 |
| Session duration            | Duration of the current/last active session                |
| Session volume              | Water used in the current/last active session              |
| Total water usage           | Cumulative water usage across all synced sessions          |
| Shower count                | Total number of recorded showers                           |
| Last shower duration        | Duration of the most recent completed shower               |
| Last shower water usage     | Volume used in the most recent completed shower            |
| Last shower avg temperature | Average temperature of the most recent completed shower    |


All temperature, volume, and flow sensors automatically adapt to your Home
Assistant unit system. If your instance is set to imperial, you'll see °F,
gallons, and gal/min without any extra configuration. You can also override
units per-entity in the entity settings.

### Alert Settings


| Entity                      | Type            | Description                                  |
| --------------------------- | --------------- | -------------------------------------------- |
| Water-use alert threshold   | Number (slider) | Liters/gallons before the LED alert triggers |
| Temperature alert threshold | Number (slider) | Temperature threshold for the LED alert      |
| Water alert LED color       | Select          | LED color for the water-use alert            |
| Temperature alert LED color | Select          | LED color for the temperature alert          |
| Water-use alert             | Switch          | Enable/disable the water-use alert           |
| Temperature alert           | Switch          | Enable/disable the temperature alert         |


Alert settings written from Home Assistant persist across restarts. Changes
made in the Hai phone app are not currently synced back.

### Actions


| Entity       | Description                                               |
| ------------ | --------------------------------------------------------- |
| History sync | Button to manually pull usage records from the showerhead |


History also syncs automatically when a shower ends.

---

## How It Works

The integration polls the showerhead over BLE every 30 seconds when idle.
During an active shower, it subscribes to real-time temperature and shower-end
notifications from the device.

When a shower ends, the integration automatically syncs the usage history,
updates the session sensors, and imports the data into Home Assistant's
long-term statistics. Usage records are stored locally so your dashboard data
survives HA restarts.

The **Total water usage** sensor works with HA's Energy dashboard under the
Water tab.

---

## Maintenance

- **Reauthentication** — If the device key ever needs refreshing, use Home
Assistant's built-in reauthentication flow — no need to delete and re-add
the integration.
- **Address correction** — If the showerhead's Bluetooth address changes
(e.g., after a factory reset or proxy swap), use the reconfigure flow. Your
entities, statistics, and usage history carry over automatically.

---

## Troubleshooting

**Shower not found during setup?**
Make sure the ESPHome proxy is online and the showerhead is in BLE range. Keep
the Hai phone app closed during setup to avoid Bluetooth contention.

**Sensors showing "unavailable"?**
The shower is likely out of range or asleep. The integration recovers
automatically on the next successful connection — it won't show stale or
garbage data while disconnected.

**Firmware version unavailable?**
This is expected. The ESPHome proxy can't satisfy the BLE security level
needed to read that characteristic. The firmware sensor is disabled by default.

**History sync failing?**
Check the integration diagnostics for details (`last_history_sync_result`,
`last_history_sync_error`). If it persists, restart the ESPHome proxy and
reload the integration.

For more detail, see the
[BLE troubleshooting guide](docs/troubleshooting_ble_proxy.md).

---

## Known Limitations

- **Firmware version** is not readable through the ESPHome proxy path (sensor
disabled by default)
- **App-side alert changes** made in the Hai phone app are not synced back to
Home Assistant yet

---

## Removal

1. Remove the integration from **Settings > Devices & Services**
2. If installed manually, delete `custom_components/hai_shower`
3. Restart Home Assistant

Existing recorder history will remain until HA purges it naturally.

---

## Agentic Coding

This project used Claude and Codex as coding assistants for portions of the
implementation and documentation. They could not, however, take the long
showers multiple times a day while holding a cellphone like some sort of
heathen to debug or do any of the physical human things required to get this
project released.

---

[![Buy Me a Coffee](https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=☕&slug=nikcamaju&button_colour=ff8800&font_colour=000000&font_family=Lato&outline_colour=000000&coffee_colour=FFDD00)](https://buymeacoffee.com/nikcamaj)

---

## License

MIT License. See [LICENSE](LICENSE) for details.