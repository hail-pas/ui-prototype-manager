from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import app.main as main
from app.page_management import (
    ProjectDuplicateRequest,
    api_duplicate_project,
)


class ProjectCopyTests(unittest.TestCase):
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
                is_html = page_id == "page-a"
                page_type = "html" if is_html else "image"
                entry_path = "index.html" if is_html else "image.png"
                instrumentation_version = (
                    main.HTML_INSTRUMENTATION_VERSION if is_html else 0
                )
                connection.execute(
                    """
                    INSERT INTO pages(
                        id, project_id, name, type, storage_backend, storage_prefix,
                        entry_path, instrumentation_version, sort_order, created_at
                    ) VALUES (?, 'project-a', ?, ?, 'local', ?, ?, ?, ?, ?)
                    """,
                    (
                        page_id,
                        f"Page {index + 1}",
                        page_type,
                        prefix,
                        entry_path,
                        instrumentation_version,
                        index,
                        created_at,
                    ),
                )
                path = main.local_asset_path(f"{prefix}/{entry_path}")
                path.parent.mkdir(parents=True, exist_ok=True)
                data = (
                    main.prepare_html_asset(
                        page_id, b"<html><body><button>Next</button></body></html>"
                    )
                    if is_html
                    else f"asset-{page_id}".encode()
                )
                path.write_bytes(data)
                connection.execute(
                    """
                    INSERT INTO page_assets(page_id, relative_path, media_type, size_bytes)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        page_id,
                        entry_path,
                        "text/html; charset=utf-8" if is_html else "image/png",
                        path.stat().st_size,
                    ),
                )

            connection.execute(
                """
                INSERT INTO interactions(
                    id, project_id, name, source_page_id, action, target_page_id,
                    target_url, kind, payload_json, created_at
                ) VALUES (
                    'interaction-a', 'project-a', 'Go next', 'page-a',
                    'navigate', 'page-b', NULL, 'region',
                    '{"x":0.1,"y":0.1,"width":0.2,"height":0.2}', ?
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
                    0.1, 0.1, 0.2, 0.2, 1, 'cover', 1, 1, ?, ?
                )
                """,
                (overlay_path.stat().st_size, created_at, created_at),
            )

    def tearDown(self) -> None:
        main.DATA_DIR, main.DB_PATH, main.ASSET_DIR = self.previous_paths
        if self.previous_key is None:
            os.environ.pop("UIPM_ACCESS_KEY", None)
        else:
            os.environ["UIPM_ACCESS_KEY"] = self.previous_key
        self.temp_dir.cleanup()

    def test_project_deep_copy_is_independent(self) -> None:
        result = api_duplicate_project(
            "project-a", ProjectDuplicateRequest(name="Project A copy")
        )
        copied_project_id = result["project"]["id"]
        self.assertNotEqual(copied_project_id, "project-a")
        self.assertEqual(
            result["copied"],
            {"pages": 2, "assets": 2, "interactions": 1, "overlays": 1},
        )

        copied = main.api_project(copied_project_id)
        source = main.api_project("project-a")
        self.assertEqual([page["name"] for page in copied["pages"]], ["Page 1", "Page 2"])
        self.assertTrue(
            set(page["id"] for page in copied["pages"]).isdisjoint(
                page["id"] for page in source["pages"]
            )
        )

        copied_pages = {page["name"]: page for page in copied["pages"]}
        copied_interaction = copied["interactions"][0]
        self.assertNotEqual(copied_interaction["id"], "interaction-a")
        self.assertEqual(copied_interaction["source_page_id"], copied_pages["Page 1"]["id"])
        self.assertEqual(copied_interaction["target_page_id"], copied_pages["Page 2"]["id"])

        source_page = main.get_page("page-a")
        copied_page = main.get_page(copied_pages["Page 1"]["id"])
        self.assertNotEqual(source_page["storage_prefix"], copied_page["storage_prefix"])
        source_asset = main.local_asset_path(main.asset_storage_key(source_page, "index.html"))
        copied_asset = main.local_asset_path(main.asset_storage_key(copied_page, "index.html"))
        source_html = source_asset.read_text(encoding="utf-8")
        copied_html = copied_asset.read_text(encoding="utf-8")
        self.assertIn('"page-a"', source_html)
        self.assertIn(f'"{copied_page["id"]}"', copied_html)
        self.assertNotIn('"page-a"', copied_html)
        copied_asset.write_bytes(b"changed-copy")
        self.assertIn('"page-a"', source_asset.read_text(encoding="utf-8"))

        copied_overlay = copied["overlays"][0]
        self.assertNotEqual(copied_overlay["id"], "overlay-a")
        self.assertEqual(copied_overlay["page_id"], copied_pages["Page 1"]["id"])
        self.assertNotEqual(
            copied_overlay["storage_key"],
            "assets/project-a/overlays/overlay-a.png",
        )
        copied_overlay_path = main.local_asset_path(copied_overlay["storage_key"])
        copied_overlay_path.write_bytes(b"changed-overlay")
        self.assertEqual(
            main.local_asset_path(
                "assets/project-a/overlays/overlay-a.png"
            ).read_bytes(),
            b"overlay-original",
        )


if __name__ == "__main__":
    unittest.main()
