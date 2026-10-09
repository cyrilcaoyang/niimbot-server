import asyncio
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sdl_lab_contract import EquipmentStatus, check_consistency
from test_protocol import png

from niimbot_server.api import create_app
from niimbot_server.driver import SimulatedPrinter
from niimbot_server.models import ClaimInput, PrintRequest, ReconcileRequest, Settings
from niimbot_server.service import Refused, Service


class CountPrinter(SimulatedPrinter):
    def __init__(self):
        self.connected = False
        self.prints = 0
        self.scans = 0
        self.connections = 0

    async def scan(self):
        self.scans += 1
        return await super().scan()

    async def connect(self):
        self.connections += 1
        await super().connect()

    async def print_image(self, *args):
        self.prints += 1
        await super().print_image(*args)


class HangingPrinter(CountPrinter):
    async def print_image(self, image, copies, density, guard, on_running):
        self.prints += 1
        while True:
            guard()
            await asyncio.sleep(0.01)


def claim(service, session="session", owner="operator"):
    return service.acquire(ClaimInput(owner=owner, session_id=session)).claim_token


def request(**kwargs):
    return PrintRequest(job_id=uuid4(), png_base64=png(), **kwargs)


@pytest.fixture
def settings(tmp_path):
    return Settings(journal=tmp_path / "jobs.sqlite3")


async def test_deduplication_is_durable_and_payload_bound(settings):
    driver = CountPrinter()
    service = Service(settings, driver)
    token = claim(service)
    await service.connect(token)
    req = request()
    assert service.submit(req, token).state == "running"
    await service.task
    assert service.submit(req, token).state == "simulated"
    assert driver.prints == 1
    with pytest.raises(Refused) as error:
        service.submit(req.model_copy(update={"copies": 2}), token)
    assert error.value.status == 409
    await service.close()
    restarted = Service(settings, driver)
    token = claim(restarted)
    assert restarted.submit(req, token).state == "simulated"
    assert driver.prints == 1
    await restarted.close()


async def test_claim_loss_preserves_uncertainty_and_requires_human(settings):
    driver = HangingPrinter()
    service = Service(settings, driver)
    token = claim(service)
    await service.connect(token)
    req = request()
    service.submit(req, token)
    await asyncio.sleep(0.03)
    service.deadline = time.monotonic() - 1
    await service.task
    assert service.journal.get(str(req.job_id)).state == "unknown"
    assert "printer.print" not in service.status().allowed_actions
    assert service.status().equipment_status == "unknown"
    await service.close()
    service = Service(settings, driver)
    token = claim(service, "next")
    with pytest.raises(Refused):
        await service.connect(token)
    assert service.submit(req, token).state == "unknown"
    assert driver.prints == 1
    result = service.reconcile(
        ReconcileRequest(
            job_id=req.job_id,
            observed_outcome="not_printed",
            printer_idle_confirmed=True,
            note="Inspected stock",
        ),
        token,
    )
    assert result.state == "reconciled"
    assert service.submit(req, token).state == "reconciled"
    assert driver.prints == 1
    assert service.journal.db.execute("SELECT count(*) FROM events").fetchone()[0] == 3
    await service.close()


async def test_shutdown_does_not_replay(settings):
    driver = HangingPrinter()
    service = Service(settings, driver)
    token = claim(service)
    await service.connect(token)
    req = request()
    service.submit(req, token)
    await asyncio.sleep(0.02)
    await service.close()
    restarted = Service(settings, driver)
    assert restarted.journal.get(str(req.job_id)).state == "unknown"
    assert driver.prints == 1
    await restarted.close()


async def test_hard_crash_recovery(settings):
    service = Service(settings)
    token = claim(service)
    await service.connect(token)
    req = request()
    job = service.submit(req, token)
    await service.task
    service.journal.update(job, state="running", message="Synthetic crash fixture")
    await service.close()
    restarted = Service(settings)
    assert restarted.journal.get(str(req.job_id)).state == "unknown"
    assert restarted.status().required_actions == ["printer.reconcile"]
    await restarted.close()


async def test_concurrent_jobs_and_takeover_are_refused(settings):
    service = Service(settings, HangingPrinter())
    token = claim(service)
    await service.connect(token)
    service.submit(request(), token)
    with pytest.raises(Refused) as error:
        service.submit(request(), token)
    assert error.value.status == 412
    with pytest.raises(Refused):
        claim(service, "other")
    with pytest.raises(Refused):
        claim(service, "session", "different-owner")
    service.release(token)
    with pytest.raises(Refused):
        claim(service, "new")
    await asyncio.sleep(0.02)
    await service.close()


def test_api_reads_never_touch_hardware_and_discovery_is_packaged(settings):
    driver = CountPrinter()
    with TestClient(create_app(settings, driver)) as client:
        for path in [
            "/",
            "/health",
            "/status",
            "/docs",
            "/openapi.json",
            "/agent-docs",
            "/llms.txt",
            "/docs/agent",
            "/plans/actions",
        ]:
            assert client.get(path).status_code == 200, path
        status = EquipmentStatus.model_validate(client.get("/status").json())
        assert status.equipment_status == "dry_run"
        assert not check_consistency(status)
        assert driver.scans == driver.connections == driver.prints == 0
        assert "text/markdown" in client.get("/agent-docs").headers["content-type"]


def test_api_tokens_refusals_and_openapi(settings):
    with TestClient(create_app(settings)) as client:
        for action in ("connect", "disconnect", "scan"):
            assert client.post(f"/control/{action}").status_code == 423
        assert client.post("/control/heartbeat").status_code == 401
        assert client.post("/control/release").status_code == 204
        body = {"owner": "test", "session_id": "test"}
        token = client.post("/control/claim", json=body).json()["claim_token"]
        assert client.post("/control/claim", json=body).json()["claim_token"] == token
        assert (
            client.post("/control/claim", json={**body, "session_id": "another"}).status_code == 409
        )
        headers = {"X-Claim-Token": token}
        assert (
            client.post(
                "/control/print", json=request().model_dump(mode="json"), headers=headers
            ).status_code
            == 412
        )
        assert client.get("/status").json()["last_error"] is None
        assert client.post("/control/connect", headers=headers).status_code == 200
        malformed = request().model_dump(mode="json")
        malformed["png_base64"] = "bad!"
        assert client.post("/control/print", json=malformed, headers=headers).status_code == 400
        malformed["copies"] = 0
        assert client.post("/control/print", json=malformed, headers=headers).status_code == 422
        assert client.post("/control/print", content=b"x" * 1_500_001).status_code == 413
        spec = client.get("/openapi.json").json()
        assert "412" in spec["paths"]["/control/print"]["post"]["responses"]


async def test_live_disconnected_state_is_not_ready(settings):
    service = Service(settings.model_copy(update={"mode": "ble"}), CountPrinter())
    assert service.status().equipment_status == "requires_init"
    token = claim(service)
    await service.connect(token)
    assert service.status().equipment_status == "ready"
    service.driver.connected = False
    assert service.status().equipment_status == "requires_init"
    assert not check_consistency(service.status())
    await service.close()


async def test_only_one_process_may_own_a_journal(settings):
    service = Service(settings)
    with pytest.raises(RuntimeError, match="owns this journal"):
        Service(settings)
    await service.close()
    replacement = Service(settings)
    await replacement.close()


async def test_shutdown_before_worker_starts_is_durable(settings):
    service = Service(settings)
    token = claim(service)
    await service.connect(token)
    req = request()
    service.submit(req, token)
    await service.close()
    replacement = Service(settings)
    assert replacement.journal.get(str(req.job_id)).state == "unknown"
    await replacement.close()


async def test_job_deadline_never_marks_unconfirmed_print_completed(settings):
    service = Service(settings.model_copy(update={"job_timeout_s": 0.04}), HangingPrinter())
    token = claim(service)
    await service.connect(token)
    req = request()
    service.submit(req, token)
    await service.task
    assert service.journal.get(str(req.job_id)).state == "unknown"
    assert service.cycles == 0
    assert service.status().last_error.code == "outcome_unknown"
    await service.close()


async def test_driver_fault_blocks_readiness_and_submission(settings):
    from niimbot_server.protocol import ProtocolError

    driver = CountPrinter()
    service = Service(settings.model_copy(update={"mode": "ble"}), driver)
    token = claim(service)
    await service.connect(token)
    driver.fault = ProtocolError("Unexpected printer fault")
    assert service.status().equipment_status == "error"
    assert "printer.print" not in service.status().allowed_actions
    with pytest.raises(Refused):
        service.submit(request(), token)
    assert driver.prints == 0
    await service.close()
