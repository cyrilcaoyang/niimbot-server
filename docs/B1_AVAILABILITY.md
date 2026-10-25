# B1 availability and auto-shutdown

## Next lab visit

1. Physically wake the B1 and check its charge and indicator. A disconnected gateway
   alone does not establish whether the printer is powered off, sleeping or out of range.
2. Release any gateway Bluetooth connection before connecting with the official
   NIIMBOT app. Do not change the robot service or Windows radio configuration.
3. In the printer/device settings, look for **Auto power-off / 自动关机 / 关机时间**.
   If **Never / 永不关机** is offered for this specific unit, select it. Record the
   previous setting and firmware version outside git. Do not update firmware merely
   to find this option.
4. Disconnect the phone app, then explicitly reconnect the gateway through lab-skills.
   This is a connection check, not a print request.
5. Check availability after more than an hour without printing, and again later.
   A successful setting change is not proof of continuous availability. Keep any
   observations in the operational record, not in this repository.
6. At a later attended power cycle, check whether the setting persists. If Never is
   absent, capture the available options for follow-up rather than guessing a raw value.

Do not resend old print jobs to test availability. Use status and an explicit claimed
read-only diagnostic when needed. Preserve journals and unresolved print outcomes.

## Research and confidence

English and Chinese sources were reviewed on 2026-10-23:

- An English [B1 product listing](https://www.newegg.com/niimbot-b1-1-bluetooth-monochrome-printing/p/36J-00WU-002W8?Item=9SIC7PEM346452)
  advertises 15/30/60-minute and Never options. This is product-listing evidence,
  not verification of every firmware version.
- The manufacturer's Chinese [精臣云打印 app release notes](https://apps.apple.com/cn/app/id6755411172?mt=12)
  mention adding an automatic-shutdown setting (自动关机设置项). They do not establish
  B1-specific firmware support or the precise menu path in every app version.
- [NIIMBOT community protocol documentation](https://printers.niim.blue/interfacing/proto/)
  describes reading AutoShutdownTime using PrinterInfo key 7 and writing it using
  SetAutoShutdownTime (0x27). Values are indices, not minutes; value 4 can mean
  60 minutes or Never depending on model. Do not blindly write 4.
- [niimbluelib's client implementation](https://github.com/MultiMote/niimbluelib/blob/main/src/client/abstract_client.ts)
  includes periodic heartbeat queries. This establishes a status-polling mechanism,
  not proof that heartbeat traffic prevents B1 power-off.
- The [B1 manual's charging section](https://www.manualslib.com/manual/4380568/Niimbot-Niimbot-B1.html?page=2)
  does not promise that USB power disables shutdown, and recommends against printing
  while charging for battery-life reasons. A successful print while charging does not
  establish an unattended, always-on operating mode.

Preferred approach: use the supported app setting if available, then validate idle
availability on the actual unit. Continuous availability is not yet guaranteed.

## Current implementation boundary

`niimbot-server` has explicit scan/connect/disconnect and RFID/paper queries. It now exposes explicit claimed power reads and shutdown-index writes (B1 only). It does
not periodically send printer heartbeats,
automatically reconnect, or wake a powered-off printer. `/status` is cached and
side-effect-free. Claim heartbeats renew the service lock; they are not Bluetooth
heartbeats and do not keep the printer awake.

Any future keep-alive implementation must serialize device I/O with print operations,
preserve claim/interlock semantics and keep status reads side-effect-free. Connection
recovery must never replay a physical print. Enabling Never would address power-off
only; radio range, PC power management and link failures remain separate concerns.

## Claimed service controls

`NiimbotClient.power_info()` / `POST /control/power-info` reads model ID, firmware
bytes and the current shutdown index. `NiimbotClient.set_power(PowerSetting(...))` /
`POST /control/set-power` requires `shutdown_index` (1–4) and
`expected_current_index` (1–4). The service requires a claim, idle connected state,
no unreconciled jobs and actual firmware model ID 4096 before changing B1 settings.
It reads before writing, refuses a changed baseline, sends at most one write, and
verifies the model/firmware/setting readback. The journal stores configuration intent
and outcome. No changes are applied automatically on startup or reconnect.

A matching readback confirms the raw setting, not that the firmware interprets it
as Never. Errors close the link and require a fresh read before any new attempt.
The timestamped `details.power_info_snapshot` is cached; `/status` never polls BLE.
Use `examples/inspect_printer.py power-info` with the appropriate config and URL
for an explicit read. These controls never print or feed.
