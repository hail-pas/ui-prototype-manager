from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

import app.main as main


class InteractionUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_paths = (main.DATA_DIR, main.DB_PATH, main.ASSET_DIR)
        main.DATA_DIR = Path(self.temp_dir.name)
        main.DB_PATH = main.DATA_DIR / "app.db"
        main.ASSET_DIR = main.DATA_DIR / "assets"
        main.init_db()

        created_at = main.now_iso()
        with main.db() as connection:
            connection.executemany(
                "INSERT INTO projects(id, name, created_at) VALUES (?, ?, ?)",
                [("project-a", "Project A", created_at), ("project-b", "Project B", created_at)],
            )
            connection.executemany(
                """
                INSERT INTO pages(
                    id, project_id, name, type, storage_backend,
                    storage_prefix, entry_path, created_at
                )
                VALUES (?, ?, ?, 'image', 'local', ?, 'image.png', ?)
                """,
                [
                    ("source", "project-a", "Source", "source.png", created_at),
                    ("target-a", "project-a", "Target A", "target-a.png", created_at),
                    ("target-b", "project-b", "Target B", "target-b.png", created_at),
                ],
            )

    def tearDown(self) -> None:
        main.DATA_DIR, main.DB_PATH, main.ASSET_DIR = self.previous_paths
        self.temp_dir.cleanup()

    def create_interaction(self) -> dict:
        return main.api_create_interaction(
            main.InteractionCreate(
                name="Open target",
                source_page_id="source",
                action="navigate",
                target_page_id="target-a",
                kind="region",
                payload={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            )
        )

    def test_updates_name_action_and_target_together(self) -> None:
        interaction = self.create_interaction()

        updated = main.api_update_interaction(
            interaction["id"],
            main.InteractionUpdate(name="Go back", action="back", target_page_id=None),
        )

        self.assertEqual(updated["name"], "Go back")
        self.assertEqual(updated["action"], "back")
        self.assertIsNone(updated["target_page_id"])
        self.assertEqual(updated["payload"], interaction["payload"])

    def test_name_only_patch_preserves_navigation(self) -> None:
        interaction = self.create_interaction()

        updated = main.api_update_interaction(
            interaction["id"],
            main.InteractionUpdate(name="Renamed"),
        )

        self.assertEqual(updated["name"], "Renamed")
        self.assertEqual(updated["action"], "navigate")
        self.assertEqual(updated["target_page_id"], "target-a")

    def test_rejects_target_from_another_project(self) -> None:
        interaction = self.create_interaction()

        with self.assertRaises(HTTPException) as raised:
            main.api_update_interaction(
                interaction["id"],
                main.InteractionUpdate(action="navigate", target_page_id="target-b"),
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(main.get_interaction(interaction["id"])["target_page_id"], "target-a")

    def test_rejects_empty_patch(self) -> None:
        interaction = self.create_interaction()

        with self.assertRaises(HTTPException) as raised:
            main.api_update_interaction(interaction["id"], main.InteractionUpdate())

        self.assertEqual(raised.exception.status_code, 400)

    def test_rejects_duplicate_name_without_changing_existing_value(self) -> None:
        interaction = self.create_interaction()
        other = main.api_create_interaction(
            main.InteractionCreate(
                name="Other interaction",
                source_page_id="source",
                action="back",
                kind="region",
                payload={"x": 0.6, "y": 0.2, "width": 0.2, "height": 0.2},
            )
        )

        with self.assertRaises(HTTPException) as raised:
            main.api_update_interaction(
                interaction["id"],
                main.InteractionUpdate(name=other["name"]),
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(main.get_interaction(interaction["id"])["name"], "Open target")


class InjectedEditorTests(unittest.TestCase):
    def test_static_document_contains_bidirectional_overlay_protocol(self) -> None:
        rendered = main.instrument_html("page-1", "<body><button>Open</button></body>")

        self.assertIn("uipm-editor-state", rendered)
        self.assertIn("uipm-overlay-status", rendered)
        self.assertIn("__uipm_overlay_root", rendered)
        self.assertIn("uipm-element-hover", rendered)

    def test_mode_is_selected_by_url_at_runtime(self) -> None:
        rendered = main.instrument_html("page-1", "<body><button>Open</button></body>")

        self.assertIn("location.hash.slice(1)", rendered)
        self.assertIn("const EDIT_MODE = (hashMode || queryMode) === 'edit'", rendered)
        self.assertIn('html[data-uipm-mode="edit"] [data-ui-id]:hover', rendered)
        self.assertIn("uipm-element-click", rendered)


class PlayerOverlayTests(unittest.TestCase):
    def test_overlay_videos_are_configured_to_autoplay_and_loop(self) -> None:
        player_script = (Path(main.__file__).parent / "static" / "player.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("media.autoplay = true;", player_script)
        self.assertIn("media.loop = true;", player_script)

    def test_page_transition_waits_for_base_content_and_overlays(self) -> None:
        player_script = (Path(main.__file__).parent / "static" / "player.js").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "Promise.all([contentReady.promise, frameLoaded.promise, overlay.ready])",
            player_script,
        )
        self.assertIn("Promise.all([loadBaseImage(), overlay.ready])", player_script)
        self.assertIn("await afterStablePaint(signal);", player_script)
        self.assertNotIn("stage.innerHTML =", player_script)


class HtmlInstrumentationUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_paths = (main.DATA_DIR, main.DB_PATH, main.ASSET_DIR)
        main.DATA_DIR = Path(self.temp_dir.name).resolve()
        main.DB_PATH = main.DATA_DIR / "app.db"
        main.ASSET_DIR = main.DATA_DIR / "assets"
        main.init_db()

    def tearDown(self) -> None:
        main.DATA_DIR, main.DB_PATH, main.ASSET_DIR = self.previous_paths
        self.temp_dir.cleanup()

    def test_outdated_local_html_is_upgraded_once(self) -> None:
        created_at = main.now_iso()
        prefix = "assets/project-a/page-a"
        path = main.local_asset_path(f"{prefix}/index.html")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '<body><button>Open</button><style id="__uipm_style">old</style>'
            '<script id="__uipm_script">old</script></body>',
            encoding="utf-8",
        )
        with main.db() as connection:
            connection.execute(
                "INSERT INTO projects(id, name, created_at) VALUES (?, ?, ?)",
                ("project-a", "Project A", created_at),
            )
            connection.execute(
                """
                INSERT INTO pages(
                    id, project_id, name, type, storage_backend,
                    storage_prefix, entry_path, created_at
                ) VALUES (?, ?, ?, 'html', 'local', ?, 'index.html', ?)
                """,
                ("page-a", "project-a", "Page A", prefix, created_at),
            )
            connection.execute(
                """
                INSERT INTO page_assets(page_id, relative_path, media_type, size_bytes)
                VALUES ('page-a', 'index.html', 'text/html; charset=utf-8', ?)
                """,
                (path.stat().st_size,),
            )

        upgraded = main.ensure_html_instrumentation(main.get_page("page-a"))
        first_content = path.read_text(encoding="utf-8")
        upgraded_again = main.ensure_html_instrumentation(main.get_page("page-a"))

        self.assertEqual(
            upgraded["instrumentation_version"], main.HTML_INSTRUMENTATION_VERSION
        )
        self.assertEqual(
            upgraded_again["instrumentation_version"],
            upgraded["instrumentation_version"],
        )
        self.assertEqual(first_content, path.read_text(encoding="utf-8"))
        self.assertEqual(first_content.count('id="__uipm_script"'), 1)
        self.assertNotIn(">old</script>", first_content)
        self.assertIn("uipm-preview-key", first_content)


if __name__ == "__main__":
    unittest.main()
