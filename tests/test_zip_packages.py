from __future__ import annotations

import asyncio
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urljoin, urlsplit

from fastapi import HTTPException, Request, UploadFile

import app.main as main


def zip_upload(entries: dict[str, bytes], filename: str = "demo.zip") -> UploadFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    buffer.seek(0)
    return UploadFile(filename=filename, file=buffer)


def request_content(url: str, range_header: str | None = None) -> tuple[int, dict[str, str], bytes]:
    parts = urlsplit(url).path.strip("/").split("/", 3)
    _, page_id, token, asset_path = parts
    headers = [] if range_header is None else [(b"range", range_header.encode("ascii"))]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": urlsplit(url).path,
        "raw_path": urlsplit(url).path.encode("utf-8"),
        "query_string": b"",
        "headers": headers,
        "client": ("test", 1234),
        "server": ("testserver", 80),
    }
    response = main.page_asset_content(
        Request(scope),
        page_id=page_id,
        token=token,
        asset_path=asset_path,
    )
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    asyncio.run(response(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        key.decode("latin-1"): value.decode("latin-1")
        for key, value in start["headers"]
    }
    return int(start["status"]), response_headers, body


class ZipPagePackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_paths = (main.DATA_DIR, main.DB_PATH, main.ASSET_DIR)
        main.DATA_DIR = Path(self.temp_dir.name).resolve()
        main.DB_PATH = main.DATA_DIR / "app.db"
        main.ASSET_DIR = main.DATA_DIR / "assets"
        self.env = patch.dict(os.environ, {"UIPM_ACCESS_KEY": "test-secret"})
        self.env.start()
        main.init_db()
        with main.db() as connection:
            connection.execute(
                "INSERT INTO projects(id, name, created_at) VALUES (?, ?, ?)",
                ("project-a", "Project A", main.now_iso()),
            )

    def tearDown(self) -> None:
        self.env.stop()
        main.DATA_DIR, main.DB_PATH, main.ASSET_DIR = self.previous_paths
        self.temp_dir.cleanup()

    def upload(self, archive: UploadFile) -> dict:
        pages = asyncio.run(
            main.api_upload_pages(
                "project-a",
                storage_backend="local",
                names_json='["Demo"]',
                render_mode="auto",
                viewport_width=1920,
                viewport_height=1080,
                files=[archive],
            )
        )
        return pages[0]

    def test_upload_preserves_relative_tree_and_serves_range_requests(self) -> None:
        page = self.upload(
            zip_upload(
                {
                    "index.html": (
                        b"<html><head><link rel='stylesheet' href='./css/app.css'></head>"
                        b"<body><img src='./images/logo.png'><video src='./video/demo.mp4'></video>"
                        b"</body></html>"
                    ),
                    "css/app.css": b"body{background-image:url('../images/logo.png')}",
                    "images/logo.png": b"not-a-real-png",
                    "video/demo.mp4": b"0123456789",
                }
            )
        )

        prefix = main.local_asset_path(page["storage_prefix"])
        self.assertTrue((prefix / "css/app.css").is_file())
        self.assertTrue((prefix / "images/logo.png").is_file())
        rendered = (prefix / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="__uipm_script"', rendered)

        serialized = main.page_to_api(page)
        status, _, body = request_content(serialized["content_url"])
        self.assertEqual(status, 200)
        self.assertIn(b"./images/logo.png", body)

        image_url = urljoin(serialized["content_url"], "images/logo.png")
        status, _, body = request_content(image_url)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"not-a-real-png")

        video_url = urljoin(serialized["content_url"], "video/demo.mp4")
        status, headers, body = request_content(video_url, "bytes=2-5")
        self.assertEqual(status, 206)
        self.assertEqual(body, b"2345")
        self.assertEqual(headers["content-range"], "bytes 2-5/10")

    def test_single_wrapper_directory_is_removed(self) -> None:
        page = self.upload(
            zip_upload(
                {
                    "demo/index.html": b"<body><img src='./assets/a.png'></body>",
                    "demo/assets/a.png": b"image",
                }
            )
        )

        prefix = main.local_asset_path(page["storage_prefix"])
        self.assertTrue((prefix / "index.html").is_file())
        self.assertTrue((prefix / "assets/a.png").is_file())
        self.assertFalse((prefix / "demo").exists())

    def test_rejects_unsafe_or_ambiguous_archives(self) -> None:
        cases = [
            {"index.html": b"<body></body>", "../secret.txt": b"secret"},
            {"home.html": b"<body></body>"},
            {"index.html": b"<body></body>", "other.html": b"<body></body>"},
        ]
        for entries in cases:
            with self.subTest(entries=list(entries)):
                with self.assertRaises(HTTPException) as raised:
                    self.upload(zip_upload(entries))
                self.assertEqual(raised.exception.status_code, 400)

    def test_deleting_page_removes_the_whole_package(self) -> None:
        page = self.upload(
            zip_upload(
                {
                    "index.html": b"<body></body>",
                    "assets/a.txt": b"asset",
                }
            )
        )
        prefix = main.local_asset_path(page["storage_prefix"])
        self.assertTrue(prefix.exists())

        main.api_delete_page(page["id"])

        self.assertFalse(prefix.exists())
        with self.assertRaises(HTTPException):
            main.get_page(page["id"])


if __name__ == "__main__":
    unittest.main()
