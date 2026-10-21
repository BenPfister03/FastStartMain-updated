from fastapi import Request
from fastapi.responses import HTMLResponse

from app.dependencies.auth import IsUserLoggedIn, get_current_user, is_admin
from app.dependencies.session import SessionDep
from . import router, templates


@router.get("/", response_class=HTMLResponse, name="index_view")
async def index_view(
    request: Request,
    user_logged_in: IsUserLoggedIn,
    db: SessionDep,
):
    app_home = request.url_for("login_view")
    if user_logged_in:
        user = await get_current_user(request, db)
        if await is_admin(user):
            app_home = request.url_for("admin_home_view")
        else:
            app_home = request.url_for("user_home_view")

    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "user_logged_in": user_logged_in,
            "app_home": app_home,
        },
    )
