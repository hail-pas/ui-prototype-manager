from __future__ import annotations

import asyncio
import io
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Request, UploadFile
from starlette.datastructures import Headers
from starlette.responses import FileResponse

import app.main as main


def png_bytes(width: int = 800, height: int = 400) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


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


class OverlayApiTests(unittest.TestCase):
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
                "INSERT INTO projects(id, name, created_at) VALUES (?, ?, ?)",
                ("project-a", "Project A", created_at),
            )
            connection.executemany(
                """
                INSERT INTO pages(
                    id, project_id, name, type, storage_backend, storage_prefix,
                    entry_path, viewport_width, viewport_height, created_at
                ) VALUES (?, 'project-a', ?, ?, 'local', ?, ?, 1600, 900, ?)
                """,
                [
                    (
                        "image-page",
                        "Image page",
                        "image",
                        "assets/project-a/image-page",
                        "image.png",
                        created_at,
                    ),
                    (
                        "html-page",
                        "HTML page",
                        "html",
                        "assets/project-a/html-page",
                        "index.html",
                        created_at,
                    ),
                ],
            )
            connection.execute(
                "UPDATE pages SET instrumentation_version = ? WHERE id = 'html-page'",
                (main.HTML_INSTRUMENTATION_VERSION,),
            )

    def tearDown(self) -> None:
        self.env.stop()
        main.DATA_DIR, main.DB_PATH, main.ASSET_DIR = self.previous_paths
        self.temp_dir.cleanup()

    def create_image(self, page_id: str = "image-page") -> dict:
        return asyncio.run(
            main.api_create_overlay(
                page_id,
                file=upload("overlay.png", png_bytes(), "image/png"),
                storage_backend="local",
            )
        )

    def create_video(self, page_id: str = "html-page") -> dict:
        return asyncio.run(
            main.api_create_overlay(
                page_id,
                file=upload("overlay.mp4", mp4_bytes(), "video/mp4"),
                storage_backend="local",
            )
        )

    def test_creates_image_and_video_overlays_for_both_page_types(self) -> None:
        cases = [
            ("image-page", "image"),
            ("image-page", "video"),
            ("html-page", "image"),
            ("html-page", "video"),
        ]
        created: list[dict] = []
        for page_id, overlay_type in cases:
            with self.subTest(page_id=page_id, overlay_type=overlay_type):
                item = (
                    self.create_image(page_id)
                    if overlay_type == "image"
                    else self.create_video(page_id)
                )
                created.append(item)
                self.assertEqual(item["type"], overlay_type)
                self.assertAlmostEqual(item["aspect_ratio"], 2 if overlay_type == "image" else 16 / 9)
                self.assertGreater(item["width"], 0)
                self.assertLessEqual(item["x"] + item["width"], 1)
                self.assertLessEqual(item["y"] + item["height"], 1)
                self.assertTrue(main.local_asset_path(item["storage_key"]).is_file())
                self.assertTrue(item["content_url"].startswith("/overlay-content/"))

        project = main.api_project("project-a")
        self.assertEqual(len(project["overlays"]), len(created))
        self.assertTrue(all("content_url_expires_at" in item for item in project["overlays"]))

    def test_patch_validates_geometry_and_updates_editable_properties(self) -> None:
        item = self.create_video()

        updated = main.api_update_overlay(
            item["id"],
            main.OverlayUpdate(
                x=0.1,
                y=0.2,
                width=0.4,
                height=0.225,
                object_fit="contain",
                z_index=7,
                video_controls=False,
            ),
        )

        self.assertEqual(updated["x"], 0.1)
        self.assertEqual(updated["object_fit"], "contain")
        self.assertEqual(updated["z_index"], 7)
        self.assertFalse(updated["video_controls"])

        with self.assertRaises(HTTPException) as raised:
            main.api_update_overlay(item["id"], main.OverlayUpdate(x=0.9))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(main.get_overlay(item["id"])["x"], 0.1)

        with self.assertRaises(HTTPException):
            main.default_overlay_geometry(main.get_page("image-page"), 0)

    def test_rejects_mismatched_or_invalid_media(self) -> None:
        cases = [
            upload("wrong.jpg", png_bytes(), "image/png"),
            upload("broken.png", b"not a png", "image/png"),
        ]
        for candidate in cases:
            with self.subTest(filename=candidate.filename):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        main.api_create_overlay(
                            "image-page", file=candidate, storage_backend="local"
                        )
                    )
                self.assertEqual(raised.exception.status_code, 400)

    def test_content_gateway_and_delete_remove_local_asset(self) -> None:
        item = self.create_image()
        path = main.local_asset_path(item["storage_key"])
        _, _, overlay_id, token, asset_name = item["content_url"].split("/", 4)
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": item["content_url"],
                "headers": [],
            }
        )

        response = main.overlay_asset_content(
            request,
            overlay_id=overlay_id,
            token=token,
            asset_name=asset_name,
        )

        self.assertIsInstance(response, FileResponse)
        self.assertEqual(Path(response.path), path)
        main.api_delete_overlay(item["id"])
        self.assertFalse(path.exists())
        with self.assertRaises(HTTPException):
            main.get_overlay(item["id"])

    def test_page_and_project_delete_cleanup_overlay_assets(self) -> None:
        page_overlay = self.create_image("image-page")
        project_overlay = self.create_video("html-page")
        page_path = main.local_asset_path(page_overlay["storage_key"])
        project_path = main.local_asset_path(project_overlay["storage_key"])

        main.api_delete_page("image-page")

        self.assertFalse(page_path.exists())
        with self.assertRaises(HTTPException):
            main.get_overlay(page_overlay["id"])

        main.api_delete_project("project-a")

        self.assertFalse(project_path.exists())
        with self.assertRaises(HTTPException):
            main.get_overlay(project_overlay["id"])

    @patch("app.main.object_storage")
    @patch("app.main.s3_configured", return_value=True)
    def test_s3_overlay_delete_uses_object_storage(
        self, _configured, storage_factory
    ) -> None:
        overlay = {"storage_backend": "s3", "storage_key": "uipm/p/o/media.mp4"}

        main.delete_overlay_asset(overlay)

        storage_factory.return_value.delete.assert_called_once_with(
            "uipm/p/o/media.mp4"
        )


if __name__ == "__main__":
    unittest.main()
