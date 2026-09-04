from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

import app.main as main
from app.page_management import (
    PageDuplicateRequest,
    ProjectUpdate,
    api_duplicate_page,
    api_update_project,
)


class PageCopyTests(unittest.TestCase):
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
            for index, page_id in enumerate(("page-a", "page-b")):
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
                path.write_bytes(f"image-{page_id}".encode())
                connection.execute(
                    """
                    INSERT INTO page_assets(page_id, relative_path, media_type, size_bytes)
                    VALUES (?, 'image.png', 'image/png', ?)
                    """,
                    (page_id, path.stat().st_size),
                )

            connection.execute(
                """
                INSERT INTO interactions(
                    id, project_id, name, source_page_id, action, target_page_id,
                    target_url, kind, payload_json, created_at
                ) VALUES (
                    'interaction-self', 'project-a', 'Self link', 'page-a',
                    'navigate', 'page-a', NULL, 'region',
                    '{"x":0.1,"y":0.1,"width":0.2,"height":0.2}', ?
                )
                """,
                (created_at,),
            )
            connection.execute(
                """
                INSERT INTO interactions(
                    id, project_id, name, source_page_id, action, target_page_id,
                    target_url, kind, payload_json, created_at
                ) VALUES (
                    'interaction-next', 'project-a', 'Next page', 'page-a',
                    'navigate', 'page-b', NULL, 'region',
                    '{"x":0.4,"y":0.4,"width":0.2,"height":0.2}', ?
                )
                """,
                (created_at,),
            )

            overlay_path = main.local_asset_path(
                "assets/project-a/overlays/overlay-a.png"
            )
            overlay_path.parent.mkdir(parents=True, exist_ok=True)
            overlay_path.write_bytes(b"overlay-original")
            connection.execute(
                """
                INSERT INTO overlays(
                    id, project_id, page_id, type, storage_backend, storage_key,
                    media_type, size_bytes, x, y, width, height, aspect_ratio,
                    object_fit, z_index, video_controls, created_at, updated_at
                ) VALUES (
                    'overlay-a', 'project-a', 'page-a', 'image', 'local',
                    'assets/project-a/overlays/overlay-a.png', 'image/png', ?,
                    0.2, 0.2, 0.3, 0.3, 1, 'contain', 3, 1, ?, ?
                )
                """,
                (overlay_path.stat().st_size, created_at, created_at),
            )
            connection.execute(
                """
                INSERT INTO overlays(
                    id, project_id, page_id, type, storage_backend, storage_key,
                    media_type, size_bytes, x, y, width, height, aspect_ratio,
                    object_fit, z_index, video_controls, created_at, updated_at
                ) VALUES (
                    'overlay-url', 'project-a', 'page-a', 'image', 'url',
                    'https://example.com/example.png', 'image/png', 0,
                    0.1, 0.1, 0.2, 0.2, 1, 'cover', 4, 1, ?, ?
                )
                """,
                (created_at, created_at),
            )

    def tearDown(self) -> None:
        main.DATA_DIR, main.DB_PATH, main.ASSET_DIR = self.previous_paths
        if self.previous_key is None:
            os.environ.pop("UIPM_ACCESS_KEY", None)
        else:
            os.environ["UIPM_ACCESS_KEY"] = self.previous_key
        self.temp_dir.cleanup()

    def test_project_can_be_renamed(self) -> None:
        updated = api_update_project("project-a", ProjectUpdate(name="  Renamed   Project  "))
        self.assertEqual(updated["name"], "Renamed Project")
        self.assertEqual(main.get_project("project-a")["name"], "Renamed Project")

    def test_image_page_deep_copy_is_independent(self) -> None:
        result = api_duplicate_page(
            "page-a", PageDuplicateRequest(name="Page 1 copy")
        )
        copied_page = result["page"]
        copied_id = copied_page["id"]

        self.assertNotEqual(copied_id, "page-a")
        self.assertEqual(copied_page["name"], "Page 1 copy")
        self.assertEqual(result["copied"], {"assets": 1, "interactions": 2, "overlays": 2})

        project = main.api_project("project-a")
        self.assertEqual(
            [page["id"] for page in project["pages"]],
            ["page-a", copied_id, "page-b"],
        )

        source_page = main.get_page("page-a")
        copied_page_row = main.get_page(copied_id)
        self.assertNotEqual(source_page["storage_prefix"], copied_page_row["storage_prefix"])
        source_asset = main.local_asset_path(
            main.asset_storage_key(source_page, "image.png")
        )
        copied_asset = main.local_asset_path(
            main.asset_storage_key(copied_page_row, "image.png")
        )
        self.assertEqual(source_asset.read_bytes(), copied_asset.read_bytes())
        copied_asset.write_bytes(b"changed-copy")
        self.assertEqual(source_asset.read_bytes(), b"image-page-a")

        with main.db() as connection:
            copied_interactions = connection.execute(
                """
                SELECT * FROM interactions
                WHERE source_page_id = ? ORDER BY name
                """,
                (copied_id,),
            ).fetchall()
            self.assertEqual(len(copied_interactions), 2)
            self.assertTrue(all(row["id"] not in {"interaction-self", "interaction-next"} for row in copied_interactions))
            self_link = next(row for row in copied_interactions if row["name"].startswith("Self link copy"))
            next_link = next(row for row in copied_interactions if row["name"].startswith("Next page copy"))
            self.assertEqual(self_link["target_page_id"], copied_id)
            self.assertEqual(next_link["target_page_id"], "page-b")

            copied_overlays = connection.execute(
                "SELECT * FROM overlays WHERE page_id = ? ORDER BY z_index",
                (copied_id,),
            ).fetchall()
            self.assertEqual(len(copied_overlays), 2)
            self.assertTrue(all(row["id"] not in {"overlay-a", "overlay-url"} for row in copied_overlays))

        local_overlay = next(
            row for row in copied_overlays if row["storage_backend"] == "local"
        )
        self.assertNotEqual(
            local_overlay["storage_key"],
            "assets/project-a/overlays/overlay-a.png",
        )
        copied_overlay_path = main.local_asset_path(local_overlay["storage_key"])
        original_overlay_path = main.local_asset_path(
            "assets/project-a/overlays/overlay-a.png"
        )
        self.assertEqual(copied_overlay_path.read_bytes(), original_overlay_path.read_bytes())
        copied_overlay_path.write_bytes(b"changed-overlay")
        self.assertEqual(original_overlay_path.read_bytes(), b"overlay-original")

        url_overlay = next(
            row for row in copied_overlays if row["storage_backend"] == "url"
        )
        self.assertEqual(url_overlay["storage_key"], "https://example.com/example.png")

    def test_duplicate_name_is_rejected_before_copying(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            api_duplicate_page("page-a", PageDuplicateRequest(name="Page 2"))
        self.assertEqual(raised.exception.status_code, 409)

    def test_html_page_copy_is_explicitly_unsupported(self) -> None:
        with main.db() as connection:
            connection.execute("UPDATE pages SET type = 'html' WHERE id = 'page-a'")

        with self.assertRaises(HTTPException) as raised:
            api_duplicate_page("page-a", PageDuplicateRequest(name="HTML copy"))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("HTML", str(raised.exception.detail))


if __name__ == "__main__":
    unittest.main()
