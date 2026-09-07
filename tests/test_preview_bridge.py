from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app import preview_bridge


ROOT = Path(__file__).resolve().parents[1]


class PreviewBridgeTests(unittest.TestCase):
    @staticmethod
    def core(project_id: str = "project-a") -> SimpleNamespace:
        return SimpleNamespace(
            token_secret=lambda: b"preview-bridge-test-secret",
            get_page=lambda page_id: {
                "id": page_id,
                "project_id": project_id,
            },
        )

    def test_bridge_token_is_stable_and_bound_to_project_and_page(self) -> None:
        with patch("app.preview_bridge._core", return_value=self.core()):
            token = preview_bridge.create_bridge_token("project-a", "page-a")
            self.assertEqual(
                token,
                preview_bridge.create_bridge_token("project-a", "page-a"),
            )
            self.assertTrue(
                preview_bridge.valid_bridge_token("project-a", "page-a", token)
            )
            self.assertFalse(
                preview_bridge.valid_bridge_token("project-a", "page-b", token)
            )
            self.assertFalse(
                preview_bridge.valid_bridge_token("project-b", "page-a", token)
            )

    def test_bridge_page_only_posts_navigation_message_for_matching_page(self) -> None:
        with patch("app.preview_bridge._core", return_value=self.core()):
            token = preview_bridge.create_bridge_token("project-a", "page-a")
            response = preview_bridge.preview_bridge("project-a", "page-a", token)

        body = response.body.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type":"uipm-external-navigate"', body)
        self.assertIn('"projectId":"project-a"', body)
        self.assertIn('"pageId":"page-a"', body)
        self.assertIn("window.parent.postMessage(payload, '*')", body)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_bridge_page_rejects_invalid_token(self) -> None:
        with patch("app.preview_bridge._core", return_value=self.core()):
            with self.assertRaises(HTTPException) as raised:
                preview_bridge.preview_bridge("project-a", "page-a", "invalid")
        self.assertEqual(raised.exception.status_code, 404)

    def test_bridge_page_rejects_page_from_another_project(self) -> None:
        with patch("app.preview_bridge._core", return_value=self.core("project-b")):
            token = preview_bridge.create_bridge_token("project-a", "page-a")
            with self.assertRaises(HTTPException) as raised:
                preview_bridge.preview_bridge("project-a", "page-a", token)
        self.assertEqual(raised.exception.status_code, 404)

    def test_bridge_url_api_returns_absolute_stable_url(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": "https",
                "server": ("uipm.example", 443),
                "path": "/api/pages/page-a/bridge-url",
                "root_path": "",
                "query_string": b"",
                "headers": [],
                "router": preview_bridge.router,
            }
        )
        with patch("app.preview_bridge._core", return_value=self.core()):
            first = preview_bridge.api_page_bridge_url(request, "page-a")
            second = preview_bridge.api_page_bridge_url(request, "page-a")

        self.assertEqual(first, second)
        self.assertEqual(first["project_id"], "project-a")
        self.assertEqual(first["page_id"], "page-a")
        self.assertTrue(
            first["bridge_url"].startswith(
                "https://uipm.example/content/preview-bridge/project-a/page-a/"
            )
        )

    def test_player_bridge_extension_waits_for_outgoing_view_to_leave_visual_stack(self) -> None:
        script = (ROOT / "app/static/preview-bridge-player.js").read_text(
            encoding="utf-8"
        )
        template = (ROOT / "app/templates/player.html").read_text(encoding="utf-8")
        package_init = (ROOT / "app/__init__.py").read_text(encoding="utf-8")

        self.assertIn("event.source !== bridgeExternalFrame.contentWindow", script)
        self.assertIn("targetProjectId !== bridgeRoot.dataset.projectId", script)
        self.assertIn("!page(targetPageId)", script)
        self.assertIn("!externalOpen", script)
        self.assertIn("targetPageId === currentPageId", script)
        self.assertIn("const sourcePageId = currentPageId", script)
        self.assertIn("await navigation.navigate(targetPageId)", script)
        self.assertIn("waitForOutgoingPageToSettle", script)
        self.assertIn("view.classList.contains('is-cached')", script)
        self.assertIn("new MutationObserver", script)
        self.assertIn("await waitForOutgoingPageToSettle(sourcePageId)", script)
        self.assertIn("if (externalOpen) closeExternalPage();", script)

        navigate_index = script.index("await navigation.navigate(targetPageId)")
        settle_index = script.index("await waitForOutgoingPageToSettle(sourcePageId)")
        close_index = script.index("if (externalOpen) closeExternalPage();")
        self.assertLess(navigate_index, settle_index)
        self.assertLess(settle_index, close_index)
        self.assertIn("preview-bridge-player.js?v=20260907-preview-bridge-v3", template)
        self.assertIn("preview_bridge as _preview_bridge", package_init)


if __name__ == "__main__":
    unittest.main()
