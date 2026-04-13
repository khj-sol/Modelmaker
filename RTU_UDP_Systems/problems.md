# Problems

## 1. Dashboard SSH/SFTP push fails with `getaddrinfo failed`

- Symptom: Dashboard `Push to RTU & Restart` returned `SSH/SFTP error: [Errno 11001] getaddrinfo failed`.
- Cause found: The RTU management IP used by the dashboard had become stale after the Raspberry Pi IP changed.
- Working RTU SSH target confirmed: `192.168.137.156`
- Code fix: dashboard now trims and validates the RTU SSH host before push/restart, and the API now rejects malformed host strings with a clear 400 error before Paramiko runs.
- Note: The dashboard stores RTU SSH IP values in browser local storage, so old values can persist. Invalid saved values are now ignored by the UI.

## 2. RTU server config had an invalid primary host

- File: [config/rtu_config.ini](C:\Users\shson\Desktop\New folder\RTU_UDP_Systems\config\rtu_config.ini)
- Problem value: `primary_host = 13.125.192.15..`
- Issue: Invalid host due to trailing extra dot.
- Current intended dashboard target on direct Ethernet link: `192.168.137.1:13132`
- Important implementation note: current RTU client code effectively uses `primary_host`/`primary_port`; `secondary_host` is not actively used in practice.
- Status: left as an operator-managed config item. Code now prevents similarly malformed SSH target hosts in dashboard push flows, but `rtu_config.ini` itself is not auto-rewritten by the server.

## 3. Huawei inverter AC voltage was displayed 10x too high in dashboard

- Symptom example: `AC R V: 2316.0 V`
- Cause: Huawei AC voltage values in dashboard needed `÷10` display correction.
- Current status: Dashboard display path was adjusted so Huawei AC voltage is shown with `/10`.

## 4. Huawei inverter AC current was over-corrected during dashboard-only fix

- Symptom after temporary UI change: `AC R I: 6.2 A` while power and voltage implied about `61~63 A`
- Cause: Huawei AC current display was incorrectly scaled down in the dashboard.
- Current status: Reverted that extra current correction. Current display now follows the original scaling path again.

## 5. Duplicate inverter cards can remain in dashboard after device config changes

- Symptom: Both `Inverter #1` and `Inverter #2` appeared even though only one inverter is physically installed.
- Checked config: [config/rs485_ch1.ini](C:\Users\shson\Desktop\New folder\RTU_UDP_Systems\config\rs485_ch1.ini) currently has only one active inverter entry.
- Likely cause: Dashboard live registry keeps previously seen `(device_type, device_number)` entries in memory and does not immediately remove stale device slots after configuration changes.
- Code fix: the UDP engine now drops device entries whose per-device timestamp has gone stale relative to the RTU update interval, so old inverter slots disappear automatically after config changes.

## 6. `rs485_ch1.ini` comments and actual values are inconsistent

- Example: Section comments mention one inverter vendor, while actual `protocol`/`model` values point to another.
- Risk: This makes field diagnosis and remote edits error-prone.
- Status: deferred intentionally. This is an operational config file and should be edited only when the live plant mapping is confirmed.

## 7. Device numbering vs. Modbus slave ID caused confusion

- Clarification:
- `slave_id` = actual Modbus address on RS485
- `device_number` = logical device number used in H01 packets and shown in dashboard as `Inverter #n`
- Risk: If two slots reuse old `device_number` expectations, dashboard behavior can look inconsistent even when Modbus communication is correct.
- Status: documentation/diagnosis item only. No code change needed.
