import httpx
import pytest
from test_service import request

from niimbot_server.api import create_app
from niimbot_server.models import Settings
from niimbot_server.sdk import NiimbotClient, skill_definitions

pytest.importorskip("lab_skills", reason="Optional: run with the lab-skills checkout on PYTHONPATH")

from lab_skills.claims import ClaimManager  # noqa: E402
from lab_skills.registry import EquipmentEntry, Registry  # noqa: E402
from lab_skills.session import LabSession  # noqa: E402


async def test_real_sdk_claims_discovery_and_wrapper(tmp_path):
    app = create_app(Settings(journal=tmp_path / "jobs.sqlite3"))
    entry = EquipmentEntry(
        id="niimbot_d110m",
        name="Niimbot D110_M",
        kind="other",
        adapter="http",
        protocol="1.2",
        base_url="http://test",
    )
    async with app.router.lifespan_context(app):
        async with LabSession(Registry(equipment=[entry])) as session:
            await session._http.aclose()
            session._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
            client = session.get(entry.id)
            assert (await client.status()).protocol_version == "1.2"
            discovery = await client.discover()
            assert not discovery.unavailable
            async with ClaimManager(client, owner="test-operator") as lease:
                printer = NiimbotClient(client, lease)
                assert (await printer.scan())[0]["address"] == "simulation"
                await printer.connect()
                assert "printer.print" in (await client.status()).allowed_actions
                result = await printer.print_label(request())
                await app.state.service.task
                assert app.state.service.journal.get(result["job_id"]).state == "simulated"
                await printer.disconnect()
            assert app.state.service.current_claim() is None
    assert {d.name for d in skill_definitions()} == {
        "printer.connect",
        "printer.print",
        "printer.disconnect",
    }
