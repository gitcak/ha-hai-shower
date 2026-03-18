# Hai Shower Bluetooth Troubleshooting

This guide covers the current known failure modes when using the Hai Shower
integration through Home Assistant and an ESPHome Bluetooth proxy.

## Start With the Basics

Check these first:

- The ESPHome Bluetooth proxy is online in Home Assistant
- The shower is powered on and in BLE range of the proxy
- The phone app is not actively fighting for the same BLE session
- Home Assistant can still see the shower as a connectable Bluetooth device

If the shower is simply out of range, the integration should degrade cleanly
instead of publishing bogus sensor values.

## Symptom: Setup or Runtime Says the Shower Is Not Visible

What it usually means:

- The shower is out of range
- The proxy is online but too far away for reliable BLE discovery
- The shower is not actively advertising at that moment

What to check:

1. Confirm the ESPHome proxy is online and exposed as a Bluetooth source in
   Home Assistant.
2. Move the proxy or shower closer for validation.
3. Retry while the shower is awake/active instead of fully idle if discovery is
   intermittent.
4. Keep the phone away from active Bluetooth debugging during the test window.

Expected behavior:

- The integration should retry cleanly
- Sensors should remain `unavailable` / `null`, not stale or nonsense values

## Symptom: ESPHome Proxy `status=133`

What it means:

- The BLE proxy connection dropped or the GATT session became invalid
- This is a transport/proxy reliability issue, not a decode or recorder issue

What the integration already does:

- Uses `bleak-retry-connector`
- Avoids holding idle connections open between normal polls
- Resets post-shower runtime monitoring after automatic sync
- Aborts refreshes cleanly when the client disconnects mid-cycle

What to try:

1. Wait for the next scheduled poll and see if the integration recovers on its
   own.
2. Avoid repeated manual actions immediately after an automatic history sync.
3. If the issue is persistent, restart the ESPHome proxy and then reload the
   integration.
4. Re-test with the shower active to separate idle visibility issues from
   active-session proxy churn.

Current status:

- Post-shower recovery is validated on the clean path
- Extended multi-shower proxy stability is still under validation

## Symptom: `ble_disconnected_during_refresh`

What it means:

- The proxy dropped the connection during a poll cycle
- The integration detected the mid-refresh disconnect and stopped the read
  instead of continuing with a stale GATT cache

What to do:

- Treat it as a transient transport failure first
- Check whether the next refresh recovers cleanly before assuming the device is
  wedged
- If it repeats frequently, focus on proxy placement, proxy health, and nearby
  BLE contention

## Symptom: Manual History Sync Fails

Known previous failure:

- `BluetoothGATTWriteResponse: Insufficient authorization (8)`

Current behavior:

- The integration now forces a fresh reconnect before writing the history-sync
  trigger characteristic
- That fixed the stale-session authorization failure in live validation

If manual sync still fails:

1. Make sure the shower is actually visible to the proxy
2. Retry once after the next idle poll or after the proxy reconnects
3. Check integration diagnostics for:
   - `last_history_sync_trigger`
   - `last_history_sync_result`
   - `last_history_sync_error`
   - `last_history_sync_records`

## Symptom: Firmware Version Is Unavailable

This is expected today.

Why:

- The firmware characteristic read appears to require a BLE security level
  that the ESPHome proxy path does not currently satisfy

Current strategy:

- The firmware sensor is disabled by default
- If manually enabled, it may remain unavailable
- This is treated as a known limitation for v0.1, not a core runtime failure

## Symptom: Alert Settings Work in HA but Don’t Match App-Side Changes

Current behavior:

- Values written from Home Assistant persist across restart
- Changes made directly in the Hai app are not yet read back into Home
  Assistant automatically

Interpretation:

- This is a feature gap, not a BLE failure
- A later readback-on-connect enhancement is planned for app-side drift

## Where To Look

Useful observability points:

- Home Assistant logs for BLE/proxy errors
- Integration diagnostics export
- `History sync` diagnostic button

The diagnostics payload includes the last history-sync timestamps, trigger
source, result, error text, record count, and recent session summaries.
