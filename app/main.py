import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from app.config import get_settings
from app.routers import api_router, router, static_files, templates

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import ensure_db_and_tables

    # First boot on Render often races the free Postgres instance.
    ensure_db_and_tables()
    yield


app = FastAPI(
    middleware=[
        Middleware(SessionMiddleware, secret_key=get_settings().secret_key)
    ],
    lifespan=lifespan,
)

app.include_router(router)
app.include_router(api_router)
app.mount("/static", static_files, name="static")


@app.middleware("http")
async def recover_uninitialized_db(request: Request, call_next):
    """If a request hits a missing schema, create tables once and retry."""
    from app.database import recover_if_uninitialized

    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001 — catch then re-raise if not DB init
        if not recover_if_uninitialized(exc):
            raise
        logger.info("Retrying %s after database init", request.url.path)
        try:
            return await call_next(request)
        except Exception:
            # Schema exists now but the prior response may still need a refresh.
            if request.method.upper() == "GET":
                return RedirectResponse(url=str(request.url), status_code=303)
            raise


@app.get("/health")
async def health():
    return {"ok": True}


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    """JSON errors for /api (spec shape {"error": ...}); HTML 401 page elsewhere."""
    if request.url.path.startswith("/api"):
        return JSONResponse(
            {"error": exc.detail, "detail": exc.detail},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        return templates.TemplateResponse(
            request=request,
            name="401.html",
        )
    return await http_exception_handler(request, exc)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=get_settings().app_host,
        port=get_settings().app_port,
        reload=get_settings().env.lower() != "production",
    )
