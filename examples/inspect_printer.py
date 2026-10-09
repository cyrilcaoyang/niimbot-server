"""Claimed SDK diagnostics only: no printing, feeding, pairing or radio changes."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from lab_skills.claims import ClaimManager
from lab_skills.registry import EquipmentEntry, Registry
from lab_skills.session import LabSession

from niimbot_server.models import Settings
from niimbot_server.sdk import NiimbotClient


async def run(args):
    settings = Settings.model_validate(json.loads(args.config.read_text()))
    entry = EquipmentEntry(
        id=settings.equipment_id,
        name="Niimbot " + settings.model_name,
        kind="other",
        adapter="http",
        protocol="1.2",
        base_url=args.url,
    )

    async def show_error(response):
        if response.status_code >= 400:
            await response.aread()
            print("SERVER ERROR:", response.text, flush=True)

    async with LabSession(Registry(equipment=[entry])) as session:
        # Keep diagnostics visible when EquipmentClient reduces 503 responses to a status code.
        session._http.event_hooks["response"].append(show_error)
        client = session.get(entry.id)
        if args.action == "status":
            print((await client.status()).model_dump_json(indent=2))
            return
        if args.action == "job":
            if not args.job_id:
                raise ValueError("Supply --job-id for the persisted job")
            print((await NiimbotClient(client, None).job(args.job_id)).model_dump_json(indent=2))
            return
        async with ClaimManager(client, owner="printer-operator") as claim:
            printer = NiimbotClient(client, claim)
            method = {
                "scan": printer.scan,
                "connect": printer.connect,
                "label-info": printer.label_info,
            }[args.action]
            print(json.dumps(await method(), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["scan", "connect", "label-info", "status", "job"])
    parser.add_argument("--config", type=Path, default=Path("printer.local.json"))
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    parser.add_argument("--job-id", type=UUID)
    try:
        asyncio.run(run(parser.parse_args()))
    except Exception as exc:
        parser.exit(1, f"Stopped: {exc}\nCheck the server terminal and the configured URL.\n")
