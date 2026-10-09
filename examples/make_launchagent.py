"""Generate a per-user macOS LaunchAgent file; does not install or start it."""

import argparse
import plistlib
import re
import sys
from pathlib import Path

from niimbot_server.models import Settings

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--config", type=Path, required=True)
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--name", required=True, help="Unique simple name, e.g. d110m or b1")
args = parser.parse_args()
if not re.fullmatch(r"[a-z0-9-]+", args.name) or not 1024 <= args.port <= 65535:
    parser.error("Use a lowercase alphanumeric name and a port from 1024 to 65535")
config = args.config.resolve(strict=True)
Settings.model_validate_json(config.read_text())
root = Path.cwd().resolve()
local = root / "local"
local.mkdir(exist_ok=True)
label = "org.niimbot-server." + args.name
output = local / (label + ".plist")
with output.open("xb") as stream:
    plistlib.dump(
        {
            "Label": label,
            "ProgramArguments": [
                sys.executable,
                "-m",
                "niimbot_server.cli",
                "--config",
                str(config),
                "--port",
                str(args.port),
            ],
            "WorkingDirectory": str(root),
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "StandardOutPath": str(local / (args.name + ".stdout.log")),
            "StandardErrorPath": str(local / (args.name + ".stderr.log")),
        },
        stream,
    )
print(f"Generated {output}; nothing installed or started")
