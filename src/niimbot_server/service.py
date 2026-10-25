import asyncio
import contextlib
import hashlib
import secrets
import time
from datetime import UTC, datetime, timedelta

from sdl_lab_contract import (
    ClaimedBy,
    ClaimResponse,
    ComponentStatus,
    EquipmentStatus,
    ErrorInfo,
    MetricValue,
)

from .driver import BlePrinter, SimulatedPrinter
from .journal import Journal, now
from .models import Job, Settings
from .protocol import ProtocolError, decode_png


class Refused(Exception):
    def __init__(self, status, code, detail, actions=()):
        self.status, self.code, self.detail, self.actions = status, code, detail, list(actions)


class Service:
    def __init__(self, settings: Settings, driver=None):
        self.settings = settings
        self.driver = driver or (
            SimulatedPrinter(settings) if settings.mode == "simulate" else BlePrinter(settings)
        )
        self.journal = Journal(settings.journal)
        self.claim = None
        self.token = None
        self.deadline = 0
        self.ttl = 30
        self.lock = asyncio.Lock()
        self.task = None
        self.active_job = None
        self.activity = "idle"
        self.activity_since = datetime.now(UTC)
        self.started = time.monotonic()
        self.cycles = 0
        self.last_error = None
        self.label_snapshot = None
        self.power_snapshot = None

    def current_claim(self):
        if time.monotonic() >= self.deadline:
            return None
        return self.claim

    def acquire(self, request):
        current = self.current_claim()
        if current and (current.session_id != request.session_id or current.owner != request.owner):
            raise Refused(409, "claimed", "Printer is claimed by another session")
        if not current:
            if self.active_job or self.lock.locked():
                raise Refused(409, "busy", "Previous operation is still settling")
            self.token = secrets.token_urlsafe(32)
            self.ttl = request.ttl_s
        self.deadline = time.monotonic() + self.ttl
        self.claim = ClaimedBy(
            owner=request.owner,
            session_id=request.session_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.ttl),
        )
        return ClaimResponse(
            claim_token=self.token,
            heartbeat_interval_s=self.ttl / 3,
            expires_at=self.claim.expires_at,
        )

    def require(self, token):
        if not self.current_claim() or not token or not secrets.compare_digest(token, self.token):
            raise Refused(423, "claim_required", "A valid X-Claim-Token is required")

    def heartbeat(self, token):
        try:
            self.require(token)
        except Refused as exc:
            raise Refused(401, "claim_lost", "Claim expired or token is invalid") from exc
        self.deadline = time.monotonic() + self.ttl
        self.claim.expires_at = datetime.now(UTC) + timedelta(seconds=self.ttl)

    def release(self, token):
        if token and self.token and secrets.compare_digest(token, self.token):
            self.deadline = 0
            self.claim = self.token = None

    def idle(self):
        if self.active_job or self.lock.locked():
            raise Refused(412, "busy", "An operation is in progress")

    def reconciled(self):
        if self.journal.uncertain():
            raise Refused(
                412,
                "outcome_unknown",
                "Reconcile uncertain jobs before continuing",
                ["printer.reconcile"],
            )

    def set_activity(self, value):
        if self.activity != value:
            self.activity = value
            self.activity_since = datetime.now(UTC)

    def error(self, code, message):
        self.last_error = ErrorInfo(
            code=code, message=message, severity="error", timestamp=datetime.now(UTC)
        )

    async def connect(self, token):
        self.require(token)
        self.idle()
        self.reconciled()
        async with self.lock:
            if self.driver.connected:
                return
            self.label_snapshot = None
            self.power_snapshot = None
            try:
                async with asyncio.timeout(30):
                    await self.driver.connect()
                self.require(token)
                self.set_activity("idle")
                self.last_error = None
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await self.driver.disconnect()
                self.error("connection_failed", str(exc))
                raise Refused(503, "connection_failed", str(exc)) from exc

    async def disconnect(self, token):
        self.require(token)
        self.idle()
        async with self.lock:
            await self.driver.disconnect()

    async def scan(self, token):
        self.require(token)
        self.idle()
        async with self.lock:
            async with asyncio.timeout(15):
                return await self.driver.scan()

    async def label_info(self, token):
        self.require(token)
        self.idle()
        self.reconciled()
        if not self.driver.connected:
            raise Refused(412, "requires_init", "Printer is not connected", ["printer.connect"])
        if getattr(self.driver, "fault", None):
            raise Refused(412, "printer_fault", "Printer connection has an active fault")
        async with self.lock:
            self.label_snapshot = None
            try:
                async with asyncio.timeout(self.settings.command_timeout_s * 2 + 6):
                    result = await self.driver.label_info(lambda: self.require(token))
                self.require(token)
                self.label_snapshot = result
                return result
            except Refused:
                raise
            except Exception as exc:
                # Do not leave a timed-out command's reply available to a later operation.
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(5):
                        await self.driver.disconnect()
                raise Refused(503, "label_query_failed", str(exc)) from exc

    async def power(self, token, setting=None):
        self.require(token)
        self.idle()
        self.reconciled()
        if self.settings.profile != "b1":
            raise Refused(412, "unsupported_model", "Power controls are restricted to B1")
        if not self.driver.connected or getattr(self.driver, "fault", None):
            raise Refused(412, "requires_init", "A connected fault-free printer is required")
        async with self.lock:
            self.power_snapshot = None
            attempted = False
            try:
                async with asyncio.timeout(self.settings.command_timeout_s * 7 + 5):

                    def guard():
                        self.require(token)

                    before = await self.driver.power_info(guard)
                    if setting is not None:
                        if before.model_id != 4096:
                            raise Refused(
                                412, "unsupported_model", "Firmware does not identify as B1"
                            )
                        if before.shutdown_index != setting.expected_current_index:
                            raise Refused(
                                409, "setting_changed", "Shutdown setting changed; read it again"
                            )
                        if before.shutdown_index == setting.shutdown_index:
                            self.power_snapshot = before
                            return before
                        self.journal.configuration_event(
                            action="set_shutdown",
                            outcome="intent",
                            owner=self.claim.owner,
                            before=before.model_dump(mode="json"),
                            requested=setting.shutdown_index,
                        )
                        attempted = True
                        await self.driver.set_shutdown(setting.shutdown_index, guard)
                        after = await self.driver.power_info(guard)
                        if (after.model_id, after.firmware_hex, after.shutdown_index) != (
                            before.model_id,
                            before.firmware_hex,
                            setting.shutdown_index,
                        ):
                            raise ProtocolError(
                                "Shutdown readback does not match; no automatic retry"
                            )
                        self.journal.configuration_event(
                            action="set_shutdown",
                            outcome="readback_verified",
                            after=after.model_dump(mode="json"),
                        )
                    else:
                        after = before
                    self.require(token)
                    self.power_snapshot = after
                    return after
            except Exception as exc:
                if attempted:
                    self.journal.configuration_event(
                        action="set_shutdown", outcome="unknown", error=str(exc)
                    )
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(5):
                        await self.driver.disconnect()
                if isinstance(exc, Refused):
                    raise
                raise Refused(
                    503,
                    "power_operation_failed",
                    f"{type(exc).__name__}: {exc}; read the setting before any retry",
                ) from exc

    def submit(self, request, token):
        self.require(token)
        fingerprint = hashlib.sha256(
            request.model_dump_json(exclude={"job_id"}).encode()
        ).hexdigest()
        old = self.journal.get(str(request.job_id))
        if old:
            if old.fingerprint != fingerprint:
                raise Refused(409, "job_conflict", "Job ID already exists with different content")
            return old  # Never re-send a job, including an unknown or reconciled one.
        self.idle()
        self.reconciled()
        if not self.driver.connected:
            raise Refused(412, "requires_init", "Printer is not connected", ["printer.connect"])
        if getattr(self.driver, "fault", None):
            raise Refused(412, "printer_fault", "Printer connection has an active fault")
        if request.density > self.settings.max_density:
            raise Refused(
                412,
                "density_out_of_range",
                f"{self.settings.model_name} supports density 1–{self.settings.max_density}",
            )
        image = decode_png(request.png_base64, self.settings.head_pixels)
        job = Job(
            job_id=str(request.job_id),
            fingerprint=fingerprint,
            state="running",
            copies=request.copies,
            simulated=self.settings.mode == "simulate",
            created_at=now(),
            updated_at=now(),
            message="Accepted; completion not yet observed",
        )
        self.label_snapshot = None
        self.journal.put(job)  # Durable intent before the first possible device write.
        self.active_job = job.job_id
        self.set_activity("unknown")  # Sending a job is not evidence of physical movement.
        self.task = asyncio.create_task(self.run(job, request, image, token))
        return job

    async def run(self, job, request, image, token):
        async with self.lock:
            try:
                async with asyncio.timeout(self.settings.job_timeout_s):
                    await self.driver.print_image(
                        image,
                        request.copies,
                        request.density,
                        lambda: self.require(token),
                        lambda: self.set_activity("running"),
                    )
                self.journal.update(
                    job,
                    state="simulated" if job.simulated else "completed",
                    message="Simulated only"
                    if job.simulated
                    else "Printer reported requested page count",
                )
                self.cycles += request.copies
                self.set_activity("idle")
                self.last_error = None
            except BaseException as exc:
                self.journal.update(
                    job,
                    state="unknown",
                    message=f"Outcome unknown; do not retry: {type(exc).__name__}: {exc}",
                )
                self.set_activity("unknown")
                self.error("outcome_unknown", "Job interrupted; operator reconciliation required")
                # Closing the link is not advertised as a physical stop or cancellation.
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(5):
                        await self.driver.disconnect()
                if not isinstance(exc, (Exception, asyncio.CancelledError)):
                    raise
            finally:
                image.close()
                self.active_job = None

    def reconcile(self, request, token):
        self.require(token)
        self.idle()
        job = self.journal.get(str(request.job_id))
        if not job or job.state != "unknown":
            raise Refused(412, "not_uncertain", "Only an uncertain job can be reconciled")
        result = self.journal.update(
            job,
            state="reconciled",
            observed_outcome=request.observed_outcome,
            message=f"Operator {self.claim.owner}: {request.note}",
        )
        if not self.journal.uncertain():
            self.set_activity("idle")
        return result

    def status(self):
        uncertain = self.journal.uncertain()
        connected = self.driver.connected
        fault = getattr(self.driver, "fault", None)
        active = bool(self.active_job or self.lock.locked())
        if uncertain:
            state, activity = "unknown", "unknown"
        elif active:
            state = "busy" if self.activity == "running" else "unknown"
            activity = self.activity
        elif self.settings.mode == "simulate":
            state, activity = "dry_run", "idle"
        elif connected and isinstance(fault, ProtocolError):
            state, activity = "error", "unknown"
        elif not connected:
            state, activity = "requires_init", "idle"
        else:
            state, activity = "ready", "idle"
        actions = []
        if not active:
            actions = ["printer.scan", "printer.disconnect"]
            if uncertain:
                actions.append("printer.reconcile")
            elif not fault or not connected:
                actions.append("printer.print" if connected else "printer.connect")
                if connected:
                    actions.append("printer.label_info")
                    if self.settings.profile == "b1":
                        actions.extend(["printer.power_info", "printer.set_power"])
        return EquipmentStatus(
            protocol_version="1.2",
            equipment_id=self.settings.equipment_id,
            equipment_name="Niimbot " + self.settings.model_name,
            equipment_kind="other",
            equipment_version="0.1.0",
            equipment_status=state,
            activity=activity,
            activity_since=self.activity_since,
            message="SIMULATION: no hardware"
            if self.settings.mode == "simulate"
            else "Printer protocol is experimental until validated on this printer",
            device_time=datetime.now(UTC),
            uptime_seconds=time.monotonic() - self.started,
            required_actions=["printer.reconcile"]
            if uncertain
            else ([] if connected else ["printer.connect"]),
            components={
                "printer": ComponentStatus(
                    connected=connected, state="connected" if connected else "disconnected"
                )
            },
            metrics={"cycles_total": MetricValue(value=self.cycles, unit="count")},
            last_error=self.last_error,
            allowed_actions=actions,
            details={
                "claimed_by": self.current_claim(),
                "simulated": self.settings.mode == "simulate",
                "profile": self.settings.profile,
                "model": self.settings.model_name,
                "head_pixels": self.settings.head_pixels,
                "max_density": self.settings.max_density,
                "label_info_snapshot": self.label_snapshot,
                "power_info_snapshot": self.power_snapshot,
                "active_job": self.active_job,
                "uncertain_jobs": [j.job_id for j in uncertain],
                "hardware_validated": False,
            },
        )

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.active_job:
            job = self.journal.get(self.active_job)
            if job and job.state == "running":
                self.journal.update(job, state="unknown", message="Shutdown before worker settled")
        try:
            async with asyncio.timeout(5):
                await self.driver.disconnect()
        finally:
            self.journal.close()
