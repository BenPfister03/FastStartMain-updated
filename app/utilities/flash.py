from fastapi import Request
import typing

def flash(request: Request, message: str, type: str = "success") -> None:
    if "_messages" not in request.session:
        request.session["_messages"] = []
    request.session["_messages"].append({"message": message, "type": type})


def get_flashed_messages(request: Request):
   return request.session.pop("_messages") if "_messages" in request.session else []
