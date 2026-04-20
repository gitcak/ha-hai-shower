# Hai Shower Setup and Usage

This guide covers the supported setup paths for the Hai Shower Home Assistant
integration and the entities it exposes once configured.

## Before You Start

You need:

- Home Assistant with Bluetooth support
- An ESPHome Bluetooth proxy within range of the shower
- A Hai account username and password for the one-time setup bootstrap
- The Hai shower powered on and advertising over BLE

What the cloud login is used for:

- Setup fetches the per-device encryption key from Hai's API
- Credentials are used only during setup bootstrap
- Normal telemetry, history sync, and alert-setting control are local BLE

## Installation

Supported install paths:

### HACS

1. Add `https://github.com/gitcak/ha-hai-shower` as a custom repository in
   HACS.
2. Search for `Hai Shower`.
3. Install the integration from HACS.
4. Restart Home Assistant.

### Manual

1. Copy `integration/custom_components/hai_shower` into your Home Assistant
   `custom_components` directory.
2. Restart Home Assistant.
3. Confirm the ESPHome Bluetooth proxy is online and the shower is in range.
4. In Home Assistant, go to `Settings -> Devices & Services -> Add Integration`.
5. Search for `Hai Shower`.

## Removal

To remove the integration cleanly:

1. In Home Assistant, go to `Settings -> Devices & Services`.
2. Open the Hai Shower config entry and remove it.
3. If you installed manually, delete `custom_components/hai_shower`.
4. Restart Home Assistant.

Removing the config entry unloads the integration and removes the entities.
Historical recorder data may remain until Home Assistant purges it.

## ESPHome Bluetooth Proxy

The integration expects Home Assistant to reach the shower through a Bluetooth
proxy. In practice that means:

- The proxy must be close enough to the shower for stable BLE discovery
- Home Assistant must see the proxy as an active Bluetooth source
- The shower should appear as a connectable BLE device with local name
  `haiSmartShower`

This integration does not talk to the shower through the phone app. Phone
Bluetooth access can still interfere with BLE visibility, so keep the phone
away from active pairing/debug sessions when validating behavior.

## Config Flow

The config flow is discovery-first.

### Step 1: Choose the shower

Home Assistant will list nearby Hai shower candidates discovered over
Bluetooth. If discovery does not find the shower, you can enter the Bluetooth
address manually.

To match the right physical device:

- Open the Hai app
- Go to `My Hai -> Product Info`
- Compare the Product ID shown there with the Product ID exposed by the
  integration after setup

### Step 2: Hai cloud login

The flow prompts for your Hai account email and password. This is required to
fetch the device key used for local BLE decryption.

The integration does not store the password after setup.

### Step 3: Select the matching cloud device

If the Hai account has multiple devices, the flow asks which cloud device
matches the discovered BLE shower. The integration uses that device detail
response to retrieve the BLE key material.

After the config entry is created, Home Assistant performs the first refresh
and creates the device/entities.

### Later maintenance

- Use Home Assistant's reauthentication flow if the stored Hai cloud key ever
  needs to be refreshed.
- Use Home Assistant's reconfigure flow if the shower's BLE address changes or
  needs to be corrected.
- Address correction does not require deleting and re-adding the integration;
  the integration now migrates its stable IDs and persisted usage data forward.

## What You Get

### Live BLE sensors

- Water temperature
- Water flow rate
- Battery
- Session duration
- Session volume

### Session-derived sensors

- Total water usage
- Shower count
- Last shower duration
- Last shower water usage
- Last shower average temperature

### Actions

- `History sync` diagnostic button
  - Manually re-requests usage records from the shower
  - Automatic history sync also runs after shower-end notifications

### Writable alert settings

- Water-use alert threshold
- Temperature alert threshold
- Water alert LED color
- Temperature alert LED color
- Water alert enable switch
- Temperature alert enable switch

Phase 2 persistence is implemented for HA-managed alert values:

- Values written from Home Assistant are stored in config entry options
- Those values are restored on restart
- App-side changes made outside Home Assistant are not yet read back into HA

For release/version alignment details, see `docs/release_process.md`.

## Runtime Behavior

- The integration polls the shower over BLE on a 30-second interval when idle
- During an active shower, runtime notifications drive temperature and
  shower-end handling
- After shower end, the integration performs an automatic history sync and
  updates session-derived sensors
- Usage records are stored locally so cumulative/last-session sensors survive
  Home Assistant restarts
- Long-term statistics are imported for water usage and shower count

## Current Limitations

- Firmware version is not reliably readable through the ESPHome proxy path
  because the proxy cannot satisfy the required BLE security level
  - The firmware sensor is therefore disabled by default
- Alert-setting writes are implemented and unit-tested, but broader real-
  hardware spot-check coverage is still limited
  - Composite `led_config` writes still do not read back app-side peer enable
    states. The runtime now warns when it has to assume an unknown peer bit as
    disabled.
- Multi-shower extended stability validation is still in progress

## Diagnostics

Useful places to inspect runtime behavior:

- Home Assistant integration diagnostics export
- The `History sync` button
- Home Assistant logs for BLE proxy/read/write failures

Diagnostics include recent session summaries and detailed history-sync outcome
fields such as requested time, started time, completed time, trigger source,
result, error text, and record count.
