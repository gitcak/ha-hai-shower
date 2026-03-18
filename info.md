# Hai Shower

A local-first Home Assistant integration for Hai Smart Shower devices.

## What It Does

- Connects to the showerhead over Bluetooth (via ESPHome proxy or host adapter)
- One-time Hai cloud login during setup to fetch the device encryption key
- Live water temperature, flow rate, and battery monitoring
- Automatic usage history sync after each shower
- Long-term statistics for the HA Energy dashboard (water tab)
- Writable alert settings — thresholds, LED colors, and enable toggles

After setup, everything runs locally over BLE. No cloud dependency at runtime.

## Requirements

- Home Assistant 2024.1 or newer
- A Hai Smart Shower
- Bluetooth connectivity (ESPHome proxy recommended)
- Hai account credentials for the one-time setup

## Installation

1. Add this repository as a custom repository in HACS
2. Search for "Hai Shower" and install
3. Restart Home Assistant
4. Go to **Settings > Devices & Services > Add Integration** and search for **Hai Shower**

For manual installation and detailed setup, see the [README](https://github.com/gitcak/ha-hai-shower).
