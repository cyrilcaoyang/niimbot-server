# niimbot-server

Experimental **Niimbot D110_M** device service, derived from NiimPrintX, for macOS
testing and eventual SDL2 integration. Python 3.12+, FastAPI, Bleak/CoreBluetooth
on macOS or BlueZ on Linux. GPL-3.0-only; provenance in [THIRD_PARTY.md](THIRD_PARTY.md).

**No physical printer has been tested with this implementation.** Mock tests verify
software behavior, not firmware compatibility, radio range, or print quality.
The normal D110 and D110_M have different protocols. This targets D110_M only.

## Start on your MacBook

Use a local checkout of this repository and an existing `uv` installation:

```sh
uv sync --locked --extra dev
uv run niimbot-server
```

Open <http://127.0.0.1:8091/docs> for the API or <http://127.0.0.1:8091/status>
for state. **The default is simulation.** Starting it never scans or connects.
There is no ImageMagick, Poetry, GUI toolkit, root access, or system-service installation.
Do not run multiple workers or launch multiple instances against the same printer.

For later Mac hardware testing, create a **gitignored** `printer.local.json`:

```json
{
  "mode": "ble",
  "target": "D110_M-REPLACE_WITH_EXACT_NAME",
  "profile": "d110_m",
  "journal": "local/jobs.sqlite3"
}
```

```sh
uv run niimbot-server --config printer.local.json
```

This still does **not** connect or print. Under an authorized test context, the SDK's
claimed scan/connect controls can enumerate and connect. Grant Bluetooth access to
the terminal/Python host in macOS System Settings → Privacy & Security → Bluetooth
if prompted. macOS identifiers are CoreBluetooth UUIDs; an exact advertised name also
works, and ambiguous matches are refused. Close the phone's Niimbot app before testing
so it does not retain the printer connection. No OS pairing is required by this code.

`d110_m` uses the NiimPrintX fork's 7-byte print-start and 6-byte dimensions protocol.
`d110_m_v4` is an **explicit experimental alternative** for newer firmware using 9/13-byte
messages. No automatic fallback: choose from known firmware evidence before a test,
never retry an uncertain job under another profile. Exact UUID selection for the
notify/write characteristic can be set with `characteristic` if discovery is ambiguous.

## What is implemented

- Side-effect-free `/`, `/health`, `/status`, `/jobs/{job_id}`; STATUS_SPEC **1.2**.
- Hard-enforced claim/heartbeat/release; claimed scan/connect/disconnect/print controls.
- Strict PNG validation: maximum 96-dot head width, 2400 rows, 1 MB; 1–20 copies;
  density 1–3. Render to the final orientation and actual label dimensions yourself.
  No automatic resizing/rotation; transparent backgrounds become white.
- Persistent job UUID deduplication, bounded command/job timeouts, framed/checksummed
  notifications, expected-response matching, explicit Bleak 1.x connection semantics.
- Interrupted jobs block subsequent printing until a human reconciles the physical
  outcome. Restart does not replay jobs. A matching UUID never prints twice.
- Documentation endpoints and an optional lab-skills wrapper/skill definitions.

Primary operation: physically printing/feeding labels. A print request being accepted
does not prove movement or completion. Transmission reports unknown activity until
observed progress; completion requires a reported page count, not a sleep timer.
Simulation reports `dry_run` when idle and tags every job as simulated.
`cycles_total` counts confirmed copies since process start (synthetic in simulation).
See [operating guide](src/niimbot_server/agent-guide.md) for state, claim, refusal,
reconciliation and error semantics. It is also packaged and served at `/agent-docs`.

## SDL2 boundary

The [canonical lab contract](https://github.com/AccelerationConsortium/ac-organic-lab/blob/main/docs/AGENTIC_LAB_DESIGN.md)
and [STATUS_SPEC](https://github.com/AccelerationConsortium/ac-organic-lab/blob/main/docs/STATUS_SPEC.md)
apply. All equipment use by clients goes through **lab-skills**, under the applicable
human-approved execution context. A claim is a lock, not authorization to print.
This service does not yet provide the single-device plan approval/execution panel.
Swagger documents transport; it is not an alternate execution/approval path.

`niimbot_server.sdk.NiimbotClient` accepts a lab-skills `EquipmentClient` obtained from
an active `LabSession` and a `ClaimManager`; it checks live actions and passes the claim
token through `EquipmentClient.command()`. `skill_definitions()` supplies definitions
for **reviewed registration**, without monkey-patching the SDK's generic `other` kind.
The production lab catalog, registry, host configuration and services are untouched.
The server has no dependency on the whole lab monorepo; the wrapper uses a caller's
existing lab-skills installation. SDK interoperability is tested separately when available.

Use `equipment_kind: other` until a printer kind is added through the canonical spec.
See [example registry](examples/equipment.example.yaml) for a disabled entry. Configure
the real host address only in a machine-local file. Network use needs the existing
Tailnet/edge policy; the default is loopback and claims are not authentication.

The SQLite journal is **operational recovery state**, not the scientific record.
Authorized workflows must append real print outcomes/sample identity to BitacoraDB.
This first implementation does not create or modify BitacoraDB records. Label images,
job state, device names, addresses and local settings must not be committed.
Preserve the journal across upgrades: deleting it discards deduplication and uncertainty.

## Development

```sh
uv sync --locked --extra dev
uv run pytest
uv run ruff check .
```

Tests use simulated/fake transports only. No scan, BLE connection, print, or live lab
endpoint is needed. CI runs the same checks on Linux and macOS. Runtime artifacts stay
in ignored `local/`. Shutdown disconnects and records uncertainty; it cannot promise
to stop labels already buffered by the physical printer.
