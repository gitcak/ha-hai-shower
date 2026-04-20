# Hai Shower

A local-first Home Assistant integration for Hai Smart Shower devices.

## Highlights

- Live water temperature, flow rate, and battery monitoring
- Automatic usage history sync after each shower
- Long-term statistics for the HA Energy dashboard (water tab)
- Writable alert settings — thresholds, LED colors, and enable toggles
- Works through an ESPHome Bluetooth proxy
- Metric and imperial units supported automatically

After a one-time cloud login during setup, everything runs locally over BLE. No cloud dependency at runtime. Your credentials are not stored.

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

For detailed setup, troubleshooting, and entity documentation, see the [full README](https://github.com/gitcak/ha-hai-shower).
