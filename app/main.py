from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
DATA_DIR = Path(os.getenv("UIPM_DATA_DIR", str(Path.cwd() / "data"))).expanduser().resolve()
DB_PATH = DATA_DIR / "app.db"
ASSET_DIR = DATA_DIR / "assets"
TOKEN_COOKIE = "uipm_token"
TOKEN_TTL_SECONDS = 24 * 60 * 60

app = FastAPI(title="UI Prototype Manager", version="0.4.0")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def access_key() -> str:
    key = os.getenv("UIPM_ACCESS_KEY", "").strip()
    if not key:
        raise RuntimeError("UIPM_ACCESS_KEY is required")
    return key


def token_secret() -> bytes:
    return hashlib.sha256(("uipm-token-v1:" + access_key()).encode("utf-8")).digest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_token() -> str:
    payload = json.dumps(
        {"exp": int(time.time()) + TOKEN_TTL_SECONDS, "nonce": secrets.token_urlsafe(12)},
        separators=(",", ":"),
    ).encode("utf-8")
    payload_b64 = _b64encode(payload)
    sig = hmac.new(token_secret(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64encode(sig)}"


def valid_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected = hmac.new(token_secret(), payload_b64.encode("ascii"), hashlib.sha256).digest()
        provided = _b64decode(sig_b64)
        if not hmac.compare_digest(expected, provided):
            return False
        payload = json.loads(_b64decode(payload_b64))
        exp = int(payload.get("exp", 0))
        now = int(time.time())
        return now < exp <= now + TOKEN_TTL_SECONDS + 60
    except Exception:
        return False


def wants_html(request: Request) -> bool:
    return not request.url.path.startswith("/api/")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    public = path == "/login" or path == "/api/auth/login" or path == "/health" or path.startswith("/static/")
    if public or valid_token(request.cookies.get(TOKEN_COOKIE)):
        return await call_next(request)
    if wants_html(request):
        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(url=f"/login?next={quote(target, safe='')}", status_code=303)
    return JSONResponse({"detail": "Authentication required"}, status_code=401)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pages (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('html', 'image')),
                storage_backend TEXT NOT NULL CHECK(storage_backend IN ('local', 's3')),
                storage_key TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS interactions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                source_page_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('navigate', 'back')),
                target_page_id TEXT,
                kind TEXT NOT NULL CHECK(kind IN ('element', 'region')),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CHECK ((action = 'back' AND target_page_id IS NULL) OR (action = 'navigate' AND target_page_id IS NOT NULL)),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(source_page_id) REFERENCES pages(id) ON DELETE CASCADE,
                FOREIGN KEY(target_page_id) REFERENCES pages(id) ON DELETE CASCADE
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_pages_project_name
                ON pages(project_id, name COLLATE NOCASE);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_interactions_project_name
                ON interactions(project_id, name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_pages_project ON pages(project_id, sort_order, created_at);
            CREATE INDEX IF NOT EXISTS idx_interactions_project ON interactions(project_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_interactions_source ON interactions(source_page_id);
            CREATE INDEX IF NOT EXISTS idx_interactions_target ON interactions(target_page_id);
            """
        )


@app.on_event("startup")
def startup() -> None:
    access_key()
    init_db()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def get_project(project_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Project not found")
    return row_to_dict(row)


def get_page(page_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Page not found")
    return row_to_dict(row)


def get_interaction(interaction_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM interactions WHERE id = ?", (interaction_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Interaction not found")
    item = row_to_dict(row)
    item["payload"] = json.loads(item.pop("payload_json"))
    return item


def clean_name(name: str, *, kind: str) -> str:
    value = re.sub(r"\s+", " ", str(name or "").strip())[:120]
    if not value:
        raise HTTPException(400, f"{kind} name is required")
    return value


def safe_filename(name: str) -> str:
    name = Path(name).name.strip() or "page"
    return re.sub(r"[^\w\-.()\u4e00-\u9fff ]+", "_", name)[:180]


def duplicate_error(entity: str, name: str) -> HTTPException:
    return HTTPException(409, f'{entity}名称“{name}”在当前项目中已存在')


def s3_settings() -> dict[str, str | None]:
    return {
        "bucket": os.getenv("UIPM_S3_BUCKET") or None,
        "endpoint_url": os.getenv("UIPM_S3_ENDPOINT_URL") or None,
        "region": os.getenv("UIPM_S3_REGION", "us-east-1"),
        "access_key": os.getenv("UIPM_S3_ACCESS_KEY_ID") or None,
        "secret_key": os.getenv("UIPM_S3_SECRET_ACCESS_KEY") or None,
        "prefix": os.getenv("UIPM_S3_PREFIX", "uipm").strip("/"),
        "addressing_style": os.getenv("UIPM_S3_ADDRESSING_STYLE", "path"),
    }


def s3_configured() -> bool:
    return bool(s3_settings()["bucket"])


def s3_client():
    if not s3_configured():
        raise RuntimeError("S3 is not configured. Set UIPM_S3_BUCKET first.")
    import boto3
    from botocore.config import Config

    cfg = s3_settings()
    kwargs: dict[str, Any] = {
        "region_name": cfg["region"],
        "config": Config(s3={"addressing_style": cfg["addressing_style"]}),
    }
    if cfg["endpoint_url"]:
        kwargs["endpoint_url"] = cfg["endpoint_url"]
    if cfg["access_key"]:
        kwargs["aws_access_key_id"] = cfg["access_key"]
    if cfg["secret_key"]:
        kwargs["aws_secret_access_key"] = cfg["secret_key"]
    return boto3.client("s3", **kwargs)


def asset_key(project_id: str, page_id: str, ext: str, backend: str) -> str:
    tail = f"{project_id}/{page_id}{ext}"
    if backend == "local":
        return f"assets/{tail}"
    prefix = str(s3_settings()["prefix"] or "").strip("/")
    return f"{prefix}/{tail}" if prefix else tail


def local_asset_path(key: str) -> Path:
    path = (DATA_DIR / key).resolve()
    if DATA_DIR not in path.parents:
        raise RuntimeError("Invalid local asset path")
    return path


def store_asset(*, backend: str, key: str, data: bytes, media_type: str) -> None:
    if backend == "local":
        path = local_asset_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return
    if backend == "s3":
        cfg = s3_settings()
        s3_client().put_object(Bucket=cfg["bucket"], Key=key, Body=data, ContentType=media_type)
        return
    raise RuntimeError(f"Unsupported storage backend: {backend}")


def read_asset(page: dict[str, Any]) -> bytes:
    backend = page["storage_backend"]
    key = page["storage_key"]
    if backend == "local":
        path = local_asset_path(key)
        if not path.exists():
            raise FileNotFoundError(str(path))
        return path.read_bytes()
    if backend == "s3":
        cfg = s3_settings()
        obj = s3_client().get_object(Bucket=cfg["bucket"], Key=key)
        return obj["Body"].read()
    raise FileNotFoundError(f"Unknown storage backend: {backend}")


def delete_asset(page: dict[str, Any]) -> None:
    backend = page.get("storage_backend")
    key = page.get("storage_key")
    if not key:
        return
    if backend == "local":
        try:
            local_asset_path(key).unlink(missing_ok=True)
        except OSError:
            pass
        return
    if backend == "s3" and s3_configured():
        try:
            cfg = s3_settings()
            s3_client().delete_object(Bucket=cfg["bucket"], Key=key)
        except Exception:
            pass


class LoginRequest(BaseModel):
    key: str


class ProjectCreate(BaseModel):
    name: str


class RenameRequest(BaseModel):
    name: str


class InteractionCreate(BaseModel):
    name: str
    source_page_id: str
    action: str
    target_page_id: str | None = None
    kind: str
    payload: dict[str, Any]


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if valid_token(request.cookies.get(TOKEN_COOKIE)):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html")


@app.post("/api/auth/login")
def api_login(payload: LoginRequest):
    if not hmac.compare_digest(payload.key, access_key()):
        raise HTTPException(401, "密钥错误")
    response = JSONResponse({"ok": True, "expires_in": TOKEN_TTL_SECONDS})
    response.set_cookie(
        TOKEN_COOKIE,
        create_token(),
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,
        secure=os.getenv("UIPM_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"},
        samesite="lax",
        path="/",
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(TOKEN_COOKIE, path="/")
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/project/{project_id}", response_class=HTMLResponse)
def editor(request: Request, project_id: str):
    project = get_project(project_id)
    return templates.TemplateResponse(request=request, name="editor.html", context={"project": project})


@app.get("/project/{project_id}/play", response_class=HTMLResponse)
def player(request: Request, project_id: str):
    project = get_project(project_id)
    return templates.TemplateResponse(request=request, name="player.html", context={"project": project})


@app.get("/api/config")
def api_config():
    cfg = s3_settings()
    return {
        "storage_backends": ["local", "s3"] if s3_configured() else ["local"],
        "default_storage_backend": "s3" if s3_configured() else "local",
        "data_dir": str(DATA_DIR),
        "s3": {
            "configured": s3_configured(),
            "bucket": cfg["bucket"] if s3_configured() else None,
            "endpoint_url": cfg["endpoint_url"] if s3_configured() else None,
        },
    }


@app.get("/api/projects")
def api_projects():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT p.*, COUNT(pg.id) AS page_count
            FROM projects p
            LEFT JOIN pages pg ON pg.project_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC
            """
        ).fetchall()
    return [row_to_dict(r) for r in rows]


@app.post("/api/projects")
def api_create_project(payload: ProjectCreate):
    name = clean_name(payload.name, kind="Project")
    project_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute("INSERT INTO projects(id, name, created_at) VALUES (?, ?, ?)", (project_id, name, now_iso()))
    return get_project(project_id)


@app.delete("/api/projects/{project_id}")
def api_delete_project(project_id: str):
    get_project(project_id)
    with db() as conn:
        pages = [row_to_dict(r) for r in conn.execute("SELECT * FROM pages WHERE project_id = ?", (project_id,)).fetchall()]
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    for page in pages:
        delete_asset(page)
    return {"ok": True}


@app.get("/api/projects/{project_id}")
def api_project(project_id: str):
    project = get_project(project_id)
    with db() as conn:
        pages = conn.execute(
            "SELECT * FROM pages WHERE project_id = ? ORDER BY sort_order, created_at", (project_id,)
        ).fetchall()
        interactions = conn.execute(
            "SELECT * FROM interactions WHERE project_id = ? ORDER BY created_at", (project_id,)
        ).fetchall()
    project["pages"] = [row_to_dict(r) for r in pages]
    parsed: list[dict[str, Any]] = []
    for row in interactions:
        item = row_to_dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        parsed.append(item)
    project["interactions"] = parsed
    return project


@app.post("/api/projects/{project_id}/pages")
async def api_upload_pages(
    project_id: str,
    storage_backend: str | None = Form(None),
    names_json: str | None = Form(None),
    files: list[UploadFile] = File(...),
):
    get_project(project_id)
    if not files:
        raise HTTPException(400, "No files uploaded")

    default_backend = "s3" if s3_configured() else "local"
    backend = (storage_backend or default_backend).strip().lower()

    if backend not in {"local", "s3"}:
        raise HTTPException(400, "storage_backend must be local or s3")
    if backend == "s3" and not s3_configured():
        raise HTTPException(400, "S3 is not configured on the server")

    requested_names: list[str] | None = None
    if names_json:
        try:
            raw_names = json.loads(names_json)
            if not isinstance(raw_names, list) or len(raw_names) != len(files):
                raise ValueError
            requested_names = [clean_name(v, kind="Page") for v in raw_names]
        except (ValueError, TypeError, json.JSONDecodeError):
            raise HTTPException(400, "names_json must be a JSON array matching uploaded files")

    prepared: list[dict[str, Any]] = []
    for idx, upload in enumerate(files):
        filename = safe_filename(upload.filename or "page")
        ext = Path(filename).suffix.lower()
        page_type = "html" if ext in {".html", ".htm"} else "image" if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else None
        if not page_type:
            raise HTTPException(400, f"Unsupported file type: {filename}")
        name = requested_names[idx] if requested_names else clean_name(Path(filename).stem, kind="Page")
        prepared.append({"upload": upload, "filename": filename, "ext": ext, "type": page_type, "name": name})

    folded = [item["name"].casefold() for item in prepared]
    if len(folded) != len(set(folded)):
        raise HTTPException(409, "本次上传的页面名称存在重复")
    with db() as conn:
        existing = {str(r["name"]).casefold() for r in conn.execute("SELECT name FROM pages WHERE project_id = ?", (project_id,)).fetchall()}
        conflict = next((item["name"] for item in prepared if item["name"].casefold() in existing), None)
        if conflict:
            raise duplicate_error("页面", conflict)
        row = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS n FROM pages WHERE project_id = ?", (project_id,)).fetchone()
        next_order = int(row["n"]) + 1

    stored: list[dict[str, Any]] = []
    try:
        for item in prepared:
            data = await item["upload"].read()
            if not data:
                raise HTTPException(400, f'{item["filename"]} is empty')
            max_bytes = 12 * 1024 * 1024 if item["type"] == "html" else 25 * 1024 * 1024
            if len(data) > max_bytes:
                raise HTTPException(413, f'{item["filename"]} is too large')
            page_id = str(uuid.uuid4())
            media_type = item["upload"].content_type or mimetypes.guess_type(item["filename"])[0] or "application/octet-stream"
            key = asset_key(project_id, page_id, item["ext"], backend)
            store_asset(backend=backend, key=key, data=data, media_type=media_type)
            stored.append({**item, "id": page_id, "key": key, "backend": backend})

        with db() as conn:
            for offset, item in enumerate(stored):
                conn.execute(
                    """
                    INSERT INTO pages(id, project_id, name, type, storage_backend, storage_key, sort_order, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (item["id"], project_id, item["name"], item["type"], backend, item["key"], next_order + offset, now_iso()),
                )
    except sqlite3.IntegrityError as exc:
        for item in stored:
            delete_asset({"storage_backend": item["backend"], "storage_key": item["key"]})
        raise HTTPException(409, "页面名称在当前项目中已存在") from exc
    except Exception:
        for item in stored:
            delete_asset({"storage_backend": item["backend"], "storage_key": item["key"]})
        raise

    return [get_page(item["id"]) for item in stored]


@app.patch("/api/pages/{page_id}")
def api_rename_page(page_id: str, payload: RenameRequest):
    page = get_page(page_id)
    name = clean_name(payload.name, kind="Page")
    try:
        with db() as conn:
            conn.execute("UPDATE pages SET name = ? WHERE id = ?", (name, page_id))
    except sqlite3.IntegrityError as exc:
        raise duplicate_error("页面", name) from exc
    return get_page(page_id)


@app.delete("/api/pages/{page_id}")
def api_delete_page(page_id: str):
    page = get_page(page_id)
    with db() as conn:
        conn.execute("DELETE FROM pages WHERE id = ?", (page_id,))
    delete_asset(page)
    return {"ok": True}


@app.get("/api/pages/{page_id}/file")
def api_page_file(page_id: str):
    page = get_page(page_id)
    if page["type"] != "image":
        raise HTTPException(400, "Not an image page")
    try:
        content = read_asset(page)
    except FileNotFoundError:
        raise HTTPException(404, "Asset not found")
    except Exception as exc:
        raise HTTPException(502, f"Failed to read asset: {exc}") from exc
    suffix = Path(page["storage_key"]).suffix
    media_type = mimetypes.guess_type(f"x{suffix}")[0] or "application/octet-stream"
    return Response(content=content, media_type=media_type, headers={"Cache-Control": "private, max-age=60"})


def injected_html(page_id: str, source: str, mode: str) -> str:
    source = re.sub(
        r"<meta[^>]+http-equiv=[\"']?Content-Security-Policy[\"']?[^>]*>",
        "",
        source,
        flags=re.IGNORECASE,
    )
    edit_mode = mode == "edit"
    css = """
<style id="__uipm_style">
html,body{min-height:100%;}
[data-ui-id]{cursor:pointer!important;}
""" + ("""
[data-ui-id]:hover{outline:2px solid #2563eb!important;outline-offset:2px!important;}
.__uipm_selected{outline:3px solid #2563eb!important;outline-offset:2px!important;}
""" if edit_mode else "") + "</style>"

    script = f"""
<script id="__uipm_script">
(() => {{
  const PAGE_ID = {json.dumps(page_id)};
  const EDIT_MODE = {str(edit_mode).lower()};
  function init() {{
    const ignored = new Set(['SCRIPT','STYLE','LINK','META','TITLE','BASE','NOSCRIPT']);
    const elements = Array.from(document.body ? document.body.querySelectorAll('*') : []).filter(el => !ignored.has(el.tagName));
    elements.forEach((el, i) => el.dataset.uiId = 'u' + (i + 1));
    document.addEventListener('click', (ev) => {{
      const raw = ev.target && ev.target.closest ? ev.target.closest('[data-ui-id]') : null;
      const semantic = ev.target && ev.target.closest ? ev.target.closest('a,button,input,select,textarea,label,[role="button"],[onclick]') : null;
      const target = semantic && semantic.dataset && semantic.dataset.uiId ? semantic : raw;
      if (!target) return;
      ev.preventDefault(); ev.stopPropagation();
      if (EDIT_MODE) {{
        document.querySelectorAll('.__uipm_selected').forEach(el => el.classList.remove('__uipm_selected'));
        target.classList.add('__uipm_selected');
      }}
      window.parent.postMessage({{
        type: 'uipm-element-click', pageId: PAGE_ID, elementId: target.dataset.uiId,
        tag: target.tagName.toLowerCase(),
        text: (target.innerText || target.getAttribute('aria-label') || target.getAttribute('title') || '').trim().replace(/\\s+/g,' ').slice(0,100)
      }}, '*');
    }}, true);
  }}
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {{once:true}}); else init();
}})();
</script>
"""
    injection = css + script
    if re.search(r"</body\s*>", source, flags=re.IGNORECASE):
        return re.sub(r"</body\s*>", lambda _m: injection + "</body>", source, count=1, flags=re.IGNORECASE)
    return source + injection


@app.get("/api/pages/{page_id}/render", response_class=HTMLResponse)
def api_render_html(page_id: str, mode: str = "edit"):
    page = get_page(page_id)
    if page["type"] != "html":
        raise HTTPException(400, "Not an HTML page")
    if mode not in {"edit", "play"}:
        mode = "edit"
    try:
        raw = read_asset(page)
    except FileNotFoundError:
        raise HTTPException(404, "Asset not found")
    except Exception as exc:
        raise HTTPException(502, f"Failed to read asset: {exc}") from exc
    text = None
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    return HTMLResponse(injected_html(page_id, text, mode), headers={"Cache-Control": "no-store"})


def normalize_interaction_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if kind == "element":
        element_id = str(payload.get("elementId", "")).strip()
        if not element_id:
            raise HTTPException(400, "elementId is required")
        return {"elementId": element_id}
    if kind == "region":
        keys = ("x", "y", "width", "height")
        try:
            vals = {k: float(payload[k]) for k in keys}
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Region requires x/y/width/height")
        for k, value in vals.items():
            if value < 0 or value > 1:
                raise HTTPException(400, f"{k} must be between 0 and 1")
        if vals["width"] <= 0 or vals["height"] <= 0:
            raise HTTPException(400, "Region must have positive size")
        return vals
    raise HTTPException(400, "Invalid interaction kind")


@app.post("/api/interactions")
def api_create_interaction(payload: InteractionCreate):
    name = clean_name(payload.name, kind="Interaction")
    source = get_page(payload.source_page_id)
    action = payload.action.strip().lower()
    if action not in {"navigate", "back"}:
        raise HTTPException(400, "action must be navigate or back")
    target_page_id: str | None = None
    if action == "navigate":
        if not payload.target_page_id:
            raise HTTPException(400, "target_page_id is required for navigate action")
        target = get_page(payload.target_page_id)
        if source["project_id"] != target["project_id"]:
            raise HTTPException(400, "Target page must be in the same project")
        target_page_id = target["id"]
    project_id = source["project_id"]
    normalized = normalize_interaction_payload(payload.kind, payload.payload)
    interaction_id = str(uuid.uuid4())

    with db() as conn:
        replacing_id: str | None = None
        if payload.kind == "element":
            rows = conn.execute(
                "SELECT id, payload_json FROM interactions WHERE source_page_id = ? AND kind = 'element'",
                (payload.source_page_id,),
            ).fetchall()
            for row in rows:
                try:
                    existing_payload = json.loads(row["payload_json"])
                except json.JSONDecodeError:
                    continue
                if existing_payload.get("elementId") == normalized["elementId"]:
                    replacing_id = row["id"]
                    break

        collision = conn.execute(
            "SELECT id FROM interactions WHERE project_id = ? AND name = ? COLLATE NOCASE AND (? IS NULL OR id <> ?)",
            (project_id, name, replacing_id, replacing_id),
        ).fetchone()
        if collision:
            raise duplicate_error("交互", name)
        if replacing_id:
            conn.execute("DELETE FROM interactions WHERE id = ?", (replacing_id,))
        try:
            conn.execute(
                """
                INSERT INTO interactions(id, project_id, name, source_page_id, action, target_page_id, kind, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (interaction_id, project_id, name, payload.source_page_id, action, target_page_id, payload.kind, json.dumps(normalized, ensure_ascii=False), now_iso()),
            )
        except sqlite3.IntegrityError as exc:
            raise duplicate_error("交互", name) from exc

    return {
        "id": interaction_id, "project_id": project_id, "name": name,
        "source_page_id": payload.source_page_id, "action": action, "target_page_id": target_page_id,
        "kind": payload.kind, "payload": normalized,
    }


@app.patch("/api/interactions/{interaction_id}")
def api_rename_interaction(interaction_id: str, payload: RenameRequest):
    interaction = get_interaction(interaction_id)
    name = clean_name(payload.name, kind="Interaction")
    try:
        with db() as conn:
            conn.execute("UPDATE interactions SET name = ? WHERE id = ?", (name, interaction_id))
    except sqlite3.IntegrityError as exc:
        raise duplicate_error("交互", name) from exc
    return get_interaction(interaction_id)


@app.delete("/api/interactions/{interaction_id}")
def api_delete_interaction(interaction_id: str):
    get_interaction(interaction_id)
    with db() as conn:
        conn.execute("DELETE FROM interactions WHERE id = ?", (interaction_id,))
    return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True, "db": str(DB_PATH), "s3_configured": s3_configured()}


def main() -> None:
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    try:
        port = int(os.getenv("PORT", "8080"))
    except ValueError as exc:
        raise SystemExit("PORT must be an integer") from exc
    uvicorn.run("app.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
