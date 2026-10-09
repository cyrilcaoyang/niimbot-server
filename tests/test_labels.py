import struct
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_protocol import png
from test_service import CountPrinter, claim, request

from niimbot_server.api import create_app
from niimbot_server.driver import BlePrinter
from niimbot_server.models import LabelInfo, PaperInfo, Settings
from niimbot_server.protocol import ProtocolError, parse_paper, parse_rfid
from niimbot_server.service import Refused, Service


def rfid_payload(total=200, used=12):
    return (
        bytes.fromhex("0011223344556677") + b"\x04TEST\x03XYZ" + struct.pack(">HHB", total, used, 1)
    )


def test_rfid_zero_leading_uid_and_extensions():
    result = parse_rfid(rfid_payload() + b"\x00\xc8" + b"12345678" + b"\xff")
    assert result.tag_present is True
    assert result.barcode == "TEST"
    assert result.serial == "XYZ"
    assert result.remaining == 188
    assert result.counter_unit == "unknown"
    assert result.capacity == 200
    assert result.secondary_uid == b"12345678".hex()
    assert result.extra_hex == "ff"


def test_rfid_absent_is_not_an_empty_printer():
    result = parse_rfid(b"\x00")
    assert result.status == "no_tag" and result.total is None
    assert "does not prove" in result.message
    assert parse_rfid(rfid_payload(10, 11)).remaining is None


@pytest.mark.parametrize(
    "payload",
    [b"", b"\x01", bytes(8) + b"\xff", rfid_payload()[:-1], bytes(8) + b"\x01\xff\x00" + bytes(5)],
)
def test_bad_rfid_is_not_fabricated_stock(payload):
    with pytest.raises(ProtocolError):
        parse_rfid(payload)


def test_paper_dimensions_only_from_known_valid_layout():
    data = struct.pack(">HHBHHHHBHH", 16, 256, 1, 20, 320, 96, 120, 0, 0, 0)
    result = parse_paper(data)
    assert (result.width_mm, result.height_mm, result.gap_mm) == (12, 30, 2)
    assert result.height_pixels == 240
    assert parse_paper(b"\x00" * 4).status == "unknown"
    assert parse_paper(b"\x00" * 18).width_mm is None


class LabelPrinter(CountPrinter):
    reads = 0

    async def label_info(self, guard):
        guard()
        self.reads += 1
        return LabelInfo(
            queried_at=datetime.now(UTC),
            simulated=True,
            rfid=parse_rfid(rfid_payload()),
            paper=PaperInfo(),
        )


async def test_query_requires_claim_connection_and_idle(tmp_path):
    driver = LabelPrinter()
    service = Service(Settings(journal=tmp_path / "jobs.sqlite3"), driver)
    with pytest.raises(Refused) as error:
        await service.label_info(None)
    assert error.value.status == 423
    token = claim(service)
    with pytest.raises(Refused) as error:
        await service.label_info(token)
    assert error.value.status == 412
    await service.connect(token)
    assert "printer.label_info" in service.status().allowed_actions
    assert (await service.label_info(token)).rfid.remaining == 188
    for _ in range(3):
        assert service.status().details["label_info_snapshot"].rfid.barcode == "TEST"
    assert driver.reads == 1 and driver.prints == 0
    service.submit(request(), token)
    with pytest.raises(Refused):
        await service.label_info(token)
    assert service.label_snapshot is None
    await service.task
    await service.close()


async def test_query_timeout_clears_snapshot_and_drops_link(tmp_path):
    class BadPrinter(LabelPrinter):
        async def label_info(self, guard):
            raise TimeoutError("No RFID reply")

    service = Service(Settings(journal=tmp_path / "jobs.sqlite3"), BadPrinter())
    token = claim(service)
    await service.connect(token)
    with pytest.raises(Refused) as error:
        await service.label_info(token)
    assert error.value.code == "label_query_failed"
    assert service.label_snapshot is None
    assert not service.driver.connected
    await service.close()


def test_api_label_info_is_typed_and_status_reads_cache(tmp_path):
    driver = LabelPrinter()
    with TestClient(create_app(Settings(journal=tmp_path / "jobs.sqlite3"), driver)) as client:
        token = client.post("/control/claim", json={"owner": "test", "session_id": "test"}).json()[
            "claim_token"
        ]
        headers = {"X-Claim-Token": token}
        client.post("/control/connect", headers=headers)
        result = client.post("/control/label-info", headers=headers)
        assert result.status_code == 200
        assert result.json()["rfid"]["remaining"] == 188
        assert client.get("/status").json()["details"]["label_info_snapshot"]["simulated"]
        assert driver.reads == 1
        assert "LabelInfo" in client.get("/openapi.json").json()["components"]["schemas"]


async def test_optional_paper_timeout_keeps_rfid_but_disconnects():
    printer = BlePrinter(Settings())
    disconnected = []

    async def command(code, data, expected, guard):
        if code == 0x1A:
            return rfid_payload()
        raise TimeoutError()

    async def disconnect():
        disconnected.append(True)

    printer.command, printer.disconnect = command, disconnect
    result = await printer.label_info(lambda: None)
    assert result.rfid.barcode == "TEST"
    assert result.paper.status == "unknown"
    assert disconnected == [True]


@pytest.mark.parametrize("profile,width,density", [("d110_m", 96, 3), ("b1", 384, 5)])
async def test_model_limits_before_journaling(tmp_path, profile, width, density):
    service = Service(Settings(profile=profile, journal=tmp_path / "jobs.sqlite3"))
    token = claim(service)
    await service.connect(token)
    oversized = request().model_copy(update={"png_base64": png(width + 1)})
    with pytest.raises(ValueError):
        service.submit(oversized, token)
    assert service.journal.get(str(oversized.job_id)) is None
    if profile == "d110_m":
        with pytest.raises(Refused, match="density"):
            service.submit(request().model_copy(update={"density": 4}), token)
    valid = request().model_copy(update={"png_base64": png(width), "density": density})
    service.submit(valid, token)
    await service.task
    assert service.journal.get(str(valid.job_id)).state == "simulated"
    assert service.status().equipment_name == "Niimbot " + service.settings.model_name
    await service.close()
