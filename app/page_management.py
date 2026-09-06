from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

router = APIRouter()


class ProjectUpdate(BaseModel):
    name: str


class PageDuplicateRequest(BaseModel):
    name: str | None = None


class OverlayLinkCreate(BaseModel):
    url: str
    type: str
    aspect_ratio: float


class RegionInteractionUpdate(BaseModel):
    x: float
    y: float
    width: float
    height: float


def _core():
    # Import lazily so app.main remains the single application entry point and
    # this module can be imported while main.py is still registering routers.
    from app import main as core

    return core


def _ensure_video_page_schema(core: Any) -> None:
    with core.db() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pages'"
        ).fetchone()
    if not row:
        return
    normalized_sql = "".join(str(row["sql"] or "").lower().split())
    if "typein('html','image','video')" in normalized_sql:
        return

    conn = core.db()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.executescript(
            """
            BEGIN;
            DROP INDEX IF EXISTS uq_pages_project_name;
            DROP INDEX IF EXISTS idx_pages_project;
            ALTER TABLE pages RENAME TO pages_before_video_support;
            CREATE TABLE pages (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('html', 'image', 'video')),
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
            INSERT INTO pages(
                id, project_id, name, type, storage_backend, storage_prefix,
                entry_path, render_mode, viewport_width, viewport_height,
                instrumentation_version, sort_order, created_at
            )
            SELECT
                id, project_id, name, type, storage_backend, storage_prefix,
                entry_path, render_mode, viewport_width, viewport_height,
                instrumentation_version, sort_order, created_at
            FROM pages_before_video_support;
            DROP TABLE pages_before_video_support;
            CREATE UNIQUE INDEX uq_pages_project_name
                ON pages(project_id, name COLLATE NOCASE);
            CREATE INDEX idx_pages_project
                ON pages(project_id, sort_order, created_at);
            COMMIT;
            """
        )
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


def _copy_storage_object(
    core: Any,
    *,
    backend: str,
    source_key: str,
    target_key: str,
    media_type: str,
    size_bytes: int,
) -> None:
    if backend == "local":
        source = core.local_asset_path(source_key)
        target = core.local_asset_path(target_key)
        if not source.is_file():
            raise FileNotFoundError(source_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return

    if backend != "s3":
        raise RuntimeError(f"Unsupported storage backend: {backend}")

    storage = core.object_storage()
    # Media can be larger than regular page assets. Spool instead of loading
    # the whole object into memory while still creating an independent copy.
    with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as buffer:
        copied_size = 0
        for chunk in storage.iter_bytes(source_key):
            buffer.write(chunk)
            copied_size += len(chunk)
        if copied_size != size_bytes:
            raise OSError(
                f"Object size changed while copying: expected {size_bytes}, got {copied_size}"
            )
        buffer.seek(0)
        storage.put_fileobj(
            target_key,
            buffer,
            size=size_bytes,
            media_type=media_type,
        )


def _cleanup_storage(core: Any, copied: list[tuple[str, str]]) -> None:
    grouped: dict[str, list[str]] = defaultdict(list)
    for backend, key in copied:
        grouped[backend].append(key)

    for key in grouped.get("local", []):
        try:
            core.local_asset_path(key).unlink(missing_ok=True)
        except (OSError, RuntimeError):
            pass

    if grouped.get("s3"):
        try:
            core.object_storage().delete_many(grouped["s3"])
        except Exception:
            pass


def _unique_interaction_name(core: Any, original: str, used: set[str]) -> str:
    base = core.clean_name(f"{original} copy", kind="Interaction")
    candidate = base
    number = 2
    while candidate.casefold() in used:
        suffix = f" {number}"
        candidate = f"{base[: max(1, 120 - len(suffix))]}{suffix}"
        number += 1
    used.add(candidate.casefold())
    return candidate


@router.patch("/api/projects/{project_id}")
def api_update_project(project_id: str, payload: ProjectUpdate):
    core = _core()
    core.get_project(project_id)
    name = core.clean_name(payload.name, kind="Project")
    with core.db() as conn:
        conn.execute("UPDATE projects SET name = ? WHERE id = ?", (name, project_id))
    return core.get_project(project_id)


@router.post("/api/projects/{project_id}/video-pages")
async def api_upload_video_pages(
    project_id: str,
    storage_backend: str | None = Form(None),
    names_json: str | None = Form(None),
    files: list[UploadFile] = File(...),
):
    core = _core()
    core.get_project(project_id)
    if not files:
        raise HTTPException(400, "No files uploaded")

    _ensure_video_page_schema(core)
    default_backend = "s3" if core.s3_configured() else "local"
    backend = str(storage_backend or default_backend).strip().lower()
    if backend not in {"local", "s3"}:
        raise HTTPException(400, "storage_backend must be local or s3")
    if backend == "s3" and not core.s3_configured():
        raise HTTPException(400, "S3 is not configured on the server")

    requested_names: list[str] | None = None
    if names_json:
        try:
            raw_names = json.loads(names_json)
            if not isinstance(raw_names, list) or len(raw_names) != len(files):
                raise ValueError
            requested_names = [core.clean_name(value, kind="Page") for value in raw_names]
        except (ValueError, TypeError, json.JSONDecodeError):
            raise HTTPException(400, "names_json must be a JSON array matching uploaded files")

    prepared: list[dict[str, Any]] = []
    for index, upload in enumerate(files):
        filename = core.safe_filename(upload.filename or "video")
        media = await run_in_threadpool(core.inspect_overlay_upload, upload, filename)
        media_type, content_type, suffix, size, width, height = media
        if media_type != "video":
            raise HTTPException(400, f"Unsupported video page type: {filename}")
        name = (
            requested_names[index]
            if requested_names
            else core.clean_name(Path(filename).stem, kind="Page")
        )
        prepared.append(
            {
                "upload": upload,
                "filename": filename,
                "name": name,
                "media_type": content_type,
                "suffix": suffix,
                "size": size,
                "width": width,
                "height": height,
            }
        )

    folded = [item["name"].casefold() for item in prepared]
    if len(folded) != len(set(folded)):
        raise HTTPException(409, "本次上传的页面名称存在重复")
    with core.db() as conn:
        existing = {
            str(row["name"]).casefold()
            for row in conn.execute(
                "SELECT name FROM pages WHERE project_id = ?", (project_id,)
            ).fetchall()
        }
        conflict = next(
            (item["name"] for item in prepared if item["name"].casefold() in existing),
            None,
        )
        if conflict:
            raise core.duplicate_error("页面", conflict)
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS n FROM pages WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        next_order = int(row["n"]) + 1

    stored: list[dict[str, Any]] = []
    try:
        for item in prepared:
            page_id = str(uuid.uuid4())
            prefix = core.page_storage_prefix(project_id, page_id, backend)
            entry_path = f'video{item["suffix"]}'
            await run_in_threadpool(
                core.store_asset_stream,
                backend=backend,
                key=f"{prefix}/{entry_path}",
                fileobj=item["upload"].file,
                size=item["size"],
                media_type=item["media_type"],
            )
            stored.append(
                {
                    **item,
                    "id": page_id,
                    "storage_backend": backend,
                    "storage_prefix": prefix,
                    "entry_path": entry_path,
                }
            )

        created_at = core.now_iso()
        with core.db() as conn:
            for offset, item in enumerate(stored):
                conn.execute(
                    """
                    INSERT INTO pages(
                        id, project_id, name, type, storage_backend, storage_prefix,
                        entry_path, render_mode, viewport_width, viewport_height,
                        instrumentation_version, sort_order, created_at
                    ) VALUES (?, ?, ?, 'video', ?, ?, ?, 'fixed', ?, ?, 0, ?, ?)
                    """,
                    (
                        item["id"],
                        project_id,
                        item["name"],
                        backend,
                        item["storage_prefix"],
                        item["entry_path"],
                        item["width"],
                        item["height"],
                        next_order + offset,
                        created_at,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO page_assets(page_id, relative_path, media_type, size_bytes)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        item["entry_path"],
                        item["media_type"],
                        item["size"],
                    ),
                )
    except sqlite3.IntegrityError as exc:
        for item in stored:
            core.delete_asset_package(item, [item["entry_path"]])
        raise HTTPException(409, "页面名称在当前项目中已存在") from exc
    except Exception:
        for item in stored:
            core.delete_asset_package(item, [item["entry_path"]])
        raise

    return [core.page_to_api(core.get_page(item["id"])) for item in stored]


@router.put("/api/pages/{page_id}/video")
async def api_replace_page_video(page_id: str, file: UploadFile = File(...)):
    core = _core()
    page = core.get_page(page_id)
    if page["type"] != "video":
        raise HTTPException(400, "Only video pages can replace their base video")

    filename = core.safe_filename(file.filename or "video")
    media = await run_in_threadpool(core.inspect_overlay_upload, file, filename)
    media_type, content_type, suffix, size, width, height = media
    if media_type != "video":
        raise HTTPException(400, f"Unsupported video page type: {filename}")

    new_entry_path = f"video-{uuid.uuid4().hex}{suffix}"
    old_entry_path = str(page["entry_path"])
    try:
        await run_in_threadpool(
            core.store_asset_stream,
            backend=str(page["storage_backend"]),
            key=core.asset_storage_key(page, new_entry_path),
            fileobj=file.file,
            size=size,
            media_type=content_type,
        )
        with core.db() as conn:
            conn.execute("DELETE FROM page_assets WHERE page_id = ?", (page_id,))
            conn.execute(
                """
                INSERT INTO page_assets(page_id, relative_path, media_type, size_bytes)
                VALUES (?, ?, ?, ?)
                """,
                (page_id, new_entry_path, content_type, size),
            )
            conn.execute(
                """
                UPDATE pages
                SET entry_path = ?, viewport_width = ?, viewport_height = ?
                WHERE id = ?
                """,
                (new_entry_path, width, height, page_id),
            )
    except Exception:
        try:
            core.delete_page_asset_file(page, new_entry_path)
        except Exception:
            pass
        raise

    if old_entry_path != new_entry_path:
        try:
            core.delete_page_asset_file(page, old_entry_path)
        except Exception:
            pass
    return core.page_to_api(core.get_page(page_id))


@router.patch("/api/interactions/{interaction_id}/region")
def api_update_region_interaction(
    interaction_id: str, payload: RegionInteractionUpdate
):
    core = _core()
    interaction = core.get_interaction(interaction_id)
    if interaction["kind"] != "region":
        raise HTTPException(400, "Only region interactions can update their position")
    normalized = core.normalize_interaction_payload("region", payload.model_dump())
    with core.db() as conn:
        conn.execute(
            "UPDATE interactions SET payload_json = ? WHERE id = ?",
            (json.dumps(normalized, ensure_ascii=False), interaction_id),
        )
    return core.get_interaction(interaction_id)


async def _create_overlay_for_video_page(
    core: Any,
    page: dict[str, Any],
    file: UploadFile,
    storage_backend: str | None,
):
    backend = str(storage_backend or page["storage_backend"]).strip().lower()
    if backend not in {"local", "s3"}:
        raise HTTPException(400, "storage_backend must be local or s3")
    if backend == "s3" and not core.s3_configured():
        raise HTTPException(400, "S3 is not configured on the server")

    filename = core.safe_filename(file.filename or "overlay")
    overlay_type, media_type, suffix, size, media_width, media_height = (
        await run_in_threadpool(core.inspect_overlay_upload, file, filename)
    )
    aspect_ratio = media_width / media_height
    geometry = core.normalize_overlay_geometry(
        core.default_overlay_geometry(page, aspect_ratio)
    )
    overlay_id = str(uuid.uuid4())
    key = core.overlay_storage_key(page["project_id"], overlay_id, suffix, backend)
    overlay_stub = {"storage_backend": backend, "storage_key": key}
    created_at = core.now_iso()

    try:
        await run_in_threadpool(
            core.store_asset_stream,
            backend=backend,
            key=key,
            fileobj=file.file,
            size=size,
            media_type=media_type,
        )
        with core.db() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(z_index), -1) AS z_index FROM overlays WHERE page_id = ?",
                (page["id"],),
            ).fetchone()
            z_index = min(int(row["z_index"]) + 1, core.OVERLAY_MAX_Z_INDEX)
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
                    page["id"],
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
        core.delete_overlay_asset(overlay_stub, suppress_errors=True)
        raise
    return core.overlay_to_api(core.get_overlay(overlay_id))


@router.post("/api/pages/{page_id}/overlays")
async def api_create_overlay_with_video_page(
    page_id: str,
    file: UploadFile = File(...),
    storage_backend: str | None = Form(None),
):
    core = _core()
    page = core.get_page(page_id)
    if page["type"] != "video":
        return await core.api_create_overlay(page_id, file, storage_backend)
    return await _create_overlay_for_video_page(core, page, file, storage_backend)


@router.post("/api/pages/{page_id}/overlays/from-url")
def api_create_overlay_link_with_video_page(
    page_id: str, payload: OverlayLinkCreate
):
    core = _core()
    page = core.get_page(page_id)
    if page["type"] != "video":
        return core.api_create_overlay_link(page_id, payload)

    source_url, media_type, aspect_ratio = core.validate_overlay_link(
        payload.url, payload.type, payload.aspect_ratio
    )
    geometry = core.normalize_overlay_geometry(
        core.default_overlay_geometry(page, aspect_ratio)
    )
    overlay_id = str(uuid.uuid4())
    created_at = core.now_iso()
    with core.db() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(z_index), -1) AS z_index FROM overlays WHERE page_id = ?",
            (page_id,),
        ).fetchone()
        z_index = min(int(row["z_index"]) + 1, core.OVERLAY_MAX_Z_INDEX)
        conn.execute(
            """
            INSERT INTO overlays(
                id, project_id, page_id, type, storage_backend, storage_key,
                media_type, size_bytes, x, y, width, height, aspect_ratio,
                object_fit, z_index, video_controls, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'url', ?, ?, 0, ?, ?, ?, ?, ?, 'cover', ?, 1, ?, ?)
            """,
            (
                overlay_id,
                page["project_id"],
                page_id,
                payload.type.strip().lower(),
                source_url,
                media_type,
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
    return core.overlay_to_api(core.get_overlay(overlay_id))


@router.post("/api/pages/{page_id}/duplicate")
def api_duplicate_page(page_id: str, payload: PageDuplicateRequest):
    core = _core()
    page = core.get_page(page_id)
    if page["type"] not in {"image", "video"}:
        raise HTTPException(400, "HTML 页面暂不支持复制")

    project_id = str(page["project_id"])
    requested_name = payload.name if payload.name is not None else f'{page["name"]} copy'
    new_name = core.clean_name(requested_name, kind="Page")
    new_page_id = str(uuid.uuid4())
    created_at = core.now_iso()

    with core.db() as conn:
        if conn.execute(
            "SELECT 1 FROM pages WHERE project_id = ? AND name = ? COLLATE NOCASE",
            (project_id, new_name),
        ).fetchone():
            raise HTTPException(409, f'页面名称“{new_name}”在当前项目中已存在')

        assets = [
            dict(row)
            for row in conn.execute(
                """
                SELECT relative_path, media_type, size_bytes
                FROM page_assets
                WHERE page_id = ?
                ORDER BY relative_path
                """,
                (page_id,),
            ).fetchall()
        ]
        interactions = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM interactions WHERE source_page_id = ? ORDER BY created_at, id",
                (page_id,),
            ).fetchall()
        ]
        overlays = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM overlays WHERE page_id = ? ORDER BY z_index, created_at, id",
                (page_id,),
            ).fetchall()
        ]
        used_interaction_names = {
            str(row["name"]).casefold()
            for row in conn.execute(
                "SELECT name FROM interactions WHERE project_id = ?", (project_id,)
            ).fetchall()
        }

    if not assets:
        raise HTTPException(409, "页面资源清单为空，无法安全复制该页面")

    backend = str(page["storage_backend"])
    new_prefix = core.page_storage_prefix(project_id, new_page_id, backend)
    copied_objects: list[tuple[str, str]] = []
    copied_overlays: list[dict[str, Any]] = []

    try:
        for asset in assets:
            relative_path = str(asset["relative_path"])
            source_key = core.asset_storage_key(page, relative_path)
            target_key = f'{new_prefix.rstrip("/")}/{relative_path}'
            _copy_storage_object(
                core,
                backend=backend,
                source_key=source_key,
                target_key=target_key,
                media_type=str(asset["media_type"]),
                size_bytes=int(asset["size_bytes"]),
            )
            copied_objects.append((backend, target_key))

        for overlay in overlays:
            new_overlay_id = str(uuid.uuid4())
            overlay_backend = str(overlay["storage_backend"])
            target_key = str(overlay["storage_key"])
            if overlay_backend != "url":
                suffix = os.path.splitext(str(overlay["storage_key"]))[1]
                target_key = core.overlay_storage_key(
                    project_id, new_overlay_id, suffix, overlay_backend
                )
                _copy_storage_object(
                    core,
                    backend=overlay_backend,
                    source_key=str(overlay["storage_key"]),
                    target_key=target_key,
                    media_type=str(overlay["media_type"]),
                    size_bytes=int(overlay["size_bytes"]),
                )
                copied_objects.append((overlay_backend, target_key))

            copied_overlays.append(
                {
                    **overlay,
                    "id": new_overlay_id,
                    "page_id": new_page_id,
                    "storage_key": target_key,
                }
            )

        with core.db() as conn:
            source_sort_order = int(page.get("sort_order") or 0)
            conn.execute(
                """
                UPDATE pages
                SET sort_order = sort_order + 1
                WHERE project_id = ? AND sort_order > ?
                """,
                (project_id, source_sort_order),
            )
            conn.execute(
                """
                INSERT INTO pages(
                    id, project_id, name, type, storage_backend, storage_prefix,
                    entry_path, render_mode, viewport_width, viewport_height,
                    instrumentation_version, sort_order, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_page_id,
                    project_id,
                    new_name,
                    page["type"],
                    backend,
                    new_prefix,
                    page["entry_path"],
                    page.get("render_mode", core.DEFAULT_RENDER_MODE),
                    int(page.get("viewport_width") or core.DEFAULT_VIEWPORT_WIDTH),
                    int(page.get("viewport_height") or core.DEFAULT_VIEWPORT_HEIGHT),
                    int(page.get("instrumentation_version") or 0),
                    source_sort_order + 1,
                    created_at,
                ),
            )
            conn.executemany(
                """
                INSERT INTO page_assets(page_id, relative_path, media_type, size_bytes)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        new_page_id,
                        item["relative_path"],
                        item["media_type"],
                        item["size_bytes"],
                    )
                    for item in assets
                ],
            )

            for interaction in interactions:
                target_page_id = interaction["target_page_id"]
                if interaction["action"] == "navigate" and target_page_id == page_id:
                    target_page_id = new_page_id
                conn.execute(
                    """
                    INSERT INTO interactions(
                        id, project_id, name, source_page_id, action,
                        target_page_id, target_url, kind, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        project_id,
                        _unique_interaction_name(
                            core, str(interaction["name"]), used_interaction_names
                        ),
                        new_page_id,
                        interaction["action"],
                        target_page_id,
                        interaction["target_url"],
                        interaction["kind"],
                        interaction["payload_json"],
                        created_at,
                    ),
                )

            for overlay in copied_overlays:
                conn.execute(
                    """
                    INSERT INTO overlays(
                        id, project_id, page_id, type, storage_backend, storage_key,
                        media_type, size_bytes, x, y, width, height, aspect_ratio,
                        object_fit, z_index, video_controls, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        overlay["id"],
                        project_id,
                        new_page_id,
                        overlay["type"],
                        overlay["storage_backend"],
                        overlay["storage_key"],
                        overlay["media_type"],
                        overlay["size_bytes"],
                        overlay["x"],
                        overlay["y"],
                        overlay["width"],
                        overlay["height"],
                        overlay["aspect_ratio"],
                        overlay["object_fit"],
                        overlay["z_index"],
                        overlay["video_controls"],
                        created_at,
                        created_at,
                    ),
                )
    except sqlite3.IntegrityError as exc:
        _cleanup_storage(core, copied_objects)
        if backend == "local":
            shutil.rmtree(core.local_asset_path(new_prefix), ignore_errors=True)
        raise HTTPException(409, "复制页面时发生名称或数据冲突") from exc
    except HTTPException:
        _cleanup_storage(core, copied_objects)
        if backend == "local":
            shutil.rmtree(core.local_asset_path(new_prefix), ignore_errors=True)
        raise
    except Exception as exc:
        _cleanup_storage(core, copied_objects)
        if backend == "local":
            shutil.rmtree(core.local_asset_path(new_prefix), ignore_errors=True)
        raise HTTPException(502, "复制页面资源失败") from exc

    return {
        "page": core.page_to_api(core.get_page(new_page_id)),
        "copied": {
            "assets": len(assets),
            "interactions": len(interactions),
            "overlays": len(overlays),
        },
    }
