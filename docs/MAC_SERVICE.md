# Mac service and printing

Use one service for each printer: D110_M on 8091 and B1 on 8092, with separate
configuration files and SQLite journals. Keep the existing journals across upgrades.
The Mac must stay awake, powered and within Bluetooth range. Starting a service
never connects or prints. Physical printing has not been validated on either model.

## Update and start

From the repository directory:

```sh
git pull
uv sync --locked --extra dev
uv pip install --overrides examples/sdk-overrides.txt \
  'lab-skills @ git+https://github.com/AccelerationConsortium/ac-organic-lab@b7e936f72622b5a7875897010cc2125b97561f3b#subdirectory=skills'
```

Use `uv run --no-sync` afterward to preserve the separately installed SDK. In two
separate terminals, leaving each running:

```sh
uv run --no-sync niimbot-server --config printer.local.json --port 8091
uv run --no-sync niimbot-server --config b1.local.json --port 8092
```

These use your existing gitignored target identifiers. `b1.local.json` must have
`profile: b1` and a different journal, such as `local/b1-jobs.sqlite3`. For an entirely
hardware-free rehearsal, create `simulation.local.json` containing
`{"mode":"simulate","profile":"b1","journal":"local/simulation.sqlite3"}` and use it
for both the server and client on a spare port (8093).

Connect explicitly via the diagnostic helper. For B1:

```sh
uv run --no-sync python examples/inspect_printer.py connect --config b1.local.json --url http://127.0.0.1:8092
uv run --no-sync python examples/inspect_printer.py label-info --config b1.local.json --url http://127.0.0.1:8092
```

If a label-info query times out and closes the link, reconnect explicitly before
printing. Close any phone app holding the printer connection.

## Prepare and review a label offline

Measure stock width across the roll and label length along the feed. On irregular,
flag or self-laminating labels, confirm the printable region separately. RFID does
not currently establish usable dimensions or color for either tested printer.

The following **illustrative** 40 × 30 mm B1 stock must be replaced with your actual
measurements. Preparation never contacts a printer:

```sh
uv run --no-sync python examples/print_label.py prepare \
  --config b1.local.json --job local/first-label.json \
  --width-mm 40 --length-mm 30 --text 'TEST' --copies 1
open local/first-label.png
```

Inspect the preview and check its orientation, size, content and margins against the
loaded stock. Text is fitted down to a minimum font size; long lines are refused,
not silently cropped. For custom text, QR codes or layout, supply `--png FILE` instead
of `--text`; its pixel size must exactly match the measured canvas. No automatic
rotation or scaling is performed. Canvas dimensions are floor(mm × 203 / 25.4), with
width capped at the physical head: 96 dots for D110_M, 384 for B1. The medium can be
wider than the printable head. Preparation shows a SHA256 approval hash.

## Submit exactly the reviewed job

This command physically prints when the service is in BLE mode. Run it only after
review and within your authorized operating context. The hash is an exact-payload
confirmation, **not** a replacement for SDL2's validated plans/BitacoraDB records.
For lab runs, call `NiimbotClient.print_label()` inside the approved workflow and
retain its claim until completion; the helper is a submission example, not a lab
plan approver or the device-run panel described in the canonical contract.

```sh
uv run --no-sync python examples/print_label.py send \
  --config b1.local.json --url http://127.0.0.1:8092 \
  --job local/first-label.json --approve PASTE_THE_REVIEWED_SHA256
```

The helper verifies the service identity, profile and simulation mode, submits once,
and polls persisted job state through the SDK while maintaining the claim. `202` /
`running` means accepted, not printed. `completed` means firmware reported the page
count; inspect the physical result. `simulated` is never a real print. Start with
one copy for firmware validation. D110_M uses the same commands with
`--config printer.local.json --url http://127.0.0.1:8091` and measured D110_M stock.

Do not close the client before it finishes; losing the claim can interrupt the job.
After a timeout, interruption or unknown result, preserve the prepared JSON and its
job ID. Do not create a new ID or automatically replay. Inspect the recorded result:

```sh
uv run --no-sync python examples/inspect_printer.py job \
  --config b1.local.json --url http://127.0.0.1:8092 --job-id SAVED_JOB_UUID
```

An unknown job requires an operator observation and reconciliation through the
service's claimed reconciliation operation. Clearing a journal or generating a new
job is not recovery. The code never guesses whether a missing response meant success.

## Optional background service at login

First validate foreground operation, including macOS Bluetooth permission. Generate
one LaunchAgent per printer; this command only writes a gitignored local plist:

```sh
uv run --no-sync python examples/make_launchagent.py --config b1.local.json --port 8092 --name b1
```

Stop the foreground B1 server before loading the background instance, then:

```sh
mkdir -p ~/Library/LaunchAgents
cp local/org.niimbot-server.b1.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/org.niimbot-server.b1.plist
```

Repeat with `--config printer.local.json --port 8091 --name d110m` and the generated
`org.niimbot-server.d110m.plist`. This runs at user login, not before login. Logs go to
`local/b1.stdout.log` and `local/b1.stderr.log`. Background Bluetooth permission may
need approval separately from the terminal; check logs and perform an explicit SDK
connection test. A restart never reconnects or reprints. To stop/unload B1:

```sh
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/org.niimbot-server.b1.plist
```

Keep the checkout and virtual environment at the same absolute paths; regenerate the
plist after moving either. Logs are local and not automatically rotated. Services
bind to loopback. Remote clients should use your reviewed SSH/Tailnet access path;
do not expose the unauthenticated claim endpoints on a public interface. The lab
registry and edge access configuration are a separate deployment step.
