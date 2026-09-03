from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import uuid
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class ProjectUpdate(BaseModel):
    name: str


class PageDuplicateRequest(BaseModel):
    name: str | None = None


def _core():
    # Import lazily so app.main remains the single application entry point and
    # this module can be imported while main.py is still registering routers.
    from app import main as core

    return core


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
    # Overlay videos can be large. Spool instead of loading the whole object
    # into memory, while still writing to an independent storage key.
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


@router.post("/api/pages/{page_id}/duplicate")
def api_duplicate_page(page_id: str, payload: PageDuplicateRequest):
    core = _core()
    page = core.get_page(page_id)
    if page["type"] != "image":
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
