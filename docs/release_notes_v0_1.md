# Hai Shower v0.1 Release Notes

This document describes the intended v0.1 scope for the Hai Shower Home
Assistant integration.

## What v0.1 Includes

- Discovery-first Home Assistant config flow for Hai Smart Shower devices
- One-time Hai cloud bootstrap during setup to fetch BLE key material
- Fully local BLE telemetry after setup
- Live water temperature, flow rate, battery, and active-session entities
- Automatic shower-end history sync
- Manual `History sync` diagnostic button
- Persistent local usage-record storage across Home Assistant restarts
- Session-derived sensors:
  - total water usage
  - shower count
  - last shower duration
  - last shower water usage
  - last shower average temperature
- Recorder statistics import for water usage and shower count
- Writable alert settings for:
  - water-use threshold
  - temperature threshold
  - water LED color
  - temperature LED color
  - water alert enable
  - temperature alert enable
- Phase 2 persistence for HA-managed alert settings via config entry options

## What Changed During This Release Cycle

Notable implementation work already folded into the current v0.1 tree:

- Flow-rate unit corrected to device-native mL/s and HA-facing L/min
- Mixed-format usage-record parsing fixed
- Post-shower runtime reset added after automatic sync
- BLE proxy churn hardening landed for intentional disconnects, mid-refresh
  drops, out-of-range notify deferral, and idle reconnect behavior
- Recorder statistics metadata fixes applied and live-validated in Home
  Assistant
- Automatic history sync now avoids replaying stored session-complete events on
  repeated syncs
- Recorder backfill now imports from the full synced history set before the
  local 250-record persistence cap is applied
- Config-entry recovery flows added for both cloud-key reauthentication and
  BLE address correction without reinstall
- Alert-setting writes (water threshold, LED colors) live-validated on
  hardware — payloads confirmed correct, no disconnects during write
- Setup guide and BLE troubleshooting guide added

## Known Limitations

- Standalone alert-setting writes (water threshold, water LED color,
  temperature LED color) are live-validated on real hardware. Composite
  `led_config` writes (temperature threshold + enable toggles) are unit-tested
  but not yet exercised live.
  - Composite `led_config` writes do not read back app-side peer enable states;
    the runtime warns when it has to assume an unknown peer bit as disabled
- Firmware version is not reliably readable through the ESPHome proxy path
  - The firmware sensor is disabled by default
- Cloud history import is not implemented
- App-side alert-setting changes are not yet read back into Home Assistant
- Extended multi-shower BLE proxy stability validation is still in progress

## Upgrade Notes

For existing local development installs:

- Restart Home Assistant after replacing the custom component files
- Reload the config entry if Home Assistant does not pick up the updated
  entity model immediately
- The firmware sensor may disappear from default-enabled entities because it is
  now disabled by default
- Alert settings previously written from Home Assistant now persist across
  restart via config entry options
- BLE address corrections can now be handled through Home Assistant
  reconfigure instead of deleting and recreating the entry

For fresh installs:

- Follow `docs/setup_and_usage.md`
- Use `docs/troubleshooting_ble_proxy.md` for Bluetooth/proxy issues

## Not Included In v0.1

- Ongoing cloud use for normal runtime telemetry
- Optional cloud history import/backfill
- Multi-device orchestration features beyond separate config entries
- App-side alert-setting drift readback
