from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sdl_lab_contract import ClaimedBy


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Settings(StrictModel):
    mode: Literal["simulate", "ble"] = "simulate"
    equipment_id: str | None = None
    target: str | None = None  # Exact advertised name, or CoreBluetooth UUID / BLE address.
    characteristic: str | None = None
    profile: Literal["d110_m", "d110_m_v4", "b1"] = "d110_m"
    journal: Path = Path("local/jobs.sqlite3")
    command_timeout_s: float = Field(default=10, ge=0.1, le=30)
    job_timeout_s: float = Field(default=120, ge=1, le=600)
    row_delay_s: float = Field(default=0.01, ge=0, le=0.1)
    cors_origins: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def default_identity(self):
        if self.equipment_id is None:
            self.equipment_id = "niimbot_b1" if self.profile == "b1" else "niimbot_d110m"
        return self

    @property
    def model_name(self):
        return "B1" if self.profile == "b1" else "D110_M"

    @property
    def head_pixels(self):
        return 384 if self.profile == "b1" else 96

    @property
    def max_density(self):
        return 5 if self.profile == "b1" else 3


class PrintRequest(StrictModel):
    job_id: UUID
    png_base64: str = Field(min_length=1, max_length=1_400_000)
    copies: int = Field(default=1, ge=1, le=20)
    density: int = Field(default=3, ge=1, le=5)


class RfidInfo(BaseModel):
    status: Literal["available", "no_tag", "unsupported", "unknown"] = "unknown"
    tag_present: bool | None = None
    tag_uid: str | None = None
    barcode: str | None = None
    serial: str | None = None
    total: int | None = None
    used: int | None = None
    remaining: int | None = None
    counter_unit: Literal["unknown"] = "unknown"
    consumables_type: int | None = None
    capacity: int | None = None
    secondary_uid: str | None = None
    extra_hex: str = ""
    message: str | None = None


class PaperInfo(BaseModel):
    status: Literal["available", "unsupported", "unknown"] = "unknown"
    width_mm: float | None = None
    height_mm: float | None = None
    gap_mm: float | None = None
    width_pixels: int | None = None
    height_pixels: int | None = None
    consumables_type: int | None = None
    raw_hex: str = ""
    message: str | None = None


class LabelInfo(BaseModel):
    queried_at: datetime
    simulated: bool
    rfid: RfidInfo
    paper: PaperInfo


class ReconcileRequest(StrictModel):
    job_id: UUID
    observed_outcome: Literal["printed", "not_printed"]
    printer_idle_confirmed: Literal[True]
    note: str = Field(min_length=1, max_length=1000)


class ClaimInput(StrictModel):
    owner: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    ttl_s: float = Field(default=30, ge=5, le=300)


class Job(BaseModel):
    job_id: str
    fingerprint: str
    state: Literal["running", "completed", "simulated", "unknown", "reconciled"]
    copies: int
    simulated: bool
    created_at: str
    updated_at: str
    message: str
    observed_outcome: str | None = None


class Refusal(BaseModel):
    detail: str
    code: str
    required_actions: list[str] = Field(default_factory=list)
    claimed_by: ClaimedBy | None = None


class ActionResult(BaseModel):
    message: str


class DeviceInfo(BaseModel):
    name: str | None
    address: str


class PowerInfo(BaseModel):
    queried_at: datetime
    simulated: bool
    model_id: int
    firmware_hex: str
    shutdown_index: int = Field(ge=1, le=4)
    message: str = "Shutdown index is model-dependent; 4 can mean 60 minutes or never"


class PowerSetting(StrictModel):
    shutdown_index: int = Field(ge=1, le=4)
    expected_current_index: int = Field(ge=1, le=4)
