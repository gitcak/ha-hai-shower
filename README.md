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
- **Cloud history import** (retroactive backfill of old sessions) is not
implemented in v0.1

---

## Removal

1. Remove the integration from **Settings > Devices & Services**
2. If installed manually, delete `custom_components/hai_shower`
3. Restart Home Assistant

Existing recorder history will remain until HA purges it naturally.

---

## Prior Art

When this project started, no existing Home Assistant integration for the Hai
Smart Shower could be found. Two related projects were later discovered:

### hydrao-dump (kamaradclimber, ~2020)

A Python script and Wireshark dissector for the older **Hydrao**-branded
shower heads (the predecessor to Hai). It reads a handful of GATT
characteristics (`0x0012` volumes, `0x001a` temperature, `0x001e` flow) as
**plaintext** — no encryption, no pairing, no cloud login. The Hydrao devices
use the Bluetooth SIG base UUID range (`0000caXX-...`) and expose data
openly to any BLE client. The script publishes readings to MQTT on a 2-second
poll loop. No write support, no history sync, no Home Assistant integration.

- Repo: [https://github.com/kamaradclimber/hydrao-dump](https://github.com/kamaradclimber/hydrao-dump)
- Approach: BLE-only, plaintext reads, no auth
- Status: last updated ~2020

### hai-homeassistant (taylorfinnell, Feb 2024)

A Home Assistant custom integration for Hai showers, forked from the
[adizanni/hydrao](https://github.com/adizanni/hydrao) HA integration. It
connects via BLE with a **hardcoded XOR key** (`[1, 2, 3, 4, 5, 6]`),
bypassing cloud login entirely. It exposes 8 read-only sensor entities
(temperature, volume, duration for current and last session). No write
support, no history sync, no flow rate, no LED or alert configuration.

- Repo: [https://github.com/taylorfinnell/hai-homeassistant](https://github.com/taylorfinnell/hai-homeassistant)
- Approach: BLE-only, hardcoded XOR key, no cloud login
- Entities: 8 read-only sensors
- Status: 6 commits over 4 days (Feb 14–18, 2024), abandoned since. Zero
tests, zero stars/forks, no HACS support.

### How this integration differs


| Capability          | hydrao-dump            | hai-homeassistant    | **Hai Shower (this)**            |
| ------------------- | ---------------------- | -------------------- | -------------------------------- |
| Connection          | BLE plaintext          | BLE + hardcoded key  | Cloud bootstrap + BLE            |
| Auth method         | None                   | None (hardcoded key) | Cognito SRP → per-device key     |
| Encryption          | None (Hydrao era)      | XOR `[1,2,3,4,5,6]`  | XOR with cloud-provisioned key   |
| Sensors             | 3 (volume, temp, flow) | 8 (read-only)        | 10+ (read + write)               |
| Write support       | No                     | No                   | Alerts, LED colors, thresholds   |
| History sync        | No                     | No                   | Full session download + backfill |
| HA Energy dashboard | No                     | No                   | Yes (water usage statistics)     |
| Unit conversion     | No                     | Hardcoded mL         | Full metric/imperial via HA      |
| Tests               | 0                      | 0                    | 107                              |
| HACS ready          | No                     | No                   | Yes                              |
| Active development  | No (~2020)             | No (Feb 2024)        | Yes                              |


The taylorfinnell integration independently confirms several BLE
characteristic UUIDs and the XOR cipher format, serving as third-party
validation of the protocol reverse engineering that this integration is
built on. The hardcoded key `[1, 2, 3, 4, 5, 6]` appears to be a universal
factory default — this integration fetches the key from the cloud API as
the robust, forward-proof approach in case Hai ever issues per-device keys.

---

## Agentic Coding

This project used Claude and Codex as coding assistants for portions of the
implementation and documentation. They could not, however, take the long
showers multiple times a day while holding a cellphone like some sort of
heathen to debug or do any of the physical human things required to get this
project released.

---

## License

MIT License. See [LICENSE](LICENSE) for details.