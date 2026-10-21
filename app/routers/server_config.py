"""Password-gated ops console at ``/config``.

Unlock with ``CONFIG_PASSWORD`` from the environment. When that value is empty,
the routes respond with 404 so the panel stays off by default.
"""

from __future__ import annotations

import hashlib
import os
import platform
import secrets
import sys
import threading
import time
from datetime import datetime, timezone

from fastapi import Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.config import (
    clear_settings_cache,
    get_settings,
    mask_database_uri,
    mask_secret,
    set_runtime_env,
)
from app.database import drop_all, engine, ensure_db_and_tables
from app.utilities.flash import flash

from . import router, templates

_STARTED_AT = time.time()
_SESSION_KEY = "config_panel_unlocked"


def _panel_enabled() -> bool:
    return bool(get_settings().config_password)


def _password_matches(provided: str, expected: str) -> bool:
    """Constant-time check that tolerates unequal lengths."""
    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return secrets.compare_digest(left, right)


def _is_unlocked(request: Request) -> bool:
    return bool(request.session.get(_SESSION_KEY))


def _require_panel(request: Request) -> RedirectResponse | None:
    if not _panel_enabled():
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    if not _is_unlocked(request):
        return RedirectResponse(
            url=request.url_for("config_login_view"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return None


def _gather_status() -> dict:
    settings = get_settings()
    db_ok = False
    db_error = None
    user_count = None
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
            try:
                user_count = conn.execute(text('SELECT COUNT(*) FROM "user"')).scalar()
            except Exception:
                try:
                    user_count = conn.execute(text("SELECT COUNT(*) FROM user")).scalar()
                except Exception as exc:  # noqa: BLE001
                    db_error = f"tables may be missing ({exc})"
    except Exception as exc:  # noqa: BLE001
        db_error = str(exc)

    uptime = int(time.time() - _STARTED_AT)
    hours, rem = divmod(uptime, 3600)
    minutes, seconds = divmod(rem, 60)

    return {
        "ok": db_ok,
        "db_error": db_error,
        "user_count": user_count,
        "uptime": f"{hours}h {minutes}m {seconds}s",
        "uptime_seconds": uptime,
        "started_at": datetime.fromtimestamp(_STARTED_AT, tz=timezone.utc).isoformat(),
        "now": datetime.now(tz=timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "env_mode": settings.env,
        "is_production": settings.env.lower() in {"prod", "production"},
    }


def _gather_config() -> dict:
    settings = get_settings()
    return {
        "ENV": settings.env,
        "APP_HOST": settings.app_host,
        "APP_PORT": settings.app_port,
        "DATABASE_URI": mask_database_uri(settings.database_uri),
        "SECRET_KEY": mask_secret(settings.secret_key),
        "CONFIG_PASSWORD": "(set)" if settings.config_password else "(unset)",
        "JWT_ALGORITHM": settings.jwt_algorithm,
        "JWT_ACCESS_TOKEN_EXPIRES": settings.jwt_access_token_expires,
        "DB_POOL_SIZE": settings.db_pool_size,
        "DB_ADDITIONAL_OVERFLOW": settings.db_additional_overflow,
        "DB_POOL_TIMEOUT": settings.db_pool_timeout,
        "DB_POOL_RECYCLE": settings.db_pool_recycle,
        "PORT (os)": os.environ.get("PORT", "(unset)"),
        "RENDER": os.environ.get("RENDER", "(unset)"),
        "RENDER_SERVICE_ID": os.environ.get("RENDER_SERVICE_ID", "(unset)"),
    }


def _schedule_restart() -> None:
    def _exit() -> None:
        time.sleep(1.0)
        os._exit(0)

    threading.Thread(target=_exit, daemon=True).start()


@router.get("/config/login", response_class=HTMLResponse, name="config_login_view")
async def config_login_view(request: Request):
    if not _panel_enabled():
        return templates.TemplateResponse(
            request=request,
            name="config.html",
            context={"mode": "disabled"},
            status_code=404,
        )
    if _is_unlocked(request):
        return RedirectResponse(
            url=request.url_for("config_panel_view"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return templates.TemplateResponse(
        request=request,
        name="config.html",
        context={"mode": "login"},
    )


@router.post("/config/login", name="config_login_action")
async def config_login_action(
    request: Request,
    password: str = Form(""),
):
    if not _panel_enabled():
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    expected = get_settings().config_password
    if _password_matches(password, expected):
        request.session[_SESSION_KEY] = True
        flash(request, "Config panel unlocked", "success")
        return RedirectResponse(
            url=request.url_for("config_panel_view"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    flash(request, "Incorrect config password", "danger")
    return RedirectResponse(
        url=request.url_for("config_login_view"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/config/logout", name="config_logout_action")
async def config_logout_action(request: Request):
    request.session.pop(_SESSION_KEY, None)
    flash(request, "Locked config panel", "success")
    return RedirectResponse(
        url=request.url_for("config_login_view"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/config", response_class=HTMLResponse, name="config_panel_view")
async def config_panel_view(request: Request):
    if not _panel_enabled():
        return templates.TemplateResponse(
            request=request,
            name="config.html",
            context={"mode": "disabled"},
            status_code=404,
        )
    gate = _require_panel(request)
    if gate is not None:
        return gate

    return templates.TemplateResponse(
        request=request,
        name="config.html",
        context={
            "mode": "panel",
            "status": _gather_status(),
            "config": _gather_config(),
        },
    )


@router.post("/config/actions/restart", name="config_restart_action")
async def config_restart_action(request: Request):
    gate = _require_panel(request)
    if gate is not None:
        return gate
    flash(
        request,
        "Restarting process… On Render the service will come back automatically; locally restart uvicorn if it stays down.",
        "warning",
    )
    _schedule_restart()
    return RedirectResponse(
        url=request.url_for("config_panel_view"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/config/actions/reinit-db", name="config_reinit_db_action")
async def config_reinit_db_action(
    request: Request,
    drop: str = Form(""),
    seed: str = Form(""),
):
    gate = _require_panel(request)
    if gate is not None:
        return gate

    try:
        if drop:
            drop_all()
        ensure_db_and_tables()
        created = skipped = 0
        if seed:
            created, skipped = _seed_demo_users()
        msg = "Database reinitialized"
        if drop:
            msg += " (tables dropped)"
        if seed:
            msg += f"; seed created={created} skipped={skipped}"
        flash(request, msg, "success")
    except Exception as exc:  # noqa: BLE001
        flash(request, f"DB reinit failed: {exc}", "danger")

    return RedirectResponse(
        url=request.url_for("config_panel_view"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/config/actions/seed", name="config_seed_action")
async def config_seed_action(request: Request):
    gate = _require_panel(request)
    if gate is not None:
        return gate
    try:
        ensure_db_and_tables()
        created, skipped = _seed_demo_users()
        flash(
            request,
            f"Seed done — created {created}, skipped {skipped}",
            "success",
        )
    except Exception as exc:  # noqa: BLE001
        flash(request, f"Seed failed: {exc}", "danger")
    return RedirectResponse(
        url=request.url_for("config_panel_view"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/config/actions/toggle-env", name="config_toggle_env_action")
async def config_toggle_env_action(request: Request):
    gate = _require_panel(request)
    if gate is not None:
        return gate

    current = get_settings().env.lower()
    target = "development" if current in {"prod", "production"} else "production"
    try:
        value = set_runtime_env(target)
        flash(
            request,
            f"ENV set to {value}. Some settings (OpenAPI schema, engine echo) need a process restart to fully apply.",
            "success",
        )
    except Exception as exc:  # noqa: BLE001
        flash(request, f"ENV toggle failed: {exc}", "danger")
    return RedirectResponse(
        url=request.url_for("config_panel_view"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/config/actions/set-env", name="config_set_env_action")
async def config_set_env_action(
    request: Request,
    mode: str = Form(...),
):
    gate = _require_panel(request)
    if gate is not None:
        return gate
    try:
        value = set_runtime_env(mode)
        flash(request, f"ENV set to {value}", "success")
    except Exception as exc:  # noqa: BLE001
        flash(request, f"ENV update failed: {exc}", "danger")
    return RedirectResponse(
        url=request.url_for("config_panel_view"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/config/actions/ping-db", name="config_ping_db_action")
async def config_ping_db_action(request: Request):
    gate = _require_panel(request)
    if gate is not None:
        return gate
    status_info = _gather_status()
    if status_info["ok"]:
        flash(
            request,
            f"Database OK (users={status_info['user_count']})",
            "success",
        )
    else:
        flash(request, f"Database error: {status_info['db_error']}", "danger")
    return RedirectResponse(
        url=request.url_for("config_panel_view"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/config/actions/dispose-pool", name="config_dispose_pool_action")
async def config_dispose_pool_action(request: Request):
    gate = _require_panel(request)
    if gate is not None:
        return gate
    try:
        engine.dispose()
        flash(request, "Connection pool disposed; next request will reconnect", "success")
    except Exception as exc:  # noqa: BLE001
        flash(request, f"Dispose failed: {exc}", "danger")
    return RedirectResponse(
        url=request.url_for("config_panel_view"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/config/actions/clear-cache", name="config_clear_cache_action")
async def config_clear_cache_action(request: Request):
    gate = _require_panel(request)
    if gate is not None:
        return gate
    clear_settings_cache()
    flash(request, "Settings cache cleared; next read reloads from env/.env", "success")
    return RedirectResponse(
        url=request.url_for("config_panel_view"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _seed_demo_users() -> tuple[int, int]:
    from app.database import get_cli_session
    from app.repositories.user import UserRepository
    from app.schemas.user import AdminCreate, RegularUserCreate
    from app.utilities.security import encrypt_password

    demo_users = [
        ("bob", "bob@example.com", "bobpass", "regular_user"),
        ("admin", "admin@example.com", "adminpass", "admin"),
    ]
    created = 0
    skipped = 0
    with get_cli_session() as session:
        repo = UserRepository(session)
        for username, email, password, role in demo_users:
            if repo.get_by_username(username):
                skipped += 1
                continue
            payload_cls = AdminCreate if role == "admin" else RegularUserCreate
            repo.create(
                payload_cls(
                    username=username,
                    email=email,
                    password=encrypt_password(password),
                    role=role,
                )
            )
            created += 1
    return created, skipped
