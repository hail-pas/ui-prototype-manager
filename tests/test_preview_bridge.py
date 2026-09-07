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

    @staticmethod
    def request(path: str = "/", scheme: str = "https") -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": scheme,
                "server": ("uipm.example", 443 if scheme == "https" else 80),
                "path": path,
                "root_path": "",
                "query_string": b"",
                "headers": [],
                "router": preview_bridge.router,
            }
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

    def test_bridge_response_keeps_current_document_and_sets_short_lived_command(self) -> None:
        request = self.request(
            "/content/preview-bridge/project-a/page-a/token",
            scheme="https",
        )
        with patch("app.preview_bridge._core", return_value=self.core()):
            token = preview_bridge.create_bridge_token("project-a", "page-a")
            response = preview_bridge.preview_bridge(
                request,
                "project-a",
                "page-a",
                token,
            )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.body, b"")
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        cookie = response.headers.get("set-cookie", "")
        self.assertIn("uipm_preview_bridge=", cookie)
        self.assertIn("Max-Age=30", cookie)
        self.assertIn("Path=/", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn("Secure", cookie)
        self.assertNotIn("正在返回预览", response.body.decode("utf-8"))

    def test_bridge_response_uses_non_secure_command_cookie_on_http_localhost(self) -> None:
        request = self.request(scheme="http")
        with patch("app.preview_bridge._core", return_value=self.core()):
            token = preview_bridge.create_bridge_token("project-a", "page-a")
            response = preview_bridge.preview_bridge(
                request,
                "project-a",
                "page-a",
                token,
            )
        self.assertNotIn("Secure", response.headers.get("set-cookie", ""))

    def test_bridge_page_rejects_invalid_token(self) -> None:
        with patch("app.preview_bridge._core", return_value=self.core()):
            with self.assertRaises(HTTPException) as raised:
                preview_bridge.preview_bridge(
                    self.request(),
                    "project-a",
                    "page-a",
                    "invalid",
                )
        self.assertEqual(raised.exception.status_code, 404)

    def test_bridge_page_rejects_page_from_another_project(self) -> None:
        with patch("app.preview_bridge._core", return_value=self.core("project-b")):
            token = preview_bridge.create_bridge_token("project-a", "page-a")
            with self.assertRaises(HTTPException) as raised:
                preview_bridge.preview_bridge(
                    self.request(),
                    "project-a",
                    "page-a",
                    token,
                )
        self.assertEqual(raised.exception.status_code, 404)

    def test_bridge_url_api_returns_absolute_stable_url(self) -> None:
        request = self.request("/api/pages/page-a/bridge-url")
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

    def test_player_bridge_keeps_cover_until_target_has_painted(self) -> None:
        script = (ROOT / "app/static/preview-bridge-player.js").read_text(
            encoding="utf-8"
        )
        template = (ROOT / "app/templates/player.html").read_text(encoding="utf-8")
        package_init = (ROOT / "app/__init__.py").read_text(encoding="utf-8")
        bridge_source = (ROOT / "app/preview_bridge.py").read_text(encoding="utf-8")

        self.assertIn("event.source !== bridgeExternalFrame.contentWindow", script)
        self.assertIn("BRIDGE_COOKIE_NAME = 'uipm_preview_bridge'", script)
        self.assertIn("document.cookie", script)
        self.assertIn("window.setInterval", script)
        self.assertIn("decodeBridgeCommand", script)
        self.assertIn("targetProjectId !== bridgeRoot.dataset.projectId", script)
        self.assertIn("!page(targetPageId)", script)
        self.assertIn("targetPageId === currentPageId", script)
        self.assertIn("const sourcePageId = currentPageId", script)
        self.assertIn("await navigation.navigate(targetPageId)", script)
        self.assertIn("await waitForOutgoingPageToSettle(sourcePageId)", script)
        self.assertIn("const painted = await waitForTargetPaint(targetPageId)", script)
        self.assertGreaterEqual(script.count("requestAnimationFrame"), 2)
        self.assertIn("targetView.classList.contains('is-active')", script)
        self.assertIn("if (painted && externalOpen", script)

        navigate_index = script.index("await navigation.navigate(targetPageId)")
        settle_index = script.index("await waitForOutgoingPageToSettle(sourcePageId)")
        paint_index = script.index("const painted = await waitForTargetPaint(targetPageId)")
        close_index = script.index("if (painted && externalOpen")
        self.assertLess(navigate_index, settle_index)
        self.assertLess(settle_index, paint_index)
        self.assertLess(paint_index, close_index)

        self.assertIn("response = Response(status_code=204)", bridge_source)
        self.assertNotIn("window.parent.postMessage", bridge_source)
        self.assertIn("preview-bridge-player.js?v=20260907-preview-bridge-v4", template)
        self.assertIn("preview_bridge as _preview_bridge", package_init)


if __name__ == "__main__":
    unittest.main()
