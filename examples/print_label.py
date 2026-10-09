"""Prepare a preview offline; explicitly submit an approved label through lab-skills."""

import argparse
import asyncio
import base64
import json
from pathlib import Path

from niimbot_server.models import Settings
from niimbot_server.printing import PreparedLabel, prepare


async def send(args, settings, label):
    from lab_skills.claims import ClaimManager
    from lab_skills.registry import EquipmentEntry, Registry
    from lab_skills.session import LabSession

    from niimbot_server.sdk import NiimbotClient

    if args.approve != label.digest():
        raise ValueError("Approval hash differs from this exact prepared label; review it again")
    label.validate_for(settings)
    entry = EquipmentEntry(
        id=settings.equipment_id,
        name="Niimbot " + settings.model_name,
        kind="other",
        adapter="http",
        protocol="1.2",
        base_url=args.url,
    )
    async with LabSession(Registry(equipment=[entry])) as session:
        client = session.get(entry.id)
        status = await client.status()
        if (
            status.equipment_id != label.equipment_id
            or status.details.get("profile") != label.profile
        ):
            raise ValueError("Service identity/profile differs from the reviewed label")
        if status.details.get("simulated") != (settings.mode == "simulate"):
            raise ValueError("Service mode differs from local configuration")
        async with ClaimManager(client, owner=args.owner) as claim:
            printer = NiimbotClient(client, claim)
            print(f"Job {label.request.job_id}: submitting once", flush=True)
            # Never connect, regenerate IDs, or retry automatically.
            result = await printer.print_label(label.request)
            print(json.dumps(result, indent=2), flush=True)
            async with asyncio.timeout(settings.job_timeout_s + 15):
                while result["state"] == "running":
                    claim.assert_alive()
                    await asyncio.sleep(0.5)
                    result = (await printer.job(label.request.job_id)).model_dump(mode="json")
            print(json.dumps(result, indent=2))
            if result["state"] not in ("completed", "simulated"):
                raise RuntimeError("Outcome needs operator review; do not submit a new job")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "send"])
    parser.add_argument("--config", type=Path, default=Path("printer.local.json"))
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--width-mm", type=float)
    parser.add_argument("--length-mm", type=float)
    parser.add_argument("--text")
    parser.add_argument("--png", type=Path)
    parser.add_argument("--copies", type=int, default=1)
    parser.add_argument("--density", type=int, default=3)
    parser.add_argument("--approve", help="SHA256 displayed by prepare after human preview review")
    parser.add_argument("--owner", default="printer-operator")
    args = parser.parse_args()
    try:
        settings = Settings.model_validate_json(args.config.read_text())
        if args.action == "prepare":
            if args.width_mm is None or args.length_mm is None:
                raise ValueError("Supply measured --width-mm and --length-mm")
            label = prepare(
                settings,
                args.width_mm,
                args.length_mm,
                text=args.text,
                png=args.png.read_bytes() if args.png else None,
                copies=args.copies,
                density=args.density,
            )
            args.job.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation preserves the original job ID and reviewed payload.
            with args.job.open("x") as stream:
                stream.write(label.model_dump_json(indent=2) + "\n")
            preview = args.job.with_suffix(".png")
            with preview.open("xb") as stream:
                stream.write(base64.b64decode(label.request.png_base64))
            print(f"Preview: {preview}\nJob ID: {label.request.job_id}")
            print(f"Profile: {label.profile}; copies: {label.request.copies}")
            print(f"Approval SHA256: {label.digest()}\nPrepared only; nothing sent")
        else:
            label = PreparedLabel.model_validate_json(args.job.read_text())
            asyncio.run(send(args, settings, label))
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(
            1,
            f"Stopped: {exc}\nIf submission began, preserve the job file and inspect its "
            "status; do not create a replacement job or retry automatically.\n",
        )


if __name__ == "__main__":
    main()
