import asyncio
import hmac
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .service import Conflict, Service


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    samples: list[dict] = Field(min_length=1, max_length=3)


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "manual"] | None = None
    profile: Literal["voice", "interactive", "bulk"] | None = None


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "reject"]


def create_app(service=None, token=None, report_dir="reports/latest"):
    service = service or Service()
    token = token if token is not None else os.getenv("SDWAN_API_TOKEN")

    @asynccontextmanager
    async def lifespan(app):
        async def loop():
            while True:
                started = time.monotonic()
                try:
                    collector = getattr(service, "collector", None)
                    if collector:
                        samples = await asyncio.to_thread(collector.collect)
                        await asyncio.to_thread(service.ingest, samples)
                    await asyncio.to_thread(service.tick)
                    await asyncio.to_thread(service.store.put, "service_status", service.status())
                except Exception as error:  # noqa: BLE001 - isolate failures at the control-loop boundary
                    service.store.log(
                        time.time(), "control_loop_error", {"error_type": type(error).__name__}
                    )
                    try:
                        await asyncio.to_thread(service.tick)
                    except Exception as watchdog_error:  # noqa: BLE001 - watchdog must keep running
                        service.store.log(
                            time.time(),
                            "watchdog_error",
                            {"error_type": type(watchdog_error).__name__},
                        )
                await asyncio.sleep(max(0, 5 - (time.monotonic() - started)))

        task = asyncio.create_task(loop())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.to_thread(service.close)

    app = FastAPI(
        title="AI SD-WAN Lab", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.mount(
        "/openapi-assets",
        StaticFiles(directory=Path(__file__).parent / "openapi_assets"),
        name="openapi-assets",
    )

    @app.get("/docs", include_in_schema=False)
    def docs(request: Request):
        response = get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title="AI SD-WAN control API",
            swagger_js_url="/openapi-assets/swagger-ui-bundle.js",
            swagger_css_url="/openapi-assets/swagger-ui.css",
            swagger_favicon_url="/openapi-assets/favicon-32x32.png",
            swagger_ui_parameters={"persistAuthorization": False},
        )
        from fastapi.responses import HTMLResponse

        body = response.body.decode().replace(
            "<script>", '<script nonce="' + request.state.doc_nonce + '">'
        )
        return HTMLResponse(body)

    bearer = HTTPBearer(auto_error=False)

    def authorized(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        supplied = credentials.credentials if credentials is not None else ""
        if token and not hmac.compare_digest(supplied, token):
            raise HTTPException(401, "Bearer token required")
        if (
            not token
            and request.client
            and request.client.host not in {"127.0.0.1", "::1", "testclient"}
        ):
            raise HTTPException(403, "Configure SDWAN_API_TOKEN for remote API access")
        origin = request.headers.get("origin")
        if origin:
            expected = urlsplit(str(request.base_url))
            supplied = urlsplit(origin)
            if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc):
                raise HTTPException(403, "Cross-origin API access is disabled")

    @app.middleware("http")
    async def headers(request, call_next):
        if request.url.path == "/docs":
            request.state.doc_nonce = secrets.token_urlsafe(24)
        if request.method in {"POST", "PUT", "PATCH"}:
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Invalid length"}, 400)
            if length > 65536:
                return JSONResponse({"detail": "Body too large"}, 413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.url.path == "/docs":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'nonce-"
                + request.state.doc_nonce
                + "'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
            )
        return response

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "backend": service.adapter.backend,
            "model_loaded": service.model is not None,
            "storage": service.store.backend,
        }

    @app.get("/api/state", dependencies=[Depends(authorized)])
    def state():
        return service.status()

    @app.get("/api/events", dependencies=[Depends(authorized)])
    def events():
        return service.store.events()

    @app.post("/api/telemetry", dependencies=[Depends(authorized)])
    def ingest(batch: Batch):
        if service.adapter.backend.startswith("flexiwan"):
            raise HTTPException(
                403, "Live telemetry is collected from the edge; replay injection disabled"
            )
        result = service.ingest(batch.samples)
        return {**result, "state": service.tick()}

    @app.post("/api/configuration", dependencies=[Depends(authorized)])
    def configure(configuration: Configuration):
        try:
            return service.configure(configuration.mode, configuration.profile)
        except Conflict as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/recommendations/{recommendation_id}", dependencies=[Depends(authorized)])
    def review(recommendation_id: str, review: Review):
        try:
            result = service.review(recommendation_id, review.action == "approve")
            return JSONResponse(result, status_code=202 if result.get("apply_inflight") else 200)
        except Conflict as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/evaluation", dependencies=[Depends(authorized)])
    def evaluation():
        path = Path(report_dir) / "results.json"
        if not path.exists():
            raise HTTPException(404, "Run python -m sdwan eval first")
        return FileResponse(path, media_type="application/json")

    @app.get("/")
    def dashboard():
        return RedirectResponse("https://localhost:3000/d/ai-sdwan", status_code=307)

    return app
