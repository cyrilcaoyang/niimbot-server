"""Optional lab-skills integration; callers supply their existing EquipmentClient/claim."""

from .models import PrintRequest


class NiimbotClient:
    def __init__(self, equipment_client, claim):
        self.client = equipment_client
        self.claim = claim

    async def command(self, action, path, body=None):
        self.claim.assert_alive()
        status = await self.client.status()
        if action not in status.allowed_actions:
            raise RuntimeError(f"{action} is unavailable: {status.equipment_status}")
        return await self.client.command(path, body, claim_token=self.claim.token, timeout=40)

    async def scan(self):
        return await self.command("printer.scan", "/control/scan")

    async def label_info(self):
        return await self.command("printer.label_info", "/control/label-info")

    async def connect(self):
        return await self.command("printer.connect", "/control/connect")

    async def print_label(self, request: PrintRequest):
        # Call only under an approved execution context. This wrapper does not authorize plans.
        return await self.command("printer.print", "/control/print", request)

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
            ("print", "Submit one deduplicated label job", PrintRequest, ["ready", "dry_run"]),
            ("disconnect", "Close the printer connection", Empty, ["ready", "dry_run"]),
        ]
    ]
