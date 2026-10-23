# Windows service

Run the printer service in native Windows Python 3.12+ so Bleak uses Windows BLE.
WSL Bluetooth visibility is not evidence of a usable Windows radio. Check the physical
adapter's PnP status, Bluetooth LE enumerator and Bluetooth service first.

Use a dedicated checkout and `uv sync --locked --extra dev --python 3.13` to create
an isolated environment. Run `.venv\Scripts\python.exe -m pytest -q` before deployment.
The journal uses a nonblocking byte-range lock on Windows and flock on Unix. Do not
share a journal between processes; preserve it across updates.

Create a gitignored `b1.local.json` with mode `ble`, profile `b1`, an exact advertised
printer name (Mac CoreBluetooth UUIDs are not portable), and a dedicated journal.
Release the Mac or phone connection before connecting Windows to the same printer.

Use a dedicated NSSM service, executable `.venv\Scripts\python.exe`, arguments
`-m niimbot_server.cli --config b1.local.json --port 8092`, and the checkout as the
working directory. Put logs in a local operational directory and enable rotation.
This service must not share or alter instrument Python environments or robot services.
The default loopback bind is suitable for SSH forwarding. Any Tailnet bind must use
a machine-local address and scoped access rules; claims are not authentication.

Validate BLE discovery and connection under the actual Windows service account,
not just an interactive desktop session. The SDK diagnostics helper remains the
client boundary. Installing/running the gateway does not require pairing, a radio
reset or any robot service restart. Never change those to hide a connection failure.

Startup never scans, connects, prints or replays jobs. A sleeping printer may need
an operator to wake it and an explicit SDK reconnect. Migration to a fresh host
starts a new operational journal: do not resubmit previously used job files from
another host, because deduplication belongs to the original journal. Archive the
old journal and use newly reviewed jobs for intentional new prints only.
