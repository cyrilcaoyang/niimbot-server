import json

import pytest
from test_service import claim

from niimbot_server.driver import BlePrinter, SimulatedPrinter
from niimbot_server.models import PowerSetting, Settings
from niimbot_server.service import Refused, Service


async def test_driver_power_query_and_write_are_exact_nonprinting_commands():
    printer = BlePrinter(Settings(profile="b1"))
    sent = []

    async def command(code, data, response, guard):
        guard()
        sent.append((code, data, response))
        return {8: b"\x10\x00", 9: b"\x05\x12", 7: b"\x01"}[data[0]] if code == 0x40 else b"\x01"

    printer.command = command
    result = await printer.power_info(lambda: None)
    assert result.model_id == 4096 and result.firmware_hex == "0512"
    await printer.set_shutdown(4, lambda: None)
    assert sent == [
        (0x40, b"\x08", 0x48),
        (0x40, b"\x09", 0x49),
        (0x40, b"\x07", 0x47),
        (0x27, b"\x04", 0x37),
    ]


async def test_power_claim_readback_journal_and_cached_status(tmp_path):
    service = Service(Settings(profile="b1", journal=tmp_path / "jobs.sqlite3"))
    try:
        with pytest.raises(Refused):
            await service.power(None)
        token = claim(service)
        await service.connect(token)
        before = await service.power(token)
        assert before.shutdown_index == 1
        result = await service.power(
            token, PowerSetting(shutdown_index=4, expected_current_index=1)
        )
        assert result.shutdown_index == 4
        assert service.status().details["power_info_snapshot"] == result
        rows = service.journal.db.execute("SELECT data FROM configuration_events").fetchall()
        assert [json.loads(r[0])["outcome"] for r in rows] == ["intent", "readback_verified"]
        assert service.cycles == 0
        with pytest.raises(Refused) as exc:
            await service.power(token, PowerSetting(shutdown_index=2, expected_current_index=1))
        assert exc.value.code == "setting_changed"
    finally:
        await service.close()


@pytest.mark.parametrize("bad_model,ignore_write", [(True, False), (False, True)])
async def test_wrong_model_or_failed_readback_does_not_claim_success(
    tmp_path, bad_model, ignore_write
):
    class Driver(SimulatedPrinter):
        writes = 0

        async def power_info(self, guard):
            result = await super().power_info(guard)
            result.model_id = 4097 if bad_model else 4096
            return result

        async def set_shutdown(self, index, guard):
            self.writes += 1
            if not ignore_write:
                await super().set_shutdown(index, guard)

    driver = Driver()
    service = Service(Settings(profile="b1", journal=tmp_path / "j.sqlite3"), driver)
    try:
        token = claim(service)
        await service.connect(token)
        with pytest.raises(Refused):
            await service.power(token, PowerSetting(shutdown_index=4, expected_current_index=1))
        assert driver.writes == (0 if bad_model else 1)
        assert service.power_snapshot is None
        assert not driver.connected
    finally:
        await service.close()
