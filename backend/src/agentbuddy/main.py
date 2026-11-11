from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from filelock import FileLock
from pydantic import BaseModel

from agentbuddy.api.capabilities import router as capabilities_router
from agentbuddy.api.catalog import router as catalog_router
from agentbuddy.api.chat import router as chat_router
from agentbuddy.api.system import router as system_router
from agentbuddy.catalog.service import DomainError
from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database


class HealthResponse(BaseModel):
    status: str

    service: str


def create_app(settings: Settings | None = None) -> FastAPI:

    settings = settings or Settings()

    prefix = settings.api_prefix

    database = Database(settings.database_path)

    @asynccontextmanager
    async def lifespan(app):

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with FileLock(str(settings.database_path) + ".api.lock", timeout=0):
            try:
                yield
            finally:
                database.close()

    app = FastAPI(
        lifespan=lifespan,
        title="AgentBuddy API",
        version="0.1.0",
        openapi_url=f"{prefix}/openapi.json",
        docs_url=f"{prefix}/docs",
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=f"{prefix}/docs/oauth2-redirect",
    )

    app.state.database = database

    app.state.settings = settings

    router = APIRouter()

    @router.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:

        # Liveness only: does not claim models, database or Worker are ready.

        return HealthResponse(status="ok", service="agentbuddy-api")

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):

        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {"code": exc.code, "message": exc.message, "retryable": False},
                "request_id": str(uuid4()),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):

        # Never echo input values: provider forms may contain credentials.

        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数不合法，请检查表单",
                    "fields": [list(e["loc"]) for e in exc.errors()],
                }
            },
        )

    router.include_router(capabilities_router)
    router.include_router(catalog_router)

    router.include_router(chat_router)

    router.include_router(system_router)

    app.include_router(router, prefix=prefix)

    return app
