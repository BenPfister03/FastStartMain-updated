from fastapi.responses import RedirectResponse
from fastapi import Request, status

from app.utilities.security import access_token_cookie_kwargs
from . import router


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(
        url=request.url_for("login_view"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(key="access_token", **access_token_cookie_kwargs())
    return response
