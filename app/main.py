from __future__ import annotations

import logging
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.intent_engine import IntentRoutingEngine
from app.models import AnalyzeRequest, AnalyzeResponse, HealthResponse

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # P0: API key authentication middleware
    if settings.require_auth and settings.api_key:
        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            if request.url.path.startswith("/api/") and request.method != "OPTIONS":
                auth = request.headers.get("Authorization", "")
                if not auth.startswith("Bearer ") or auth[7:].strip() != settings.api_key:
                    return JSONResponse(status_code=401, content={"detail": "未授权访问"})
            return await call_next(request)

    # P2: Observability middleware (request timing)
    @app.middleware("http")
    async def observability_middleware(request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        logger.info(
            "method=%s path=%s status=%s elapsed_ms=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
        return response

    engine = IntentRoutingEngine(settings)
    app.state.engine = engine
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(settings.static_dir / "index.html")

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return app.state.engine.build_health()

    @app.post("/api/query", response_model=AnalyzeResponse)
    def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
        # P0: server-side override — client cannot bypass execution guard
        if request.allow_action_execution and not settings.server_allow_action_execution:
            request = request.model_copy(update={"allow_action_execution": False})
        return app.state.engine.analyze(request)

    # P2: hot-reload data without restart
    @app.post("/api/reload", include_in_schema=False)
    def reload_data():
        app.state.engine = IntentRoutingEngine(settings)
        return {"status": "reloaded"}

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
