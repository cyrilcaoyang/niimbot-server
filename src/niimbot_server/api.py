from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sdl_lab_contract import ClaimResponse, EquipmentStatus, HealthResponse, ProbeResponse

from .models import (
    ActionResult,
    ClaimInput,
    DeviceInfo,
    Job,
    PrintRequest,
    ReconcileRequest,
    Refusal,
    Settings,
)
from .service import Refused, Service

Token = Annotated[str | None, Header(alias="X-Claim-Token")]
ERRORS = {code: {"model": Refusal} for code in (401, 409, 412, 423, 503)}


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        messages, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > 1_500_000:
                response = JSONResponse(
                    {"detail": "Request exceeds 1.5 MB", "code": "too_large"}, status_code=413
                )
                return await response(scope, receive, send)
            messages.append(message)
            if not message.get("more_body"):
                break

        async def replay():
            return messages.pop(0) if messages else await receive()

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None, driver=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.service = Service(settings, driver)
        try:
            yield
        finally:
            await app.state.service.close()

    app = FastAPI(title="Niimbot D110_M server", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BodyLimit)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["X-Claim-Token", "Content-Type"],
        )

    @app.exception_handler(Refused)
    async def refused(request, exc):
        return JSONResponse(
            status_code=exc.status,
            content=Refusal(
                detail=exc.detail,
                code=exc.code,
                required_actions=exc.actions,
                claimed_by=request.app.state.service.current_claim()
                if exc.code == "claimed"
                else None,
            ).model_dump(mode="json"),
        )

    @app.get("/", response_model=ProbeResponse)
    def probe():
        return ProbeResponse(
            equipment_id=settings.equipment_id,
            equipment_name="Niimbot D110_M",
            protocol_version="1.2",
        )

    @app.get("/health", response_model=HealthResponse)
    def health():
        return HealthResponse(status="healthy")

    @app.get("/status", response_model=EquipmentStatus)
    async def status(request: Request):
        """Cached local state only. Never scans, connects, or writes to the printer."""
        return request.app.state.service.status()

    @app.get("/jobs/{job_id}", response_model=Job, responses={404: {"model": Refusal}})
    async def job(job_id: UUID, request: Request):
        result = request.app.state.service.journal.get(str(job_id))
        if not result:
            raise Refused(404, "not_found", "Job not found")
        return result

    @app.post("/control/claim", response_model=ClaimResponse, responses=ERRORS)
    async def claim(body: ClaimInput, request: Request):
        return request.app.state.service.acquire(body)

    @app.post("/control/heartbeat", status_code=204, responses=ERRORS)
    async def heartbeat(request: Request, token: Token = None):
        request.app.state.service.heartbeat(token)
        return Response(status_code=204)

    @app.post("/control/release", status_code=204)
    async def release(request: Request, token: Token = None):
        request.app.state.service.release(token)
        return Response(status_code=204)

    @app.post("/control/scan", response_model=list[DeviceInfo], responses=ERRORS)
    async def scan(request: Request, token: Token = None):
        try:
            return await request.app.state.service.scan(token)
        except Refused:
            raise
        except Exception as exc:
            raise Refused(503, "scan_failed", str(exc)) from exc

    @app.post("/control/connect", response_model=ActionResult, responses=ERRORS)
    async def connect(request: Request, token: Token = None):
        await request.app.state.service.connect(token)
        return ActionResult(message="Connected; no label printed")

    @app.post("/control/disconnect", response_model=ActionResult, responses=ERRORS)
    async def disconnect(request: Request, token: Token = None):
        await request.app.state.service.disconnect(token)
        return ActionResult(message="Disconnected; this is not a physical stop command")

    @app.post(
        "/control/print",
        status_code=202,
        response_model=Job,
        responses={**ERRORS, 400: {"model": Refusal}, 413: {"model": Refusal}},
    )
    async def print_label(body: PrintRequest, request: Request, token: Token = None):
        """Accept an authorized SDK job. Acceptance does not mean physical completion."""
        try:
            return request.app.state.service.submit(body, token)
        except ValueError as exc:
            raise Refused(400, "invalid_image", str(exc)) from exc

    @app.post("/control/reconcile", response_model=Job, responses=ERRORS)
    async def reconcile(body: ReconcileRequest, request: Request, token: Token = None):
        """Operator observation only. Never retransmits the job or changes the printer."""
        return request.app.state.service.reconcile(body, token)

    @app.get("/agent-docs", response_class=PlainTextResponse)
    def guide():
        return PlainTextResponse(
            files("niimbot_server").joinpath("agent-guide.md").read_text(),
            media_type="text/markdown",
        )

    @app.get("/llms.txt", response_class=PlainTextResponse)
    def index():
        return (
            "# Niimbot server\n- [Guide](/agent-docs)\n"
            "- [OpenAPI](/openapi.json)\n- [Swagger](/docs)\n"
        )

    @app.get("/docs/agent")
    def agent_documentation():
        return {
            "documentation_version": "1.0",
            "equipment_kind": "other",
            "model": "D110_M",
            "protocol_version": "1.2",
            "scope": "Single experimental label printer",
            "links": {"guide": "/agent-docs", "openapi": "/openapi.json"},
            "agent_boundary": [
                "Use lab-skills and authorized plans; claims are not approval",
                "Never retry an uncertain print or reconcile without a human",
            ],
        }

    @app.get("/plans/actions")
    def actions():
        # Discovery only; this service does not yet implement the device-run plan executor.
        return {
            "actions": [
                {
                    "action": "printer.print",
                    "idempotent": True,
                    "args_schema": PrintRequest.model_json_schema(),
                    "description": "Job-ID deduplication; never repeat a physical print",
                },
            ]
        }

    return app
