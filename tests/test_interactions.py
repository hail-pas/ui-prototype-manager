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

    def test_creates_and_updates_external_link_interaction(self) -> None:
        interaction = main.api_create_interaction(
            main.InteractionCreate(
                name="Open documentation",
                source_page_id="source",
                action="external",
                target_url="HTTPS://EXAMPLE.COM/docs#start",
                kind="region",
                payload={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            )
        )

        self.assertEqual(interaction["action"], "external")
        self.assertIsNone(interaction["target_page_id"])
        self.assertEqual(interaction["target_url"], "https://example.com/docs#start")

        updated = main.api_update_interaction(
            interaction["id"],
            main.InteractionUpdate(
                action="navigate",
                target_page_id="target-a",
                target_url=None,
            ),
        )

        self.assertEqual(updated["action"], "navigate")
        self.assertEqual(updated["target_page_id"], "target-a")
        self.assertIsNone(updated["target_url"])

    def test_rejects_unsafe_external_link_urls(self) -> None:
        for index, url in enumerate(
            (
                "javascript:alert(1)",
                "/relative/path",
                "https://user:password@example.com/private",
            )
        ):
            with self.subTest(url=url), self.assertRaises(HTTPException) as raised:
                main.api_create_interaction(
                    main.InteractionCreate(
                        name=f"Unsafe link {index}",
                        source_page_id="source",
                        action="external",
                        target_url=url,
                        kind="region",
                        payload={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
                    )
                )
            self.assertEqual(raised.exception.status_code, 400)

    def test_init_db_migrates_existing_interactions_for_external_links(self) -> None:
        created_at = main.now_iso()
        with main.db() as connection:
            connection.execute("DROP INDEX uq_interactions_project_name")
            connection.execute("DROP INDEX idx_interactions_project")
            connection.execute("DROP INDEX idx_interactions_source")
            connection.execute("DROP INDEX idx_interactions_target")
            connection.execute("DROP TABLE interactions")
            connection.execute(
                """
                CREATE TABLE interactions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    source_page_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('navigate', 'back')),
                    target_page_id TEXT,
                    kind TEXT NOT NULL CHECK(kind IN ('element', 'region')),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    CHECK (
                        (action = 'back' AND target_page_id IS NULL)
                        OR (action = 'navigate' AND target_page_id IS NOT NULL)
                    ),
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(source_page_id) REFERENCES pages(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_page_id) REFERENCES pages(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                INSERT INTO interactions(
                    id, project_id, name, source_page_id, action, target_page_id,
                    kind, payload_json, created_at
                ) VALUES (?, 'project-a', 'Legacy link', 'source', 'navigate',
                    'target-a', 'region', ?, ?)
                """,
                (
                    "legacy-interaction",
                    '{"x":0.1,"y":0.2,"width":0.3,"height":0.4}',
                    created_at,
                ),
            )

        main.init_db()

        migrated = main.get_interaction("legacy-interaction")
        self.assertEqual(migrated["action"], "navigate")
        self.assertEqual(migrated["target_page_id"], "target-a")
        self.assertIsNone(migrated["target_url"])
        external = main.api_create_interaction(
            main.InteractionCreate(
                name="New external link",
                source_page_id="source",
                action="external",
                target_url="https://example.com",
                kind="region",
                payload={"x": 0.5, "y": 0.2, "width": 0.2, "height": 0.2},
            )
        )
        self.assertEqual(external["action"], "external")


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

    def test_editor_validates_and_creates_direct_link_overlays(self) -> None:
        static_dir = Path(main.__file__).parent / "static"
        template_dir = Path(main.__file__).parent / "templates"
        editor_script = (static_dir / "editor.js").read_text(encoding="utf-8")
        editor_template = (template_dir / "editor.html").read_text(encoding="utf-8")

        self.assertIn('id="overlayLinkButton"', editor_template)
        self.assertIn('id="overlayLinkDialog"', editor_template)
        self.assertIn("inspectOverlayLink(url, type)", editor_script)
        self.assertIn("/overlays/from-url", editor_script)
        self.assertIn("media.referrerPolicy = 'no-referrer';", editor_script)

    def test_external_links_render_in_a_full_page_frame_with_return_fallback(self) -> None:
        static_dir = Path(main.__file__).parent / "static"
        template_dir = Path(main.__file__).parent / "templates"
        player_script = (static_dir / "player.js").read_text(encoding="utf-8")
        stylesheet = (static_dir / "app.css").read_text(encoding="utf-8")
        player_template = (template_dir / "player.html").read_text(encoding="utf-8")
        editor_script = (static_dir / "editor.js").read_text(encoding="utf-8")

        self.assertIn('id="externalLayer"', player_template)
        self.assertIn('id="externalFrame"', player_template)
        self.assertIn('id="externalReturnBtn"', player_template)
        self.assertIn("function openExternalPage(url)", player_script)
        self.assertIn("function closeExternalPage()", player_script)
        self.assertIn("interaction.action === 'external'", player_script)
        self.assertIn("可能加载较慢或被安全策略阻止", player_script)
        self.assertIn(".player-external-return-dock:hover", stylesheet)
        self.assertIn("width:32px;overflow:hidden", stylesheet)
        self.assertIn(".player-external-return-icon", stylesheet)
        self.assertNotIn("页面空白时也可返回原型", player_template)
        self.assertIn('value="external"', editor_script)


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
