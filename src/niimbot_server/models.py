from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sdl_lab_contract import ClaimedBy


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Settings(StrictModel):
    mode: Literal["simulate", "ble"] = "simulate"
    equipment_id: str = "niimbot_d110m"
    target: str | None = None  # Exact advertised name, or CoreBluetooth UUID / BLE address.
    characteristic: str | None = None
    profile: Literal["d110_m", "d110_m_v4"] = "d110_m"
    journal: Path = Path("local/jobs.sqlite3")
    command_timeout_s: float = Field(default=10, ge=0.1, le=30)
    job_timeout_s: float = Field(default=120, ge=1, le=600)
    row_delay_s: float = Field(default=0.01, ge=0, le=0.1)
    cors_origins: list[str] = Field(default_factory=list)


class PrintRequest(StrictModel):
    job_id: UUID
    png_base64: str = Field(min_length=1, max_length=1_400_000)
    copies: int = Field(default=1, ge=1, le=20)
    density: int = Field(default=3, ge=1, le=3)


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
