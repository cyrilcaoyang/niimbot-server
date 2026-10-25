"""Optional lab-skills integration; callers supply their existing EquipmentClient/claim."""

from uuid import UUID

from .models import Job, PowerSetting, PrintRequest


class NiimbotClient:
    def __init__(self, equipment_client, claim):
        self.client = equipment_client
        self.claim = claim

    async def command(self, action, path, body=None, timeout=40):
        self.claim.assert_alive()
        status = await self.client.status()
        if action not in status.allowed_actions:
            raise RuntimeError(f"{action} is unavailable: {status.equipment_status}")
        return await self.client.command(path, body, claim_token=self.claim.token, timeout=timeout)

    async def scan(self):
        return await self.command("printer.scan", "/control/scan")

    async def label_info(self):
        return await self.command("printer.label_info", "/control/label-info")

    async def power_info(self):
        return await self.command("printer.power_info", "/control/power-info", timeout=220)

    async def set_power(self, request: PowerSetting):
        return await self.command("printer.set_power", "/control/set-power", request, timeout=220)

    async def connect(self):
        return await self.command("printer.connect", "/control/connect")

    async def print_label(self, request: PrintRequest):
        # Call only under an approved execution context. This wrapper does not authorize plans.
        return await self.command("printer.print", "/control/print", request)

    async def job(self, job_id):
        """Read persisted job state through the SDK; never resubmit a print."""
        job_id = UUID(str(job_id))
        return await self.client._fetch_envelope(f"/jobs/{job_id}", Job, label="job")

    async def disconnect(self):
        return await self.command("printer.disconnect", "/control/disconnect")


def skill_definitions():
    """Definitions for reviewed registration; importing does not modify the lab catalog."""
    from lab_skills.skill_catalog import SkillDef
    from pydantic import BaseModel

    class Empty(BaseModel):
        pass

    return [
        SkillDef(
            name=f"printer.{name}",
            kind="other",
            description=description,
            endpoint=f"/control/{name.replace('_', '-')}",
            args_schema=args,
            requires_states=states,
        )
        for name, description, args, states in [
            (
                "connect",
                "Connect the configured Niimbot printer",
                Empty,
                ["requires_init", "dry_run"],
            ),
            ("label_info", "Read RFID and paper information", Empty, ["ready", "dry_run"]),
            ("power_info", "Read B1 power configuration", Empty, ["ready", "dry_run"]),
            (
                "set_power",
                "Change B1 shutdown index with readback",
                PowerSetting,
                ["ready", "dry_run"],
            ),
            ("print", "Submit one deduplicated label job", PrintRequest, ["ready", "dry_run"]),
            ("disconnect", "Close the printer connection", Empty, ["ready", "dry_run"]),
        ]
    ]
