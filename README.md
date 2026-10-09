# niimbot-server

Experimental **Niimbot D110_M and B1** device service, derived from NiimPrintX, for macOS
testing and eventual SDL2 integration. Python 3.12+, FastAPI, Bleak/CoreBluetooth
on macOS or BlueZ on Linux. GPL-3.0-only; provenance in [THIRD_PARTY.md](THIRD_PARTY.md).

**D110_M discovery and connection have been reported working on macOS.**
Label queries and printing still need firmware validation; B1 has only fake-transport
tests. The normal D110, B1 Pro and B1 SE are not supported.

See [Mac service and print workflow](docs/MAC_SERVICE.md) for measured-size previews,
explicit job submission, job monitoring and optional background services.

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

## Read the loaded label stock (no printing)

Install the optional SDK with this override: the pinned lab-skills revision requests
contract v1.2.0, conflicting with this service's v1.2.1.

```sh
uv pip install --overrides examples/sdk-overrides.txt \
  'lab-skills @ git+https://github.com/AccelerationConsortium/ac-organic-lab@b7e936f72622b5a7875897010cc2125b97561f3b#subdirectory=skills'
```

Use `uv run --no-sync` afterward to retain the separately installed SDK. Restart the
updated server, then in a second terminal:

```sh
uv run --no-sync python examples/inspect_printer.py connect
uv run --no-sync python examples/inspect_printer.py label-info
```

The helper reads `printer.local.json` and uses lab-skills and a claim. It never prints
or feeds. `NiimbotClient.label_info()` explicitly queries RFID and paper information.
RFID may report a stock barcode, serial and counters; counter units remain unknown,
so `remaining` is not asserted to be a label count. A recognized paper reply provides
printer-reported dimensions. Unknown formats and unsupported commands are identified
explicitly. No tag does not prove there is no paper. Stock names, colors and dimensions
are not guessed from barcodes or SKU text.

`/status` only returns timestamped `details.label_info_snapshot`; polling never queries
the printer. Manual roll changes can make it stale. Query again after changing rolls;
a new connection or accepted print clears it. Optional paper query timeout preserves
RFID data and closes the link; reconnect explicitly. RFID failures return
`label_query_failed` and close the link. There are no automatic retries.

## B1 profile

Experimental `b1` selects a 384-dot head at 203 dpi, density 1–5 and the B1 print sequence.
Copy `examples/b1.example.json` to `b1.local.json`, set the exact advertised B1 name or
Bluetooth identifier, and give each printer its own process, journal and port:

```sh
uv run --no-sync niimbot-server --config b1.local.json --port 8092
uv run --no-sync python examples/inspect_printer.py scan --config b1.local.json --url http://127.0.0.1:8092
```

Replace `scan` with `connect` or `label-info` for subsequent diagnostics. The default
equipment ID is `niimbot_b1`. No physical B1 has been validated; this does not imply
B1 Pro or B1 SE compatibility.

## What is implemented

- Side-effect-free `/`, `/health`, `/status`, `/jobs/{job_id}`; STATUS_SPEC **1.2**.
- Hard-enforced claim/heartbeat/release; claimed scan/connect/disconnect/label-info/print controls.
- Strict PNG validation: maximum 96-dot D110_M or 384-dot B1 head width, 2400 rows, 1 MB;
  1–20 copies; density 1–3 for D110_M, 1–5 for B1. Render to the final orientation and actual label dimensions yourself.
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
endpoint is needed. The GitHub Actions template in `examples/github-actions-tests.yml`
runs the same checks on Linux and macOS once copied to `.github/workflows/tests.yml`
by an account with workflow permission. Automatic CI is not active yet. Runtime artifacts stay
in ignored `local/`. Shutdown disconnects and records uncertainty; it cannot promise
to stop labels already buffered by the physical printer.
