from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import stat
import struct
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.object_storage import (
    ObjectStorageConfigurationError,
    object_storage,
    object_storage_settings,
)

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
DATA_DIR = Path(os.getenv("UIPM_DATA_DIR", str(Path.cwd() / "data"))).expanduser().resolve()
DB_PATH = DATA_DIR / "app.db"
ASSET_DIR = DATA_DIR / "assets"
TOKEN_COOKIE = "uipm_token"
TOKEN_TTL_SECONDS = 24 * 60 * 60
DEFAULT_RENDER_MODE = "auto"
DEFAULT_VIEWPORT_WIDTH = 1920
DEFAULT_VIEWPORT_HEIGHT = 1080
RENDER_MODES = {"auto", "responsive", "fixed"}
HTML_INSTRUMENTATION_VERSION = 1
CONTENT_TOKEN_TTL_SECONDS = TOKEN_TTL_SECONDS
ZIP_MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
ZIP_MAX_EXTRACTED_BYTES = 3 * 1024 * 1024 * 1024
ZIP_MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
ZIP_MAX_FILES = 5000
ZIP_MAX_COMPRESSION_RATIO = 200
ZIP_MAX_PATH_LENGTH = 512
HTML_MAX_BYTES = 20 * 1024 * 1024
IMAGE_MAX_BYTES = 25 * 1024 * 1024
OVERLAY_IMAGE_MAX_BYTES = 25 * 1024 * 1024
OVERLAY_VIDEO_MAX_BYTES = 100 * 1024 * 1024
OVERLAY_DEFAULT_WIDTH = 0.3
OVERLAY_MAX_Z_INDEX = 1000
OVERLAY_MEDIA_TYPES = {
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".webp": ("image", "image/webp"),
    ".gif": ("image", "image/gif"),
    ".mp4": ("video", "video/mp4"),
    ".webm": ("video", "video/webm"),
}
STREAM_CHUNK_BYTES = 1024 * 1024

app = FastAPI(title="UI Prototype Manager", version="0.5.0")
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
    public = (
        path == "/login"
        or path == "/api/auth/login"
        or path == "/health"
        or path.startswith("/static/")
        or path.startswith("/content/")
        or path.startswith("/overlay-content/")
    )
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
                storage_prefix TEXT NOT NULL,
                entry_path TEXT NOT NULL,
                render_mode TEXT NOT NULL DEFAULT 'auto' CHECK(render_mode IN ('auto', 'responsive', 'fixed')),
                viewport_width INTEGER NOT NULL DEFAULT 1920,
                viewport_height INTEGER NOT NULL DEFAULT 1080,
                instrumentation_version INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS page_assets (
                page_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                PRIMARY KEY(page_id, relative_path),
                FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
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

            CREATE TABLE IF NOT EXISTS overlays (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                page_id TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('image', 'video')),
                storage_backend TEXT NOT NULL CHECK(storage_backend IN ('local', 's3')),
                storage_key TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
                x REAL NOT NULL CHECK(x >= 0 AND x <= 1),
                y REAL NOT NULL CHECK(y >= 0 AND y <= 1),
                width REAL NOT NULL CHECK(width > 0 AND width <= 1),
                height REAL NOT NULL CHECK(height > 0 AND height <= 1),
                aspect_ratio REAL NOT NULL CHECK(aspect_ratio > 0),
                object_fit TEXT NOT NULL DEFAULT 'cover' CHECK(object_fit IN ('contain', 'cover')),
                z_index INTEGER NOT NULL DEFAULT 0 CHECK(z_index >= 0 AND z_index <= 1000),
                video_controls INTEGER NOT NULL DEFAULT 1 CHECK(video_controls IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK(x + width <= 1.000000001),
                CHECK(y + height <= 1.000000001),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_pages_project_name
                ON pages(project_id, name COLLATE NOCASE);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_interactions_project_name
                ON interactions(project_id, name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_pages_project ON pages(project_id, sort_order, created_at);
            CREATE INDEX IF NOT EXISTS idx_interactions_project ON interactions(project_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_interactions_source ON interactions(source_page_id);
            CREATE INDEX IF NOT EXISTS idx_interactions_target ON interactions(target_page_id);
            CREATE INDEX IF NOT EXISTS idx_overlays_page
                ON overlays(page_id, z_index, created_at);
            """
        )
        page_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(pages)").fetchall()
        }
        required_columns = {
            "storage_prefix",
            "entry_path",
            "instrumentation_version",
        }
        if not required_columns.issubset(page_columns):
            raise RuntimeError(
                f"Database schema is incompatible; remove {DB_PATH} and restart"
            )


@app.on_event("startup")
def startup() -> None:
    access_key()
    object_storage_settings().validate()
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


def get_overlay(overlay_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM overlays WHERE id = ?", (overlay_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Overlay not found")
    return row_to_dict(row)


def clean_name(name: str, *, kind: str) -> str:
    value = re.sub(r"\s+", " ", str(name or "").strip())[:120]
    if not value:
        raise HTTPException(400, f"{kind} name is required")
    return value


def normalize_render_settings(
    *,
    render_mode: str | None,
    viewport_width: int | None,
    viewport_height: int | None,
) -> tuple[str, int, int]:
    mode = str(render_mode or DEFAULT_RENDER_MODE).strip().lower()
    if mode not in RENDER_MODES:
        raise HTTPException(400, "render_mode must be auto, responsive or fixed")
    width = DEFAULT_VIEWPORT_WIDTH if viewport_width is None else int(viewport_width)
    height = DEFAULT_VIEWPORT_HEIGHT if viewport_height is None else int(viewport_height)
    if width < 240 or width > 10000:
        raise HTTPException(400, "viewport_width must be between 240 and 10000")
    if height < 240 or height > 10000:
        raise HTTPException(400, "viewport_height must be between 240 and 10000")
    return mode, width, height


def safe_filename(name: str) -> str:
    name = Path(name).name.strip() or "page"
    return re.sub(r"[^\w\-.()\u4e00-\u9fff ]+", "_", name)[:180]


def duplicate_error(entity: str, name: str) -> HTTPException:
    return HTTPException(409, f'{entity}名称“{name}”在当前项目中已存在')


def s3_configured() -> bool:
    return object_storage_settings().configured


def page_storage_prefix(project_id: str, page_id: str, backend: str) -> str:
    tail = f"{project_id}/{page_id}"
    if backend == "local":
        return f"assets/{tail}"
    prefix = object_storage_settings().prefix
    return f"{prefix}/{tail}" if prefix else tail


def overlay_storage_key(
    project_id: str, overlay_id: str, suffix: str, backend: str
) -> str:
    tail = f"{project_id}/overlays/{overlay_id}{suffix}"
    if backend == "local":
        return f"assets/{tail}"
    prefix = object_storage_settings().prefix
    return f"{prefix}/{tail}" if prefix else tail


def local_asset_path(key: str) -> Path:
    path = (DATA_DIR / key).resolve()
    if path == DATA_DIR or DATA_DIR not in path.parents:
        raise RuntimeError("Invalid local asset path")
    return path


def asset_storage_key(page: dict[str, Any], relative_path: str) -> str:
    return f'{str(page["storage_prefix"]).rstrip("/")}/{relative_path}'


def store_asset_stream(
    *,
    backend: str,
    key: str,
    fileobj: BinaryIO,
    size: int,
    media_type: str,
) -> None:
    fileobj.seek(0)
    if backend == "local":
        path = local_asset_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as output:
            shutil.copyfileobj(fileobj, output, length=STREAM_CHUNK_BYTES)
        return
    if backend == "s3":
        object_storage().put_fileobj(
            key,
            fileobj,
            size=size,
            media_type=media_type,
        )
        return
    raise RuntimeError(f"Unsupported storage backend: {backend}")


def store_asset_bytes(*, backend: str, key: str, data: bytes, media_type: str) -> None:
    store_asset_stream(
        backend=backend,
        key=key,
        fileobj=io.BytesIO(data),
        size=len(data),
        media_type=media_type,
    )


def read_page_asset(page: dict[str, Any], relative_path: str | None = None) -> bytes:
    backend = page["storage_backend"]
    key = asset_storage_key(page, relative_path or page["entry_path"])
    if backend == "local":
        path = local_asset_path(key)
        if not path.exists():
            raise FileNotFoundError(str(path))
        return path.read_bytes()
    if backend == "s3":
        return object_storage().read(key)
    raise FileNotFoundError(f"Unknown storage backend: {backend}")


def page_asset_paths(page_id: str) -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT relative_path FROM page_assets WHERE page_id = ? ORDER BY relative_path",
            (page_id,),
        ).fetchall()
    return [str(row["relative_path"]) for row in rows]


def delete_asset_package(
    page: dict[str, Any], relative_paths: list[str] | None = None
) -> None:
    backend = page.get("storage_backend")
    prefix = page.get("storage_prefix")
    if not prefix:
        return
    if backend == "local":
        try:
            shutil.rmtree(local_asset_path(str(prefix)), ignore_errors=True)
        except OSError:
            pass
        return
    if backend == "s3" and s3_configured():
        try:
            paths = relative_paths
            if paths is None and page.get("id"):
                paths = page_asset_paths(str(page["id"]))
            keys = [f'{str(prefix).rstrip("/")}/{path}' for path in paths or []]
            object_storage().delete_many(keys)
        except Exception:
            pass


def delete_overlay_asset(
    overlay: dict[str, Any], *, suppress_errors: bool = False
) -> None:
    try:
        backend = str(overlay["storage_backend"])
        key = str(overlay["storage_key"])
        if backend == "local":
            path = local_asset_path(key)
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
            return
        if backend == "s3":
            if not s3_configured():
                raise ObjectStorageConfigurationError("S3 is not configured on the server")
            object_storage().delete(key)
            return
        raise RuntimeError(f"Unsupported storage backend: {backend}")
    except Exception:
        if not suppress_errors:
            raise


def create_content_token(page_id: str) -> tuple[str, int]:
    expires_at = int(time.time()) + CONTENT_TOKEN_TTL_SECONDS
    message = f"uipm-content-v1:{page_id}:{expires_at}".encode("utf-8")
    signature = hmac.new(token_secret(), message, hashlib.sha256).digest()
    return f"{expires_at}.{_b64encode(signature)}", expires_at


def valid_content_token(page_id: str, token: str) -> bool:
    if "." not in token:
        return False
    try:
        expires_raw, signature_raw = token.split(".", 1)
        expires_at = int(expires_raw)
        now = int(time.time())
        if now >= expires_at or expires_at > now + CONTENT_TOKEN_TTL_SECONDS + 60:
            return False
        message = f"uipm-content-v1:{page_id}:{expires_at}".encode("utf-8")
        expected = hmac.new(token_secret(), message, hashlib.sha256).digest()
        return hmac.compare_digest(expected, _b64decode(signature_raw))
    except Exception:
        return False


def page_to_api(page: dict[str, Any]) -> dict[str, Any]:
    item = dict(page)
    token, expires_at = create_content_token(str(item["id"]))
    entry_path = quote(str(item["entry_path"]), safe="/")
    item["content_url"] = f'/content/{item["id"]}/{token}/{entry_path}'
    item["content_url_expires_at"] = datetime.fromtimestamp(
        expires_at, tz=timezone.utc
    ).isoformat()
    return item


def overlay_to_api(overlay: dict[str, Any]) -> dict[str, Any]:
    item = dict(overlay)
    item["video_controls"] = bool(item.get("video_controls"))
    token, expires_at = create_content_token(f'overlay:{item["id"]}')
    asset_name = quote(Path(str(item["storage_key"])).name, safe="")
    item["content_url"] = f'/overlay-content/{item["id"]}/{token}/{asset_name}'
    item["content_url_expires_at"] = datetime.fromtimestamp(
        expires_at, tz=timezone.utc
    ).isoformat()
    return item


class LoginRequest(BaseModel):
    key: str


class ProjectCreate(BaseModel):
    name: str


class PageUpdate(BaseModel):
    name: str | None = None
    render_mode: str | None = None
    viewport_width: int | None = None
    viewport_height: int | None = None


class InteractionCreate(BaseModel):
    name: str
    source_page_id: str
    action: str
    target_page_id: str | None = None
    kind: str
    payload: dict[str, Any]


class InteractionUpdate(BaseModel):
    name: str | None = None
    action: str | None = None
    target_page_id: str | None = None


class OverlayUpdate(BaseModel):
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    object_fit: str | None = None
    z_index: int | None = None
    video_controls: bool | None = None


@dataclass(frozen=True)
class ArchiveMember:
    info: zipfile.ZipInfo
    relative_path: str
    media_type: str


def file_size(fileobj: BinaryIO) -> int:
    position = fileobj.tell()
    fileobj.seek(0, os.SEEK_END)
    size = fileobj.tell()
    fileobj.seek(position)
    return size


def _positive_dimensions(width: int, height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0 or width > 100_000 or height > 100_000:
        raise ValueError("Media dimensions are invalid")
    return width, height


def probe_image_dimensions(fileobj: BinaryIO, suffix: str) -> tuple[int, int]:
    original_position = fileobj.tell()
    try:
        fileobj.seek(0)
        header = fileobj.read(32)
        if suffix == ".png":
            if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
                raise ValueError("Invalid PNG file")
            return _positive_dimensions(*struct.unpack(">II", header[16:24]))

        if suffix == ".gif":
            if header[:6] not in {b"GIF87a", b"GIF89a"}:
                raise ValueError("Invalid GIF file")
            width, height = struct.unpack("<HH", header[6:10])
            return _positive_dimensions(width, height)

        if suffix in {".jpg", ".jpeg"}:
            if header[:2] != b"\xff\xd8":
                raise ValueError("Invalid JPEG file")
            fileobj.seek(2)
            sof_markers = {
                0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
            }
            while True:
                prefix = fileobj.read(1)
                if not prefix:
                    break
                if prefix != b"\xff":
                    continue
                marker_raw = fileobj.read(1)
                while marker_raw == b"\xff":
                    marker_raw = fileobj.read(1)
                if not marker_raw:
                    break
                marker = marker_raw[0]
                if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                    continue
                length_raw = fileobj.read(2)
                if len(length_raw) != 2:
                    break
                segment_length = struct.unpack(">H", length_raw)[0]
                if segment_length < 2:
                    raise ValueError("Invalid JPEG segment")
                if marker in sof_markers:
                    dimensions = fileobj.read(5)
                    if len(dimensions) != 5:
                        break
                    height, width = struct.unpack(">HH", dimensions[1:5])
                    return _positive_dimensions(width, height)
                fileobj.seek(segment_length - 2, os.SEEK_CUR)
            raise ValueError("JPEG dimensions were not found")

        if suffix == ".webp":
            if header[:4] != b"RIFF" or header[8:12] != b"WEBP":
                raise ValueError("Invalid WebP file")
            fileobj.seek(12)
            total_size = file_size(fileobj)
            while fileobj.tell() + 8 <= total_size:
                chunk_type = fileobj.read(4)
                chunk_size_raw = fileobj.read(4)
                if len(chunk_type) != 4 or len(chunk_size_raw) != 4:
                    break
                chunk_size = struct.unpack("<I", chunk_size_raw)[0]
                payload_start = fileobj.tell()
                if chunk_type == b"VP8X" and chunk_size >= 10:
                    payload = fileobj.read(10)
                    width = 1 + int.from_bytes(payload[4:7], "little")
                    height = 1 + int.from_bytes(payload[7:10], "little")
                    return _positive_dimensions(width, height)
                if chunk_type == b"VP8 " and chunk_size >= 10:
                    payload = fileobj.read(10)
                    if payload[3:6] != b"\x9d\x01\x2a":
                        raise ValueError("Invalid WebP VP8 frame")
                    width = int.from_bytes(payload[6:8], "little") & 0x3FFF
                    height = int.from_bytes(payload[8:10], "little") & 0x3FFF
                    return _positive_dimensions(width, height)
                if chunk_type == b"VP8L" and chunk_size >= 5:
                    payload = fileobj.read(5)
                    if payload[0] != 0x2F:
                        raise ValueError("Invalid WebP VP8L frame")
                    bits = int.from_bytes(payload[1:5], "little")
                    width = (bits & 0x3FFF) + 1
                    height = ((bits >> 14) & 0x3FFF) + 1
                    return _positive_dimensions(width, height)
                next_chunk = payload_start + chunk_size + (chunk_size % 2)
                if next_chunk > total_size:
                    break
                fileobj.seek(next_chunk)
            raise ValueError("WebP dimensions were not found")

        raise ValueError("Unsupported image type")
    finally:
        fileobj.seek(original_position)


def _iter_mp4_boxes(
    fileobj: BinaryIO, start: int, end: int
) -> Iterator[tuple[bytes, int, int]]:
    cursor = start
    while cursor + 8 <= end:
        fileobj.seek(cursor)
        header = fileobj.read(8)
        if len(header) != 8:
            return
        size = struct.unpack(">I", header[:4])[0]
        box_type = header[4:8]
        header_size = 8
        if size == 1:
            extended = fileobj.read(8)
            if len(extended) != 8:
                return
            size = struct.unpack(">Q", extended)[0]
            header_size = 16
        elif size == 0:
            size = end - cursor
        if size < header_size or cursor + size > end:
            return
        yield box_type, cursor + header_size, cursor + size
        cursor += size


def _mp4_video_track_dimensions(
    fileobj: BinaryIO, track_start: int, track_end: int
) -> tuple[int, int] | None:
    track_header: tuple[int, int] | None = None
    media_box: tuple[int, int] | None = None
    for box_type, payload_start, box_end in _iter_mp4_boxes(
        fileobj, track_start, track_end
    ):
        if box_type == b"tkhd":
            track_header = (payload_start, box_end)
        elif box_type == b"mdia":
            media_box = (payload_start, box_end)
    if not track_header or not media_box:
        return None

    is_video = False
    for box_type, payload_start, box_end in _iter_mp4_boxes(
        fileobj, media_box[0], media_box[1]
    ):
        if box_type != b"hdlr" or box_end - payload_start < 12:
            continue
        fileobj.seek(payload_start + 8)
        is_video = fileobj.read(4) == b"vide"
        break
    if not is_video:
        return None

    payload_start, box_end = track_header
    if box_end - payload_start < 8:
        return None
    fileobj.seek(box_end - 8)
    raw = fileobj.read(8)
    if len(raw) != 8:
        return None
    width_fixed, height_fixed = struct.unpack(">II", raw)
    width = round(width_fixed / 65536)
    height = round(height_fixed / 65536)
    try:
        return _positive_dimensions(width, height)
    except ValueError:
        return None


def probe_mp4_dimensions(fileobj: BinaryIO) -> tuple[int, int]:
    original_position = fileobj.tell()
    try:
        total_size = file_size(fileobj)
        moov: tuple[int, int] | None = None
        has_mp4_signature = False
        for box_type, payload_start, box_end in _iter_mp4_boxes(fileobj, 0, total_size):
            if box_type in {b"ftyp", b"moov"}:
                has_mp4_signature = True
            if box_type == b"moov":
                moov = (payload_start, box_end)
        if not has_mp4_signature or not moov:
            raise ValueError("Invalid MP4 file")
        for box_type, payload_start, box_end in _iter_mp4_boxes(
            fileobj, moov[0], moov[1]
        ):
            if box_type != b"trak":
                continue
            dimensions = _mp4_video_track_dimensions(fileobj, payload_start, box_end)
            if dimensions:
                return dimensions
        raise ValueError("MP4 video dimensions were not found")
    finally:
        fileobj.seek(original_position)


def _ebml_vint(data: bytes, offset: int) -> tuple[int, int] | None:
    if offset >= len(data) or data[offset] == 0:
        return None
    marker = 0x80
    length = 1
    while length <= 8 and not data[offset] & marker:
        marker >>= 1
        length += 1
    if length > 8 or offset + length > len(data):
        return None
    value = data[offset] & (marker - 1)
    for byte in data[offset + 1:offset + length]:
        value = (value << 8) | byte
    return value, length


def _webm_uint_element(data: bytes, element_id: int) -> int | None:
    needle = bytes((element_id,))
    cursor = 0
    while True:
        cursor = data.find(needle, cursor)
        if cursor < 0:
            return None
        size_info = _ebml_vint(data, cursor + 1)
        if size_info:
            size, size_length = size_info
            value_start = cursor + 1 + size_length
            value_end = value_start + size
            if 0 < size <= 4 and value_end <= len(data):
                value = int.from_bytes(data[value_start:value_end], "big")
                if 0 < value <= 100_000:
                    return value
        cursor += 1


def probe_webm_dimensions(fileobj: BinaryIO) -> tuple[int, int]:
    original_position = fileobj.tell()
    try:
        fileobj.seek(0)
        data = fileobj.read(min(file_size(fileobj), 8 * 1024 * 1024))
        if data[:4] != b"\x1a\x45\xdf\xa3":
            raise ValueError("Invalid WebM file")
        video_cursor = 0
        while True:
            video_cursor = data.find(b"\xe0", video_cursor)
            if video_cursor < 0:
                break
            size_info = _ebml_vint(data, video_cursor + 1)
            if size_info:
                size, size_length = size_info
                payload_start = video_cursor + 1 + size_length
                payload_end = min(len(data), payload_start + size)
                payload = data[payload_start:payload_end]
                width = _webm_uint_element(payload, 0xB0)
                height = _webm_uint_element(payload, 0xBA)
                if width and height:
                    return _positive_dimensions(width, height)
            video_cursor += 1
        raise ValueError("WebM video dimensions were not found")
    finally:
        fileobj.seek(original_position)


def probe_overlay_dimensions(
    fileobj: BinaryIO, *, overlay_type: str, suffix: str
) -> tuple[int, int]:
    if overlay_type == "image":
        return probe_image_dimensions(fileobj, suffix)
    if suffix == ".mp4":
        return probe_mp4_dimensions(fileobj)
    if suffix == ".webm":
        return probe_webm_dimensions(fileobj)
    raise ValueError("Unsupported video type")


def inspect_overlay_upload(
    upload: UploadFile, filename: str
) -> tuple[str, str, str, int, int, int]:
    suffix = Path(filename).suffix.lower()
    media_definition = OVERLAY_MEDIA_TYPES.get(suffix)
    if not media_definition:
        raise HTTPException(400, f"Unsupported overlay file type: {filename}")
    overlay_type, media_type = media_definition
    claimed_type = str(upload.content_type or "").split(";", 1)[0].strip().lower()
    allowed_claimed_types = {media_type, "application/octet-stream"}
    if media_type == "image/jpeg":
        allowed_claimed_types.update({"image/jpg", "image/pjpeg"})
    if media_type == "video/mp4":
        allowed_claimed_types.add("application/mp4")
    if claimed_type and claimed_type not in allowed_claimed_types:
        raise HTTPException(400, "File extension and MIME type do not match")

    upload.file.seek(0)
    size = file_size(upload.file)
    upload.file.seek(0)
    if size <= 0:
        raise HTTPException(400, f"{filename} is empty")
    max_bytes = (
        OVERLAY_IMAGE_MAX_BYTES if overlay_type == "image" else OVERLAY_VIDEO_MAX_BYTES
    )
    if size > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise HTTPException(413, f"{filename} exceeds the {limit_mb} MB overlay limit")
    try:
        width, height = probe_overlay_dimensions(
            upload.file, overlay_type=overlay_type, suffix=suffix
        )
    except (OSError, ValueError, struct.error) as exc:
        raise HTTPException(400, f"{filename} is not a valid {overlay_type} file") from exc
    upload.file.seek(0)
    return overlay_type, media_type, suffix, size, width, height


def page_canvas_dimensions(page: dict[str, Any]) -> tuple[int, int]:
    if page["type"] == "image":
        try:
            raw = read_page_asset(page)
            return probe_image_dimensions(
                io.BytesIO(raw), Path(str(page["entry_path"])).suffix.lower()
            )
        except (FileNotFoundError, OSError, ValueError, struct.error):
            pass
    return (
        int(page.get("viewport_width") or DEFAULT_VIEWPORT_WIDTH),
        int(page.get("viewport_height") or DEFAULT_VIEWPORT_HEIGHT),
    )


def default_overlay_geometry(
    page: dict[str, Any], aspect_ratio: float
) -> dict[str, float]:
    if not math.isfinite(aspect_ratio) or aspect_ratio <= 0:
        raise HTTPException(400, "aspect_ratio must be positive")
    page_width, page_height = page_canvas_dimensions(page)
    width = OVERLAY_DEFAULT_WIDTH
    height = width * page_width / (aspect_ratio * page_height)
    if height > 1:
        height = 1.0
        width = min(1.0, height * aspect_ratio * page_height / page_width)
    return {
        "x": (1 - width) / 2,
        "y": (1 - height) / 2,
        "width": width,
        "height": height,
    }


def normalize_overlay_geometry(values: dict[str, Any]) -> dict[str, float]:
    try:
        geometry = {
            field: float(values[field]) for field in ("x", "y", "width", "height")
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, "Overlay requires x/y/width/height") from exc
    if not all(math.isfinite(value) for value in geometry.values()):
        raise HTTPException(400, "Overlay geometry must be finite")
    if geometry["x"] < 0 or geometry["x"] > 1:
        raise HTTPException(400, "x must be between 0 and 1")
    if geometry["y"] < 0 or geometry["y"] > 1:
        raise HTTPException(400, "y must be between 0 and 1")
    if geometry["width"] <= 0 or geometry["width"] > 1:
        raise HTTPException(400, "width must be greater than 0 and at most 1")
    if geometry["height"] <= 0 or geometry["height"] > 1:
        raise HTTPException(400, "height must be greater than 0 and at most 1")
    if geometry["x"] + geometry["width"] > 1.000000001:
        raise HTTPException(400, "Overlay exceeds the page's right edge")
    if geometry["y"] + geometry["height"] > 1.000000001:
        raise HTTPException(400, "Overlay exceeds the page's bottom edge")
    return geometry


def media_type_for_path(relative_path: str) -> str:
    suffix = PurePosixPath(relative_path).suffix.lower()
    overrides = {
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".htm": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".map": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".m3u8": "application/vnd.apple.mpegurl",
    }
    return overrides.get(
        suffix,
        mimetypes.guess_type(relative_path)[0] or "application/octet-stream",
    )


def validate_relative_asset_path(raw_path: str) -> str:
    if not raw_path or "\x00" in raw_path or "\\" in raw_path:
        raise HTTPException(400, f"ZIP contains an invalid path: {raw_path!r}")
    if len(raw_path) > ZIP_MAX_PATH_LENGTH or re.match(r"^[A-Za-z]:", raw_path):
        raise HTTPException(400, f"ZIP path is not allowed: {raw_path}")
    if raw_path.startswith("/") or "//" in raw_path:
        raise HTTPException(400, f"ZIP path is not relative: {raw_path}")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(400, f"ZIP path is not safe: {raw_path}")
    if any(len(part) > 255 for part in path.parts):
        raise HTTPException(400, f"ZIP path segment is too long: {raw_path}")
    return path.as_posix()


def is_ignored_archive_path(relative_path: str) -> bool:
    parts = PurePosixPath(relative_path).parts
    return bool(parts) and (parts[0] == "__MACOSX" or parts[-1] == ".DS_Store")


def inspect_zip(upload: UploadFile, filename: str) -> tuple[zipfile.ZipFile, list[ArchiveMember]]:
    upload.file.seek(0)
    upload_bytes = file_size(upload.file)
    upload.file.seek(0)
    if upload_bytes == 0:
        raise HTTPException(400, f"{filename} is empty")
    if upload_bytes > ZIP_MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{filename} exceeds the ZIP upload limit")
    try:
        archive = zipfile.ZipFile(upload.file)
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise HTTPException(400, f"{filename} is not a valid ZIP archive") from exc

    raw_members: list[tuple[zipfile.ZipInfo, str]] = []
    try:
        for info in archive.infolist():
            path = validate_relative_asset_path(info.filename.rstrip("/"))
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(unix_mode):
                raise HTTPException(400, f"ZIP symbolic links are not allowed: {path}")
            if info.flag_bits & 0x1:
                raise HTTPException(400, f"Encrypted ZIP entries are not allowed: {path}")
            if info.is_dir() or is_ignored_archive_path(path):
                continue
            raw_members.append((info, path))

        if not raw_members:
            raise HTTPException(400, f"{filename} does not contain any files")

        raw_paths = [path for _, path in raw_members]
        if "index.html" in raw_paths:
            wrapper: str | None = None
        else:
            roots = {PurePosixPath(path).parts[0] for path in raw_paths}
            wrapper = next(iter(roots)) if len(roots) == 1 else None
            if wrapper is None or f"{wrapper}/index.html" not in raw_paths:
                raise HTTPException(
                    400,
                    f"{filename} must contain index.html at its root or inside one wrapper directory",
                )

        normalized: list[tuple[zipfile.ZipInfo, str]] = []
        seen: set[str] = set()
        total_size = 0
        total_compressed = 0
        for info, raw_path in raw_members:
            path = raw_path
            if wrapper:
                prefix = f"{wrapper}/"
                if not path.startswith(prefix):
                    raise HTTPException(400, f"ZIP wrapper directory is inconsistent: {path}")
                path = path[len(prefix) :]
            path = validate_relative_asset_path(path)
            folded = path.casefold()
            if folded in seen:
                raise HTTPException(400, f"ZIP contains duplicate paths: {path}")
            seen.add(folded)
            if info.file_size > ZIP_MAX_FILE_BYTES:
                raise HTTPException(413, f"ZIP entry is too large: {path}")
            if info.file_size and info.file_size / max(1, info.compress_size) > ZIP_MAX_COMPRESSION_RATIO:
                raise HTTPException(413, f"ZIP entry compression ratio is too high: {path}")
            total_size += info.file_size
            total_compressed += info.compress_size
            normalized.append((info, path))

        if len(normalized) > ZIP_MAX_FILES:
            raise HTTPException(413, f"{filename} contains too many files")
        if total_size > ZIP_MAX_EXTRACTED_BYTES:
            raise HTTPException(413, f"{filename} exceeds the extracted size limit")
        if total_size and total_size / max(1, total_compressed) > ZIP_MAX_COMPRESSION_RATIO:
            raise HTTPException(413, f"{filename} compression ratio is too high")

        path_set = {path.casefold() for _, path in normalized}
        for _, path in normalized:
            parent = PurePosixPath(path).parent
            while parent != PurePosixPath("."):
                if parent.as_posix().casefold() in path_set:
                    raise HTTPException(400, f"ZIP contains a file/directory conflict: {path}")
                parent = parent.parent

        html_paths = [
            path
            for _, path in normalized
            if PurePosixPath(path).suffix.casefold() in {".html", ".htm"}
        ]
        if html_paths != ["index.html"]:
            raise HTTPException(
                400,
                f"{filename} must contain exactly one HTML file named index.html",
            )

        members = [
            ArchiveMember(info=info, relative_path=path, media_type=media_type_for_path(path))
            for info, path in normalized
        ]
        return archive, members
    except Exception:
        archive.close()
        raise


def copy_to_spooled_file(
    source: BinaryIO, *, expected_size: int, relative_path: str
) -> tuple[BinaryIO, int]:
    output = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b")
    total = 0
    try:
        while True:
            chunk = source.read(STREAM_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > ZIP_MAX_FILE_BYTES:
                raise HTTPException(413, f"ZIP entry is too large: {relative_path}")
            output.write(chunk)
        if total != expected_size:
            raise HTTPException(400, f"ZIP entry size is invalid: {relative_path}")
        output.seek(0)
        return output, total
    except Exception:
        output.close()
        raise


def store_uploaded_page(
    *,
    upload: UploadFile,
    filename: str,
    page_type: str,
    source_kind: str,
    project_id: str,
    page_id: str,
    backend: str,
) -> dict[str, Any]:
    prefix = page_storage_prefix(project_id, page_id, backend)
    page_stub = {
        "id": page_id,
        "storage_backend": backend,
        "storage_prefix": prefix,
    }
    assets: list[dict[str, Any]] = []
    stored_paths: list[str] = []
    archive: zipfile.ZipFile | None = None
    try:
        if source_kind == "zip":
            archive, members = inspect_zip(upload, filename)
            for member in members:
                try:
                    with archive.open(member.info, "r") as source:
                        spool, size = copy_to_spooled_file(
                            source,
                            expected_size=member.info.file_size,
                            relative_path=member.relative_path,
                        )
                except (zipfile.BadZipFile, RuntimeError) as exc:
                    raise HTTPException(
                        400, f"ZIP entry failed integrity validation: {member.relative_path}"
                    ) from exc
                try:
                    media_type = member.media_type
                    if member.relative_path == "index.html":
                        raw = spool.read(HTML_MAX_BYTES + 1)
                        if len(raw) > HTML_MAX_BYTES:
                            raise HTTPException(413, "index.html is too large")
                        prepared = prepare_html_asset(page_id, raw)
                        spool.close()
                        spool = io.BytesIO(prepared)
                        size = len(prepared)
                        media_type = "text/html; charset=utf-8"
                    key = f"{prefix}/{member.relative_path}"
                    store_asset_stream(
                        backend=backend,
                        key=key,
                        fileobj=spool,
                        size=size,
                        media_type=media_type,
                    )
                    stored_paths.append(member.relative_path)
                    assets.append(
                        {
                            "relative_path": member.relative_path,
                            "media_type": media_type,
                            "size_bytes": size,
                        }
                    )
                finally:
                    spool.close()
            entry_path = "index.html"
        else:
            upload.file.seek(0)
            size = file_size(upload.file)
            upload.file.seek(0)
            if size == 0:
                raise HTTPException(400, f"{filename} is empty")
            max_bytes = HTML_MAX_BYTES if page_type == "html" else IMAGE_MAX_BYTES
            if size > max_bytes:
                raise HTTPException(413, f"{filename} is too large")
            if page_type == "html":
                prepared = prepare_html_asset(page_id, upload.file.read())
                fileobj: BinaryIO = io.BytesIO(prepared)
                size = len(prepared)
                entry_path = "index.html"
                media_type = "text/html; charset=utf-8"
            else:
                fileobj = upload.file
                suffix = Path(filename).suffix.lower()
                entry_path = f"image{suffix}"
                media_type = media_type_for_path(entry_path)
            store_asset_stream(
                backend=backend,
                key=f"{prefix}/{entry_path}",
                fileobj=fileobj,
                size=size,
                media_type=media_type,
            )
            stored_paths.append(entry_path)
            assets.append(
                {
                    "relative_path": entry_path,
                    "media_type": media_type,
                    "size_bytes": size,
                }
            )

        return {
            "id": page_id,
            "storage_prefix": prefix,
            "entry_path": entry_path,
            "assets": assets,
            "storage_backend": backend,
        }
    except Exception:
        delete_asset_package(page_stub, stored_paths)
        raise
    finally:
        if archive is not None:
            archive.close()


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
    cfg = object_storage_settings()
    return {
        "storage_backends": ["local", "s3"] if s3_configured() else ["local"],
        "default_storage_backend": "s3" if s3_configured() else "local",
        "data_dir": str(DATA_DIR),
        "html_render_defaults": {
            "render_mode": DEFAULT_RENDER_MODE,
            "viewport_width": DEFAULT_VIEWPORT_WIDTH,
            "viewport_height": DEFAULT_VIEWPORT_HEIGHT,
        },
        "s3": {
            "configured": s3_configured(),
            "provider": cfg.provider if s3_configured() else None,
            "bucket": cfg.bucket if s3_configured() else None,
            "endpoint_url": cfg.endpoint_url if s3_configured() else None,
            "browser_endpoint_url": cfg.browser_endpoint_url if s3_configured() else None,
            "direct_read": cfg.direct_read if s3_configured() else False,
            "presign_ttl_seconds": cfg.presign_ttl_seconds if s3_configured() else None,
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
        overlays = [
            row_to_dict(row)
            for row in conn.execute(
                "SELECT * FROM overlays WHERE project_id = ?", (project_id,)
            ).fetchall()
        ]
        asset_paths = {
            page["id"]: [
                str(row["relative_path"])
                for row in conn.execute(
                    "SELECT relative_path FROM page_assets WHERE page_id = ?",
                    (page["id"],),
                ).fetchall()
            ]
            for page in pages
        }
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    for page in pages:
        delete_asset_package(page, asset_paths[page["id"]])
    for overlay in overlays:
        delete_overlay_asset(overlay, suppress_errors=True)
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
        overlays = conn.execute(
            """
            SELECT * FROM overlays
            WHERE project_id = ?
            ORDER BY page_id, z_index, created_at
            """,
            (project_id,),
        ).fetchall()
    project["pages"] = [
        page_to_api(ensure_html_instrumentation(row_to_dict(row))) for row in pages
    ]
    parsed: list[dict[str, Any]] = []
    for row in interactions:
        item = row_to_dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        parsed.append(item)
    project["interactions"] = parsed
    project["overlays"] = [overlay_to_api(row_to_dict(row)) for row in overlays]
    return project


@app.post("/api/projects/{project_id}/pages")
async def api_upload_pages(
    project_id: str,
    storage_backend: str | None = Form(None),
    names_json: str | None = Form(None),
    render_mode: str | None = Form(None),
    viewport_width: int | None = Form(None),
    viewport_height: int | None = Form(None),
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

    normalized_render_mode, normalized_viewport_width, normalized_viewport_height = normalize_render_settings(
        render_mode=render_mode,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )

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
        source_kind = "zip" if ext == ".zip" else "single"
        page_type = (
            "html"
            if ext in {".html", ".htm", ".zip"}
            else "image"
            if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
            else None
        )
        if not page_type:
            raise HTTPException(400, f"Unsupported file type: {filename}")
        name = requested_names[idx] if requested_names else clean_name(Path(filename).stem, kind="Page")
        prepared.append(
            {
                "upload": upload,
                "filename": filename,
                "type": page_type,
                "source_kind": source_kind,
                "name": name,
            }
        )

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
            page_id = str(uuid.uuid4())
            package = await run_in_threadpool(
                store_uploaded_page,
                upload=item["upload"],
                filename=item["filename"],
                page_type=item["type"],
                source_kind=item["source_kind"],
                project_id=project_id,
                page_id=page_id,
                backend=backend,
            )
            stored.append({**item, **package})

        with db() as conn:
            for offset, item in enumerate(stored):
                conn.execute(
                    """
                    INSERT INTO pages(
                        id, project_id, name, type, storage_backend, storage_prefix, entry_path,
                        render_mode, viewport_width, viewport_height, instrumentation_version,
                        sort_order, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"], project_id, item["name"], item["type"], backend,
                        item["storage_prefix"], item["entry_path"],
                        normalized_render_mode, normalized_viewport_width, normalized_viewport_height,
                        HTML_INSTRUMENTATION_VERSION if item["type"] == "html" else 0,
                        next_order + offset, now_iso(),
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO page_assets(page_id, relative_path, media_type, size_bytes)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            item["id"],
                            asset["relative_path"],
                            asset["media_type"],
                            asset["size_bytes"],
                        )
                        for asset in item["assets"]
                    ],
                )
    except sqlite3.IntegrityError as exc:
        for item in stored:
            delete_asset_package(
                item,
                [asset["relative_path"] for asset in item["assets"]],
            )
        raise HTTPException(409, "页面名称在当前项目中已存在") from exc
    except Exception:
        for item in stored:
            delete_asset_package(
                item,
                [asset["relative_path"] for asset in item["assets"]],
            )
        raise

    return [get_page(item["id"]) for item in stored]


@app.patch("/api/pages/{page_id}")
def api_update_page(page_id: str, payload: PageUpdate):
    page = get_page(page_id)
    updates: dict[str, Any] = {}
    if payload.name is not None:
        updates["name"] = clean_name(payload.name, kind="Page")
    if payload.render_mode is not None or payload.viewport_width is not None or payload.viewport_height is not None:
        render_mode, viewport_width, viewport_height = normalize_render_settings(
            render_mode=payload.render_mode if payload.render_mode is not None else page.get("render_mode"),
            viewport_width=payload.viewport_width if payload.viewport_width is not None else page.get("viewport_width"),
            viewport_height=payload.viewport_height if payload.viewport_height is not None else page.get("viewport_height"),
        )
        updates.update(
            render_mode=render_mode,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
        )
    if not updates:
        raise HTTPException(400, "No page fields to update")
    assignments = ", ".join(f"{field} = ?" for field in updates)
    try:
        with db() as conn:
            conn.execute(f"UPDATE pages SET {assignments} WHERE id = ?", (*updates.values(), page_id))
    except sqlite3.IntegrityError as exc:
        if "name" in updates:
            raise duplicate_error("页面", str(updates["name"])) from exc
        raise HTTPException(409, "页面配置冲突") from exc
    return get_page(page_id)


@app.delete("/api/pages/{page_id}")
def api_delete_page(page_id: str):
    page = get_page(page_id)
    paths = page_asset_paths(page_id)
    with db() as conn:
        overlays = [
            row_to_dict(row)
            for row in conn.execute(
                "SELECT * FROM overlays WHERE page_id = ?", (page_id,)
            ).fetchall()
        ]
        conn.execute("DELETE FROM pages WHERE id = ?", (page_id,))
    delete_asset_package(page, paths)
    for overlay in overlays:
        delete_overlay_asset(overlay, suppress_errors=True)
    return {"ok": True}


@app.get("/api/pages/{page_id}/content-url")
def api_page_content_url(page_id: str):
    page = page_to_api(ensure_html_instrumentation(get_page(page_id)))
    return {
        "content_url": page["content_url"],
        "content_url_expires_at": page["content_url_expires_at"],
    }


@app.post("/api/pages/{page_id}/overlays")
async def api_create_overlay(
    page_id: str,
    file: UploadFile = File(...),
    storage_backend: str | None = Form(None),
):
    page = get_page(page_id)
    if page["type"] not in {"image", "html"}:
        raise HTTPException(400, "Overlays are only supported on image or HTML pages")

    backend = str(storage_backend or page["storage_backend"]).strip().lower()
    if backend not in {"local", "s3"}:
        raise HTTPException(400, "storage_backend must be local or s3")
    if backend == "s3" and not s3_configured():
        raise HTTPException(400, "S3 is not configured on the server")

    filename = safe_filename(file.filename or "overlay")
    overlay_type, media_type, suffix, size, media_width, media_height = (
        await run_in_threadpool(inspect_overlay_upload, file, filename)
    )
    aspect_ratio = media_width / media_height
    geometry = await run_in_threadpool(default_overlay_geometry, page, aspect_ratio)
    geometry = normalize_overlay_geometry(geometry)
    overlay_id = str(uuid.uuid4())
    key = overlay_storage_key(page["project_id"], overlay_id, suffix, backend)
    overlay_stub = {"storage_backend": backend, "storage_key": key}

    created_at = now_iso()
    try:
        await run_in_threadpool(
            store_asset_stream,
            backend=backend,
            key=key,
            fileobj=file.file,
            size=size,
            media_type=media_type,
        )
        with db() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(z_index), -1) AS z_index
                FROM overlays WHERE page_id = ?
                """,
                (page_id,),
            ).fetchone()
            z_index = min(int(row["z_index"]) + 1, OVERLAY_MAX_Z_INDEX)
            conn.execute(
                """
                INSERT INTO overlays(
                    id, project_id, page_id, type, storage_backend, storage_key,
                    media_type, size_bytes, x, y, width, height, aspect_ratio,
                    object_fit, z_index, video_controls, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cover', ?, 1, ?, ?)
                """,
                (
                    overlay_id,
                    page["project_id"],
                    page_id,
                    overlay_type,
                    backend,
                    key,
                    media_type,
                    size,
                    geometry["x"],
                    geometry["y"],
                    geometry["width"],
                    geometry["height"],
                    aspect_ratio,
                    z_index,
                    created_at,
                    created_at,
                ),
            )
    except Exception:
        delete_overlay_asset(overlay_stub, suppress_errors=True)
        raise
    return overlay_to_api(get_overlay(overlay_id))


@app.patch("/api/overlays/{overlay_id}")
def api_update_overlay(overlay_id: str, payload: OverlayUpdate):
    overlay = get_overlay(overlay_id)
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(400, "No overlay fields to update")
    if any(value is None for value in values.values()):
        raise HTTPException(400, "Overlay fields cannot be null")

    updates: dict[str, Any] = {}
    geometry_fields = {"x", "y", "width", "height"}
    if geometry_fields.intersection(values):
        merged = {
            field: values.get(field, overlay[field]) for field in geometry_fields
        }
        normalized = normalize_overlay_geometry(merged)
        for field in geometry_fields.intersection(values):
            updates[field] = normalized[field]

    if "object_fit" in values:
        object_fit = str(values["object_fit"]).strip().lower()
        if object_fit not in {"contain", "cover"}:
            raise HTTPException(400, "object_fit must be contain or cover")
        updates["object_fit"] = object_fit
    if "z_index" in values:
        z_index = values["z_index"]
        if isinstance(z_index, bool) or not 0 <= int(z_index) <= OVERLAY_MAX_Z_INDEX:
            raise HTTPException(
                400, f"z_index must be between 0 and {OVERLAY_MAX_Z_INDEX}"
            )
        updates["z_index"] = int(z_index)
    if "video_controls" in values:
        updates["video_controls"] = int(bool(values["video_controls"]))

    if not updates:
        raise HTTPException(400, "No overlay fields to update")
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{field} = ?" for field in updates)
    try:
        with db() as conn:
            conn.execute(
                f"UPDATE overlays SET {assignments} WHERE id = ?",
                (*updates.values(), overlay_id),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Overlay configuration conflicts with its page") from exc
    return overlay_to_api(get_overlay(overlay_id))


@app.delete("/api/overlays/{overlay_id}")
def api_delete_overlay(overlay_id: str):
    overlay = get_overlay(overlay_id)
    try:
        delete_overlay_asset(overlay)
    except ObjectStorageConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "Failed to delete overlay asset") from exc
    with db() as conn:
        conn.execute("DELETE FROM overlays WHERE id = ?", (overlay_id,))
    return {"ok": True}


@app.get("/api/overlays/{overlay_id}/content-url")
def api_overlay_content_url(overlay_id: str):
    overlay = overlay_to_api(get_overlay(overlay_id))
    return {
        "content_url": overlay["content_url"],
        "content_url_expires_at": overlay["content_url_expires_at"],
    }


def get_page_asset(page_id: str, relative_path: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM page_assets
            WHERE page_id = ? AND relative_path = ?
            """,
            (page_id, relative_path),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Asset not found")
    return row_to_dict(row)


def content_headers() -> dict[str, str]:
    return {
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        "Cache-Control": "private, max-age=3600",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }


def can_redirect_to_object_storage(media_type: str) -> bool:
    base_type = media_type.split(";", 1)[0].strip().lower()
    if base_type == "image/svg+xml" or "mpegurl" in base_type:
        return False
    return base_type.startswith(("image/", "audio/", "video/"))


def parse_range_header(value: str | None, size: int) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or size <= 0:
        raise HTTPException(
            416,
            "Requested range is not satisfiable",
            headers={"Content-Range": f"bytes */{size}"},
        )
    start_raw, end_raw = match.groups()
    if not start_raw:
        suffix_length = int(end_raw or 0)
        if suffix_length <= 0:
            raise HTTPException(
                416,
                "Requested range is not satisfiable",
                headers={"Content-Range": f"bytes */{size}"},
            )
        start = max(0, size - suffix_length)
        end = size - 1
    else:
        start = int(start_raw)
        end = min(size - 1, int(end_raw)) if end_raw else size - 1
    if start >= size or start > end:
        raise HTTPException(
            416,
            "Requested range is not satisfiable",
            headers={"Content-Range": f"bytes */{size}"},
        )
    return start, end


@app.api_route(
    "/overlay-content/{overlay_id}/{token}/{asset_name}",
    methods=["GET", "HEAD", "OPTIONS"],
)
def overlay_asset_content(
    request: Request,
    overlay_id: str,
    token: str,
    asset_name: str,
):
    if not valid_content_token(f"overlay:{overlay_id}", token):
        raise HTTPException(403, "Content URL is invalid or expired")
    if request.method == "OPTIONS":
        headers = content_headers()
        headers.update(
            {
                "Access-Control-Allow-Headers": "Range",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            }
        )
        return Response(status_code=204, headers=headers)

    overlay = get_overlay(overlay_id)
    key = str(overlay["storage_key"])
    if asset_name != Path(key).name:
        raise HTTPException(404, "Overlay asset not found")
    size = int(overlay["size_bytes"])
    media_type = str(overlay["media_type"])
    headers = content_headers()
    byte_range = parse_range_header(request.headers.get("range"), size)

    if overlay["storage_backend"] == "local":
        path = local_asset_path(key)
        if not path.is_file():
            raise HTTPException(404, "Overlay asset not found")
        return FileResponse(path, media_type=media_type, headers=headers)

    if overlay["storage_backend"] != "s3":
        raise HTTPException(500, "Unsupported storage backend")

    settings = object_storage_settings()
    if request.method == "GET" and settings.direct_read:
        try:
            signed = object_storage().presign_get(key)
        except ObjectStorageConfigurationError as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                502, "Failed to create an object storage access URL"
            ) from exc
        return RedirectResponse(signed.url, status_code=307, headers=headers)

    if byte_range:
        start, end = byte_range
        status_code = 206
        content_length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    else:
        start = end = None
        status_code = 200
        content_length = size
    headers["Content-Length"] = str(content_length)
    if request.method == "HEAD":
        return Response(status_code=status_code, media_type=media_type, headers=headers)
    try:
        stream = object_storage().iter_bytes(key, start=start, end=end)
    except ObjectStorageConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "Failed to read overlay asset") from exc
    return StreamingResponse(
        stream,
        status_code=status_code,
        media_type=media_type,
        headers=headers,
    )


@app.api_route(
    "/content/{page_id}/{token}/{asset_path:path}",
    methods=["GET", "HEAD", "OPTIONS"],
)
def page_asset_content(
    request: Request,
    page_id: str,
    token: str,
    asset_path: str,
):
    if not valid_content_token(page_id, token):
        raise HTTPException(403, "Content URL is invalid or expired")
    if request.method == "OPTIONS":
        headers = content_headers()
        headers.update(
            {
                "Access-Control-Allow-Headers": "Range",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            }
        )
        return Response(status_code=204, headers=headers)
    try:
        relative_path = validate_relative_asset_path(asset_path)
    except HTTPException as exc:
        raise HTTPException(404, "Asset not found") from exc
    page = get_page(page_id)
    asset = get_page_asset(page_id, relative_path)
    size = int(asset["size_bytes"])
    media_type = str(asset["media_type"])
    headers = content_headers()
    byte_range = parse_range_header(request.headers.get("range"), size)

    if page["storage_backend"] == "local":
        path = local_asset_path(asset_storage_key(page, relative_path))
        if not path.is_file():
            raise HTTPException(404, "Asset not found")
        return FileResponse(path, media_type=media_type, headers=headers)

    if page["storage_backend"] != "s3":
        raise HTTPException(500, "Unsupported storage backend")

    settings = object_storage_settings()
    if request.method == "GET" and settings.direct_read and can_redirect_to_object_storage(media_type):
        try:
            signed = object_storage().presign_get(asset_storage_key(page, relative_path))
        except ObjectStorageConfigurationError as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, "Failed to create an object storage access URL") from exc
        return RedirectResponse(signed.url, status_code=307, headers=headers)

    if byte_range:
        start, end = byte_range
        status_code = 206
        content_length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    else:
        start = end = None
        status_code = 200
        content_length = size
    headers["Content-Length"] = str(content_length)
    if request.method == "HEAD":
        return Response(status_code=status_code, media_type=media_type, headers=headers)
    try:
        stream = object_storage().iter_bytes(
            asset_storage_key(page, relative_path),
            start=start,
            end=end,
        )
    except ObjectStorageConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "Failed to read asset") from exc
    return StreamingResponse(
        stream,
        status_code=status_code,
        media_type=media_type,
        headers=headers,
    )


@app.get("/api/pages/{page_id}/file")
def api_page_file(page_id: str):
    page = get_page(page_id)
    if page["type"] != "image":
        raise HTTPException(400, "Not an image page")
    return RedirectResponse(page_to_api(page)["content_url"], status_code=307)


def strip_html_instrumentation(source: str) -> str:
    for tag, element_id in (("style", "__uipm_style"), ("script", "__uipm_script")):
        source = re.sub(
            rf"<{tag}\b[^>]*\bid=[\"']{element_id}[\"'][^>]*>.*?</{tag}\s*>",
            "",
            source,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return source


def instrument_html(page_id: str, source: str) -> str:
    source = strip_html_instrumentation(source)
    source = re.sub(
        r"<meta[^>]+http-equiv=[\"']?Content-Security-Policy[\"']?[^>]*>",
        "",
        source,
        flags=re.IGNORECASE,
    )
    css = """
<style id="__uipm_style">
html,body{min-height:100%;}
[data-ui-id]{cursor:pointer!important;}
html[data-uipm-mode="edit"] [data-ui-id]:hover{outline:2px solid #2563eb!important;outline-offset:2px!important;}
#__uipm_overlay_root{position:fixed;inset:0;z-index:2147483646;pointer-events:none;overflow:visible;}
.__uipm_marker{position:absolute;border:1px solid rgba(37,99,235,.72);border-radius:4px;background:rgba(37,99,235,.08);box-sizing:border-box;pointer-events:none;}
.__uipm_marker.__uipm_hovered{border-color:#2563eb;background:rgba(37,99,235,.14);}
.__uipm_marker.__uipm_active{border:3px solid #2563eb;background:rgba(37,99,235,.16);box-shadow:0 0 0 3px rgba(37,99,235,.18);}
.__uipm_marker.__uipm_draft{border-style:dashed;}
.__uipm_marker_label{position:absolute;left:-1px;top:-22px;max-width:240px;padding:3px 6px;border-radius:4px;background:#2563eb;color:#fff;font:600 11px/1.35 Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;box-shadow:0 2px 8px rgba(29,78,216,.24);}
.__uipm_marker.__uipm_label_below .__uipm_marker_label{top:auto;bottom:-22px;}
</style>"""

    script = r"""
<script id="__uipm_script">
(() => {
  const PAGE_ID = __PAGE_ID__;
  const INSTRUMENTATION_VERSION = __INSTRUMENTATION_VERSION__;
  const hashMode = new URLSearchParams(location.hash.slice(1)).get('uipm-mode');
  const queryMode = new URLSearchParams(location.search).get('mode');
  const EDIT_MODE = (hashMode || queryMode) === 'edit';
  document.documentElement.dataset.uipmMode = EDIT_MODE ? 'edit' : 'play';
  const IGNORED_TAGS = new Set(['SCRIPT', 'STYLE', 'LINK', 'META', 'TITLE', 'BASE', 'NOSCRIPT']);
  const SEMANTIC_SELECTOR = 'a,button,input,select,textarea,label,[role="button"],[onclick]';
  const editorState = {interactions: [], selectedInteractionId: null, hoveredInteractionId: null, draft: null};
  let nextElementNumber = 1;
  let overlayRoot = null;
  let overlayFrame = 0;
  let sizeReportFrame = 0;
  let sizeObserver = null;
  let mutationObserver = null;
  let lastOverlayStatus = '';
  let lastHoveredElementId = null;

  function elementText(element) {
    return (element.innerText || element.getAttribute('aria-label') || element.getAttribute('title') || '')
      .trim().replace(/\s+/g, ' ').slice(0, 100);
  }

  function inspectableElements() {
    if (!document.body) return [];
    return Array.from(document.body.querySelectorAll('*')).filter((element) => {
      if (IGNORED_TAGS.has(element.tagName)) return false;
      return !element.closest('#__uipm_overlay_root');
    });
  }

  function initializeElementIds() {
    const elements = inspectableElements();
    elements.forEach((element, index) => { element.dataset.uiId = 'u' + (index + 1); });
    nextElementNumber = elements.length + 1;
  }

  function assignIdsToNewElements() {
    inspectableElements().forEach((element) => {
      if (!element.dataset.uiId) element.dataset.uiId = 'u' + nextElementNumber++;
    });
  }

  function elementById(elementId) {
    const wanted = String(elementId || '');
    return inspectableElements().find((element) => element.dataset.uiId === wanted) || null;
  }

  function interactionTarget(eventTarget, ensureIds = false) {
    if (!eventTarget || !eventTarget.closest) return null;
    if (ensureIds) assignIdsToNewElements();
    const raw = eventTarget.closest('[data-ui-id]');
    let bound = raw;
    while (EDIT_MODE && bound) {
      if (editorState.interactions.some((item) => item.elementId === bound.dataset.uiId)) return bound;
      bound = bound.parentElement ? bound.parentElement.closest('[data-ui-id]') : null;
    }
    const semantic = eventTarget.closest(SEMANTIC_SELECTOR);
    return semantic && semantic.dataset && semantic.dataset.uiId ? semantic : raw;
  }

  function reportSize() {
    cancelAnimationFrame(sizeReportFrame);
    sizeReportFrame = requestAnimationFrame(() => {
      const body = document.body;
      const root = document.documentElement;
      if (!body || !root) return;
      let contentWidth = Math.max(body.scrollWidth, root.scrollWidth);
      let contentHeight = Math.max(body.scrollHeight, root.scrollHeight);
      Array.from(body.children).forEach((element) => {
        if (element.id === '__uipm_overlay_root') return;
        const rect = element.getBoundingClientRect();
        contentWidth = Math.max(contentWidth, Math.ceil(rect.right + window.scrollX));
        contentHeight = Math.max(contentHeight, Math.ceil(rect.bottom + window.scrollY));
      });
      window.parent.postMessage({
        type: 'uipm-render-size', pageId: PAGE_ID,
        viewportWidth: window.innerWidth, viewportHeight: window.innerHeight,
        contentWidth, contentHeight
      }, '*');
    });
  }

  function postOverlayStatus(missingInteractionIds, elementMeta) {
    const status = JSON.stringify({missingInteractionIds, elementMeta});
    if (status === lastOverlayStatus) return;
    lastOverlayStatus = status;
    window.parent.postMessage({
      type: 'uipm-overlay-status', pageId: PAGE_ID,
      missingInteractionIds, elementMeta
    }, '*');
  }

  function renderOverlay() {
    if (!EDIT_MODE || !overlayRoot) return;
    overlayRoot.replaceChildren();
    const missingInteractionIds = [];
    const elementMeta = [];
    const elementsById = new Map(inspectableElements().map((element) => [element.dataset.uiId, element]));
    const items = editorState.interactions.slice();
    if (editorState.draft && editorState.draft.elementId) {
      items.push({...editorState.draft, interactionId: '__draft__', draft: true});
    }

    items.forEach((item) => {
      const target = elementsById.get(item.elementId) || null;
      if (!target) {
        if (!item.draft) missingInteractionIds.push(item.interactionId);
        return;
      }
      const rect = target.getBoundingClientRect();
      const style = window.getComputedStyle(target);
      if (rect.width <= 0 || rect.height <= 0 || style.display === 'none' || style.visibility === 'hidden') {
        if (!item.draft) missingInteractionIds.push(item.interactionId);
        return;
      }
      if (!item.draft) {
        elementMeta.push({
          interactionId: item.interactionId,
          tag: target.tagName.toLowerCase(),
          text: elementText(target)
        });
      }
      const marker = document.createElement('div');
      const isActive = item.draft || item.interactionId === editorState.selectedInteractionId;
      const isHovered = item.interactionId === editorState.hoveredInteractionId;
      marker.className = '__uipm_marker';
      if (isActive) marker.classList.add('__uipm_active');
      if (isHovered) marker.classList.add('__uipm_hovered');
      if (item.draft) marker.classList.add('__uipm_draft');
      if (rect.top < 24) marker.classList.add('__uipm_label_below');
      Object.assign(marker.style, {
        left: rect.left + 'px', top: rect.top + 'px',
        width: rect.width + 'px', height: rect.height + 'px'
      });
      const label = document.createElement('span');
      label.className = '__uipm_marker_label';
      label.textContent = item.name || elementText(target) || item.elementId;
      marker.appendChild(label);
      overlayRoot.appendChild(marker);
    });
    postOverlayStatus(missingInteractionIds.sort(), elementMeta);
  }

  function scheduleOverlay() {
    if (!EDIT_MODE) return;
    cancelAnimationFrame(overlayFrame);
    overlayFrame = requestAnimationFrame(renderOverlay);
  }

  function revealInteraction(interactionId) {
    const interaction = editorState.interactions.find((item) => item.interactionId === interactionId);
    const target = interaction ? elementById(interaction.elementId) : null;
    if (!target) return;
    target.scrollIntoView({behavior: 'smooth', block: 'center', inline: 'center'});
    window.setTimeout(scheduleOverlay, 260);
  }

  function updateEditorState(data) {
    editorState.interactions = Array.isArray(data.interactions) ? data.interactions.map((item) => ({
      interactionId: String(item.interactionId || ''),
      elementId: String(item.elementId || ''),
      name: String(item.name || '')
    })).filter((item) => item.interactionId && item.elementId) : [];
    editorState.selectedInteractionId = data.selectedInteractionId ? String(data.selectedInteractionId) : null;
    editorState.hoveredInteractionId = data.hoveredInteractionId ? String(data.hoveredInteractionId) : null;
    editorState.draft = data.draft && data.draft.elementId ? {
      elementId: String(data.draft.elementId),
      name: String(data.draft.name || '')
    } : null;
    scheduleOverlay();
    if (data.revealInteractionId) revealInteraction(String(data.revealInteractionId));
  }

  function postElementHover(elementId) {
    if (elementId === lastHoveredElementId) return;
    lastHoveredElementId = elementId;
    window.parent.postMessage({type: 'uipm-element-hover', pageId: PAGE_ID, elementId}, '*');
  }

  function init() {
    initializeElementIds();
    if (EDIT_MODE) {
      overlayRoot = document.createElement('div');
      overlayRoot.id = '__uipm_overlay_root';
      overlayRoot.setAttribute('aria-hidden', 'true');
      document.body.appendChild(overlayRoot);
    }

    document.addEventListener('keydown', (event) => {
      if (EDIT_MODE || event.key !== 'Escape' || event.repeat) return;
      event.preventDefault();
      event.stopPropagation();
      window.parent.postMessage({
        type: 'uipm-preview-key', pageId: PAGE_ID, key: 'Escape',
        instrumentationVersion: INSTRUMENTATION_VERSION
      }, '*');
    }, true);

    document.addEventListener('click', (event) => {
      const target = interactionTarget(event.target, true);
      if (!target) return;
      event.preventDefault();
      event.stopPropagation();
      window.parent.postMessage({
        type: 'uipm-element-click', pageId: PAGE_ID, elementId: target.dataset.uiId,
        tag: target.tagName.toLowerCase(), text: elementText(target)
      }, '*');
    }, true);

    if (EDIT_MODE) {
      document.addEventListener('pointerover', (event) => {
        const target = interactionTarget(event.target);
        postElementHover(target ? target.dataset.uiId : null);
      }, true);
      document.addEventListener('pointerout', (event) => {
        const from = interactionTarget(event.target);
        const to = interactionTarget(event.relatedTarget);
        if (from && to && from.dataset.uiId === to.dataset.uiId) return;
        postElementHover(to ? to.dataset.uiId : null);
      }, true);
      window.addEventListener('message', (event) => {
        const data = event.data;
        if (event.source !== window.parent || !data || data.type !== 'uipm-editor-state' || String(data.pageId || '') !== PAGE_ID) return;
        updateEditorState(data);
      });
      window.addEventListener('scroll', scheduleOverlay, true);
      window.addEventListener('resize', scheduleOverlay);
    }

    reportSize();
    window.addEventListener('load', () => { reportSize(); scheduleOverlay(); }, {once: true});
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(() => { reportSize(); scheduleOverlay(); }).catch(() => {});
    }
    if (window.ResizeObserver && document.body) {
      sizeObserver = new ResizeObserver(() => { reportSize(); scheduleOverlay(); });
      sizeObserver.observe(document.body);
    }
    if (window.MutationObserver && document.body) {
      mutationObserver = new MutationObserver((records) => {
        const relevant = records.some((record) => !(record.target.closest && record.target.closest('#__uipm_overlay_root')));
        if (!relevant) return;
        assignIdsToNewElements();
        reportSize();
        scheduleOverlay();
      });
      mutationObserver.observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'style', 'hidden']});
    }
    window.parent.postMessage({type: 'uipm-content-ready', pageId: PAGE_ID}, '*');
    if (EDIT_MODE) window.parent.postMessage({type: 'uipm-editor-ready', pageId: PAGE_ID}, '*');
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true}); else init();
})();
</script>
"""
    script = script.replace("__PAGE_ID__", json.dumps(page_id))
    script = script.replace("__INSTRUMENTATION_VERSION__", str(HTML_INSTRUMENTATION_VERSION))
    injection = css + script
    if re.search(r"</body\s*>", source, flags=re.IGNORECASE):
        return re.sub(r"</body\s*>", lambda _m: injection + "</body>", source, count=1, flags=re.IGNORECASE)
    return source + injection


def decode_html(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def prepare_html_asset(page_id: str, raw: bytes) -> bytes:
    return instrument_html(page_id, decode_html(raw)).encode("utf-8")


def ensure_html_instrumentation(page: dict[str, Any]) -> dict[str, Any]:
    item = dict(page)
    if item["type"] != "html":
        return item
    version = int(item.get("instrumentation_version") or 0)
    if version >= HTML_INSTRUMENTATION_VERSION:
        return item
    try:
        prepared = prepare_html_asset(item["id"], read_page_asset(item))
        store_asset_bytes(
            backend=item["storage_backend"],
            key=asset_storage_key(item, item["entry_path"]),
            data=prepared,
            media_type="text/html; charset=utf-8",
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "Asset not found") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Failed to update HTML runtime: {exc}") from exc
    with db() as conn:
        conn.execute(
            "UPDATE pages SET instrumentation_version = ? WHERE id = ?",
            (HTML_INSTRUMENTATION_VERSION, item["id"]),
        )
        conn.execute(
            """
            UPDATE page_assets
            SET size_bytes = ?, media_type = ?
            WHERE page_id = ? AND relative_path = ?
            """,
            (
                len(prepared),
                "text/html; charset=utf-8",
                item["id"],
                item["entry_path"],
            ),
        )
    item["instrumentation_version"] = HTML_INSTRUMENTATION_VERSION
    return item


@app.get("/api/pages/{page_id}/render", response_class=HTMLResponse)
def api_render_html(page_id: str, mode: str = "edit"):
    page = ensure_html_instrumentation(get_page(page_id))
    if page["type"] != "html":
        raise HTTPException(400, "Not an HTML page")
    if mode not in {"edit", "play"}:
        mode = "edit"
    target = page_to_api(page)["content_url"] + f"#uipm-mode={mode}"
    return RedirectResponse(target, status_code=307)


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


def normalize_interaction_action(
    source: dict[str, Any],
    action: str,
    target_page_id: str | None,
) -> tuple[str, str | None]:
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"navigate", "back"}:
        raise HTTPException(400, "action must be navigate or back")
    if normalized_action == "back":
        return normalized_action, None
    if not target_page_id:
        raise HTTPException(400, "target_page_id is required for navigate action")
    target = get_page(target_page_id)
    if source["project_id"] != target["project_id"]:
        raise HTTPException(400, "Target page must be in the same project")
    return normalized_action, target["id"]


@app.post("/api/interactions")
def api_create_interaction(payload: InteractionCreate):
    name = clean_name(payload.name, kind="Interaction")
    source = get_page(payload.source_page_id)
    action, target_page_id = normalize_interaction_action(source, payload.action, payload.target_page_id)
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
def api_update_interaction(interaction_id: str, payload: InteractionUpdate):
    interaction = get_interaction(interaction_id)
    updates: dict[str, Any] = {}
    if payload.name is not None:
        updates["name"] = clean_name(payload.name, kind="Interaction")

    fields_set = payload.model_fields_set
    if payload.action is not None or "target_page_id" in fields_set:
        source = get_page(interaction["source_page_id"])
        next_action = payload.action if payload.action is not None else interaction["action"]
        next_target = payload.target_page_id if "target_page_id" in fields_set else interaction["target_page_id"]
        action, target_page_id = normalize_interaction_action(source, next_action, next_target)
        updates.update(action=action, target_page_id=target_page_id)

    if not updates:
        raise HTTPException(400, "No interaction fields to update")

    assignments = ", ".join(f"{field} = ?" for field in updates)
    try:
        with db() as conn:
            conn.execute(f"UPDATE interactions SET {assignments} WHERE id = ?", (*updates.values(), interaction_id))
    except sqlite3.IntegrityError as exc:
        if "name" in updates:
            raise duplicate_error("交互", str(updates["name"])) from exc
        raise HTTPException(409, "交互配置冲突") from exc
    return get_interaction(interaction_id)


@app.delete("/api/interactions/{interaction_id}")
def api_delete_interaction(interaction_id: str):
    get_interaction(interaction_id)
    with db() as conn:
        conn.execute("DELETE FROM interactions WHERE id = ?", (interaction_id,))
    return {"ok": True}


@app.get("/health")
def health():
    settings = object_storage_settings()
    return {
        "ok": True,
        "db": str(DB_PATH),
        "s3_configured": settings.configured,
        "storage_provider": settings.provider if settings.configured else None,
        "direct_read": settings.direct_read if settings.configured else False,
    }


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
