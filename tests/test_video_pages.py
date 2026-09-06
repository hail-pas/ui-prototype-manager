from __future__ import annotations

import io
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from starlette.datastructures import Headers

import app.main as main
from app import page_management


def mp4_box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, box_type) + payload


def mp4_bytes(width: int = 1280, height: int = 720) -> bytes:
    file_type = mp4_box(b"ftyp", b"isom\x00\x00\x02\x00isom")
    track_header = mp4_box(
        b"tkhd", b"\x00\x00\x00\x07" + struct.pack(">II", width << 16, height << 16)
    )
    handler = mp4_box(b"hdlr", b"\x00" * 8 + b"vide")
    media = mp4_box(b"mdia", handler)
    track = mp4_box(b"trak", track_header + media)
    return file_type + mp4_box(b"moov", track)


def upload(filename: str, content: bytes, media_type: str) -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers=Headers({"content-type": media_type}),
    )


class VideoPageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_paths = (main.DATA_DIR, main.DB_PATH, main.ASSET_DIR)
        main.DATA_DIR = Path(self.temp_dir.name).resolve()
        main.DB_PATH = main.DATA_DIR / "app.db"
        main.ASSET_DIR = main.DATA_DIR / "assets"
        self.env = patch.dict(os.environ, {"UIPM_ACCESS_KEY": "test-secret"})
        self.env.start()
        main.init_db()

        created_at = main.now_iso()
        with main.db() as connection:
            connection.execute(
                "INSERT INTO projects(id, name, created_at) VALUES ('project-a', 'Project A', ?)",
                (created_at,),
            )
            connection.execute(
                """
                INSERT INTO pages(
                    id, project_id, name, type, storage_backend, storage_prefix,
                    entry_path, sort_order, created_at
                ) VALUES (
                    'image-page', 'project-a', 'Image page', 'image', 'local',
                    'assets/project-a/image-page', 'image.png', 0, ?
                )
                """,
                (created_at,),
            )
            connection.execute(
                """
                INSERT INTO page_assets(page_id, relative_path, media_type, size_bytes)
                VALUES ('image-page', 'image.png', 'image/png', 5)
                """
            )
            connection.execute(
                """
                INSERT INTO interactions(
                    id, project_id, name, source_page_id, action, target_page_id,
                    target_url, kind, payload_json, created_at
                ) VALUES (
                    'image-interaction', 'project-a', 'Back', 'image-page', 'back',
                    NULL, NULL, 'region',
                    '{"x":0.1,"y":0.1,"width":0.2,"height":0.2}', ?
                )
                """,
                (created_at,),
            )
        path = main.local_asset_path("assets/project-a/image-page/image.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")

    def tearDown(self) -> None:
        main.DATA_DIR, main.DB_PATH, main.ASSET_DIR = self.previous_paths
        self.env.stop()
        self.temp_dir.cleanup()

    async def create_video_page(self, name: str = "Motion") -> dict:
        pages = await page_management.api_upload_video_pages(
            "project-a",
            storage_backend="local",
            names_json=f'["{name}"]',
            files=[upload("motion.mp4", mp4_bytes(), "video/mp4")],
        )
        return pages[0]

    async def test_upload_migrates_schema_and_preserves_existing_relationships(self) -> None:
        page = await self.create_video_page()

        self.assertEqual(page["type"], "video")
        self.assertEqual((page["viewport_width"], page["viewport_height"]), (1280, 720))
        stored = main.get_page(page["id"])
        self.assertEqual(main.read_page_asset(stored), mp4_bytes())
        with main.db() as connection:
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pages'"
            ).fetchone()["sql"]
            self.assertIn("'video'", schema)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS n FROM interactions WHERE source_page_id = 'image-page'"
                ).fetchone()["n"],
                1,
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    async def test_replace_copy_and_move_region_keep_video_page_semantics(self) -> None:
        page = await self.create_video_page()
        page_id = page["id"]
        created_at = main.now_iso()
        with main.db() as connection:
            connection.execute(
                """
                INSERT INTO interactions(
                    id, project_id, name, source_page_id, action, target_page_id,
                    target_url, kind, payload_json, created_at
                ) VALUES (
                    'video-region', 'project-a', 'Open area', ?, 'back', NULL, NULL,
                    'region', '{"x":0.1,"y":0.2,"width":0.3,"height":0.25}', ?
                )
                """,
                (page_id, created_at),
            )

        updated_region = page_management.api_update_region_interaction(
            "video-region",
            page_management.RegionInteractionUpdate(
                x=0.45, y=0.35, width=0.3, height=0.25
            ),
        )
        self.assertEqual(updated_region["payload"]["x"], 0.45)
        self.assertEqual(updated_region["payload"]["y"], 0.35)

        old_entry = main.get_page(page_id)["entry_path"]
        replaced = await page_management.api_replace_page_video(
            page_id,
            upload("replacement.mp4", mp4_bytes(1920, 1080), "video/mp4"),
        )
        self.assertEqual(replaced["id"], page_id)
        self.assertEqual(replaced["type"], "video")
        self.assertEqual((replaced["viewport_width"], replaced["viewport_height"]), (1920, 1080))
        self.assertFalse(
            main.local_asset_path(
                f"{main.get_page(page_id)['storage_prefix']}/{old_entry}"
            ).exists()
        )

        copied = page_management.api_duplicate_page(
            page_id, page_management.PageDuplicateRequest(name="Motion copy")
        )
        self.assertEqual(copied["page"]["type"], "video")
        with main.db() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS n FROM interactions WHERE source_page_id = ?",
                    (copied["page"]["id"],),
                ).fetchone()["n"],
                1,
            )

    def test_frontend_video_is_background_media_without_controls(self) -> None:
        editor_script = (main.APP_DIR / "static" / "editor-media.js").read_text()
        player_script = (main.APP_DIR / "static" / "player-media.js").read_text()
        editor_template = (main.APP_DIR / "templates" / "editor.html").read_text()

        self.assertIn('.mp4,.webm', editor_template)
        for script in (editor_script, player_script):
            self.assertIn("video.autoplay = true", script)
            self.assertIn("video.loop = true", script)
            self.assertIn("video.muted = true", script)
            self.assertIn("video.controls = false", script)
        self.assertIn("/api/interactions/${interaction.id}/region", editor_script)


if __name__ == "__main__":
    unittest.main()
