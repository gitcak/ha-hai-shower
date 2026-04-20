# Hai Shower v0.1.3

## Highlights

- Discovery-first setup with nearby Bluetooth device selection
- One-time Hai cloud login during setup, then local BLE-only runtime
- Live water temperature, flow, battery, and session sensors
- Automatic history sync plus long-term water-usage statistics
- Writable alert thresholds, LED colors, and alert enable toggles

## Operational Notes

- Restart Home Assistant after updating
- Reauthentication is available if the stored Hai device key needs refreshing
- Reconfigure is available if the shower Bluetooth address changes

## Known Limitations

- Firmware version remains unavailable through ESPHome Bluetooth proxy security limits
- App-side alert-setting changes are not yet read back into Home Assistant
- Cloud history import is not implemented
