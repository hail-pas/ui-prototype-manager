from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException, UploadFile

import app.main as main


class PageManagementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_paths = (main.DATA_DIR, main.DB_PATH, main.ASSET_DIR)
        self.previous_key = os.environ.get("UIPM_ACCESS_KEY")
        os.environ["UIPM_ACCESS_KEY"] = "test-secret"
        main.DATA_DIR = Path(self.temp_dir.name).resolve()
        main.DB_PATH = main.DATA_DIR / "app.db"
        main.ASSET_DIR = main.DATA_DIR / "assets"
        main.init_db()

        created_at = main.now_iso()
        with main.db() as connection:
            connection.execute(
                "INSERT INTO projects(id, name, created_at) VALUES ('project-a', 'Project A', ?)",
                (created_at,),
            )
            for index, page_id in enumerate(("page-a", "page-b", "page-c")):
                prefix = f"assets/project-a/{page_id}"
                connection.execute(
                    """
                    INSERT INTO pages(
                        id, project_id, name, type, storage_backend, storage_prefix,
                        entry_path, sort_order, created_at
                    ) VALUES (?, 'project-a', ?, 'image', 'local', ?, 'image.png', ?, ?)
                    """,
                    (page_id, f"Page {index + 1}", prefix, index, created_at),
                )
                path = main.local_asset_path(f"{prefix}/image.png")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"old-{page_id}".encode())
                connection.execute(
                    """
                    INSERT INTO page_assets(page_id, relative_path, media_type, size_bytes)
                    VALUES (?, 'image.png', 'image/png', ?)
                    """,
                    (page_id, path.stat().st_size),
                )

    def tearDown(self) -> None:
        main.DATA_DIR, main.DB_PATH, main.ASSET_DIR = self.previous_paths
        if self.previous_key is None:
            os.environ.pop("UIPM_ACCESS_KEY", None)
        else:
            os.environ["UIPM_ACCESS_KEY"] = self.previous_key
        self.temp_dir.cleanup()

    def test_page_order_requires_and_persists_complete_project_order(self) -> None:
        result = main.api_update_page_order(
            "project-a", main.PageOrderUpdate(page_ids=["page-c", "page-a", "page-b"])
        )

        self.assertEqual(result["page_ids"], ["page-c", "page-a", "page-b"])
        self.assertEqual(
            [page["id"] for page in main.api_project("project-a")["pages"]],
            ["page-c", "page-a", "page-b"],
        )

        with self.assertRaises(HTTPException) as raised:
            main.api_update_page_order(
                "project-a", main.PageOrderUpdate(page_ids=["page-a", "page-b"])
            )
        self.assertEqual(raised.exception.status_code, 400)

    async def test_replace_image_keeps_page_relationships_and_removes_old_asset(self) -> None:
        created_at = main.now_iso()
        with main.db() as connection:
            connection.execute(
                """
                INSERT INTO interactions(
                    id, project_id, name, source_page_id, action, target_page_id,
                    kind, payload_json, created_at
                ) VALUES (
                    'interaction-a', 'project-a', 'Open next', 'page-a', 'navigate',
                    'page-b', 'region', '{"x":0.1,"y":0.1,"width":0.2,"height":0.2}', ?
                )
                """,
                (created_at,),
            )
            connection.execute(
                """
                INSERT INTO overlays(
                    id, project_id, page_id, type, storage_backend, storage_key,
                    media_type, size_bytes, x, y, width, height, aspect_ratio,
                    object_fit, z_index, video_controls, created_at, updated_at
                ) VALUES (
                    'overlay-a', 'project-a', 'page-a', 'image', 'url',
                    'https://example.com/overlay.png', 'image/png', 0,
                    0.2, 0.2, 0.2, 0.2, 1, 'cover', 0, 1, ?, ?
                )
                """,
                (created_at, created_at),
            )

        old_path = main.local_asset_path("assets/project-a/page-a/image.png")
        upload = UploadFile(filename="replacement.webp", file=io.BytesIO(b"new-image"))
        updated = await main.api_replace_page_image("page-a", upload)

        self.assertEqual(updated["id"], "page-a")
        self.assertTrue(updated["entry_path"].endswith(".webp"))
        self.assertFalse(old_path.exists())
        self.assertEqual(
            main.read_page_asset(main.get_page("page-a")),
            b"new-image",
        )
        with main.db() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS n FROM interactions WHERE source_page_id = 'page-a'"
                ).fetchone()["n"],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS n FROM overlays WHERE page_id = 'page-a'"
                ).fetchone()["n"],
                1,
            )

    async def test_replace_image_rejects_non_image_pages(self) -> None:
        with main.db() as connection:
            connection.execute("UPDATE pages SET type = 'html' WHERE id = 'page-a'")
        upload = UploadFile(filename="replacement.png", file=io.BytesIO(b"image"))

        with self.assertRaises(HTTPException) as raised:
            await main.api_replace_page_image("page-a", upload)

        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
