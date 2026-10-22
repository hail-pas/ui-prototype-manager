from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import Response

from app.page_management import router

BRIDGE_VERSION = "v1"
BRIDGE_COMMAND_COOKIE = "uipm_preview_bridge"
BRIDGE_COMMAND_MAX_AGE = 30


def _core() -> Any:
    # Import lazily so app.main can remain the single application entry point.
    from app import main as core

    return core


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def create_bridge_token(project_id: str, page_id: str) -> str:
    message = f"uipm-preview-bridge-{BRIDGE_VERSION}:{project_id}:{page_id}".encode("utf-8")
    signature = hmac.new(_core().token_secret(), message, hashlib.sha256).digest()
    return _b64encode(signature)


def valid_bridge_token(project_id: str, page_id: str, token: str) -> bool:
    try:
        expected = create_bridge_token(project_id, page_id)
        return hmac.compare_digest(expected, str(token or ""))
    except Exception:
        return False


def _bridge_command(project_id: str, page_id: str) -> str:
    payload = json.dumps(
        {
            "type": "uipm-external-navigate",
            "projectId": project_id,
            "pageId": page_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _b64encode(payload)


@router.get("/api/pages/{page_id}/bridge-url", name="api_page_bridge_url")
def api_page_bridge_url(request: Request, page_id: str):
    core = _core()
    page = core.get_page(page_id)
    project_id = str(page["project_id"])
    token = create_bridge_token(project_id, page_id)
    bridge_url = request.url_for(
        "preview_bridge",
        project_id=project_id,
        page_id=page_id,
        token=token,
    )
    return {
        "project_id": project_id,
        "page_id": page_id,
        "bridge_url": str(bridge_url),
    }


@router.get(
    "/content/preview-bridge/{project_id}/{page_id}/{token}",
    response_class=Response,
    name="preview_bridge",
)
def preview_bridge(request: Request, project_id: str, page_id: str, token: str):
    # /content/* is intentionally public today. The stable HMAC limits a bridge URL
    # to one project/page pair without granting any authenticated application access.
    if not valid_bridge_token(project_id, page_id, token):
        raise HTTPException(404, "Bridge URL is invalid")

    core = _core()
    page = core.get_page(page_id)
    if str(page["project_id"]) != project_id:
        raise HTTPException(404, "Bridge URL is invalid")

    # A 204 navigation keeps the iframe's current active document in place. The
    # short-lived same-origin cookie is only a handoff signal to the parent Player;
    # it does not grant authentication or persist the target beyond this transition.
    response = Response(status_code=204)
    response.set_cookie(
        BRIDGE_COMMAND_COOKIE,
        _bridge_command(project_id, page_id),
        max_age=BRIDGE_COMMAND_MAX_AGE,
        path="/",
        secure=request.url.scheme == "https",
        httponly=False,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
