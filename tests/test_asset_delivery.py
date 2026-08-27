from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from app import object_storage as object_storage_module
from app.main import instrument_html, page_to_api, prepare_html_asset
from app.object_storage import (
    ObjectStorageConfigurationError,
    ObjectStorageSettings,
    OssObjectStorage,
    PresignedObject,
    S3ObjectStorage,
    object_storage_settings,
)


class HtmlInstrumentationTests(unittest.TestCase):
    def test_instrumentation_is_static_and_mode_comes_from_url(self) -> None:
        source = """<!doctype html><html><head>
        <meta http-equiv="Content-Security-Policy" content="script-src 'none'">
        </head><body><button>打开</button></body></html>"""

        result = instrument_html("page-123", source)

        self.assertNotIn("Content-Security-Policy", result)
        self.assertIn('const PAGE_ID = "page-123";', result)
        self.assertIn("new URLSearchParams(location.hash.slice(1))", result)
        self.assertIn("uipm-content-ready", result)
        self.assertEqual(result.count('id="__uipm_script"'), 1)
        self.assertLess(
            result.index("<button>打开</button>"), result.index('id="__uipm_script"')
        )

    def test_prepare_html_asset_normalizes_non_utf8_input(self) -> None:
        source = "<!doctype html><html><body><p>中文页面</p></body></html>".encode(
            "gb18030"
        )

        prepared = prepare_html_asset("page-456", source)

        decoded = prepared.decode("utf-8")
        self.assertIn("中文页面", decoded)
        self.assertIn('const PAGE_ID = "page-456";', decoded)

    def test_instrumentation_is_idempotent_and_contains_preview_key_bridge(self) -> None:
        first = instrument_html("page-789", "<body><input></body>")
        second = instrument_html("page-789", first)

        self.assertEqual(second.count('id="__uipm_style"'), 1)
        self.assertEqual(second.count('id="__uipm_script"'), 1)
        self.assertIn("uipm-preview-key", second)
        self.assertIn("event.key !== 'Escape'", second)
        self.assertNotIn("const INSTRUMENTATION_VERSION = __", second)


class PageSerializationTests(unittest.TestCase):
    def test_local_page_uses_application_content_route(self) -> None:
        page = {
            "id": "local-page",
            "type": "image",
            "storage_backend": "local",
            "storage_key": "assets/project/local-page.png",
        }

        result = page_to_api(page)

        self.assertEqual(result["content_url"], "/api/pages/local-page/file")
        self.assertIsNone(result["content_url_expires_at"])

    @patch("app.main.object_storage")
    def test_s3_page_uses_presigned_content_url(self, storage_factory) -> None:
        storage_factory.return_value.presign_get.return_value = PresignedObject(
            url="https://assets.example.com/page.html?signature=one",
            expires_at="2030-01-01T00:00:00+00:00",
        )
        page = {
            "id": "s3-page",
            "type": "html",
            "storage_backend": "s3",
            "storage_key": "uipm/project/s3-page.html",
        }
        env = {
            "UIPM_S3_BUCKET": "prototype-assets",
            "UIPM_S3_DIRECT_READ": "true",
        }

        with patch.dict(os.environ, env, clear=False):
            result = page_to_api(page)

        self.assertEqual(
            result["content_url"], "https://assets.example.com/page.html?signature=one"
        )
        self.assertEqual(result["content_url_expires_at"], "2030-01-01T00:00:00+00:00")
        storage_factory.return_value.presign_get.assert_called_once_with(
            page["storage_key"]
        )


class ObjectStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        object_storage_module._clear_presigned_url_cache()

    def settings(self, **overrides) -> ObjectStorageSettings:
        values = {
            "bucket": "prototype-assets",
            "provider": "s3",
            "endpoint_url": "https://storage.example.com",
            "browser_endpoint_url": "https://storage.example.com",
            "region": "us-east-1",
            "access_key": "test-access-key",
            "secret_key": "test-secret-key",
            "prefix": "uipm",
            "addressing_style": "virtual",
            "signature_version": "s3v4",
            "direct_read": True,
            "presign_ttl_seconds": 3600,
            "browser_use_cname": False,
        }
        values.update(overrides)
        return ObjectStorageSettings(**values)

    def test_s3_presign_uses_browser_endpoint_without_network_io(self) -> None:
        signed = S3ObjectStorage(self.settings()).presign_get("project/page.png")

        self.assertTrue(
            signed.url.startswith(
                "https://prototype-assets.storage.example.com/project/page.png?"
            )
        )
        self.assertIn("X-Amz-Signature=", signed.url)

    def test_presigned_url_is_reused_until_refresh_margin(self) -> None:
        client = MagicMock()
        client.generate_presigned_url.side_effect = [
            "https://assets.example.com/page.png?signature=one",
            "https://assets.example.com/page.png?signature=two",
        ]
        storage = S3ObjectStorage(self.settings())

        with (
            patch.object(storage, "_client", return_value=client),
            patch("app.object_storage.monotonic", return_value=100.0),
        ):
            first = storage.presign_get("project/page.png")
            second = storage.presign_get("project/page.png")

        self.assertIs(first, second)
        self.assertEqual(first.url, "https://assets.example.com/page.png?signature=one")
        client.generate_presigned_url.assert_called_once()

        with (
            patch.object(storage, "_client", return_value=client),
            patch("app.object_storage.monotonic", return_value=3641.0),
        ):
            refreshed = storage.presign_get("project/page.png")

        self.assertEqual(
            refreshed.url, "https://assets.example.com/page.png?signature=two"
        )
        self.assertEqual(client.generate_presigned_url.call_count, 2)

    def test_overwriting_object_invalidates_presigned_url(self) -> None:
        client = MagicMock()
        client.generate_presigned_url.side_effect = [
            "https://assets.example.com/page.png?signature=before",
            "https://assets.example.com/page.png?signature=after",
        ]
        storage = S3ObjectStorage(self.settings())

        with patch.object(storage, "_client", return_value=client):
            before = storage.presign_get("project/page.png")
            storage.put("project/page.png", b"new image", media_type="image/png")
            after = storage.presign_get("project/page.png")

        self.assertNotEqual(before.url, after.url)
        client.put_object.assert_called_once()
        self.assertEqual(client.generate_presigned_url.call_count, 2)

    def test_oss_cname_presign_uses_bound_browser_domain(self) -> None:
        settings = self.settings(
            provider="oss",
            endpoint_url="https://oss-cn-hangzhou-internal.aliyuncs.com",
            browser_endpoint_url="https://assets.example.com",
            region="cn-hangzhou",
            browser_use_cname=True,
        )

        signed = OssObjectStorage(settings).presign_get("project/page.html")
        reused = OssObjectStorage(settings).presign_get("project/page.html")

        self.assertTrue(
            signed.url.startswith("https://assets.example.com/project/page.html?")
        )
        self.assertIn("x-oss-signature=", signed.url.lower())
        self.assertIs(signed, reused)

    def test_oss_direct_read_requires_cname(self) -> None:
        settings = self.settings(
            provider="oss",
            endpoint_url="https://oss-cn-hangzhou-internal.aliyuncs.com",
            browser_endpoint_url="https://oss-cn-hangzhou.aliyuncs.com",
            region="cn-hangzhou",
            browser_use_cname=False,
        )

        with self.assertRaisesRegex(
            ObjectStorageConfigurationError, "UIPM_OSS_CNAME is required"
        ):
            settings.validate()

    def test_oss_endpoints_are_derived_from_region(self) -> None:
        env = {
            "UIPM_S3_PROVIDER": "oss",
            "UIPM_S3_BUCKET": "uipm",
            "UIPM_S3_REGION": "cn-chengdu",
            "UIPM_S3_ACCESS_KEY_ID": "test-access-key",
            "UIPM_S3_SECRET_ACCESS_KEY": "test-secret-key",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = object_storage_settings()

        self.assertEqual(settings.addressing_style, "virtual")
        self.assertEqual(settings.endpoint_url, "https://oss-cn-chengdu.aliyuncs.com")
        self.assertEqual(
            settings.browser_endpoint_url, "https://oss-cn-chengdu.aliyuncs.com"
        )
        self.assertFalse(settings.browser_use_cname)

        with self.assertRaisesRegex(
            ObjectStorageConfigurationError, "UIPM_OSS_CNAME is required"
        ):
            settings.validate()

    def test_oss_cname_is_the_only_browser_override_needed(self) -> None:
        env = {
            "UIPM_S3_PROVIDER": "oss",
            "UIPM_S3_BUCKET": "uipm",
            "UIPM_S3_REGION": "cn-chengdu",
            "UIPM_S3_ACCESS_KEY_ID": "test-access-key",
            "UIPM_S3_SECRET_ACCESS_KEY": "test-secret-key",
            "UIPM_OSS_CNAME": "https://prototype.example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = object_storage_settings()

        self.assertEqual(settings.browser_endpoint_url, "https://prototype.example.com")
        self.assertTrue(settings.browser_use_cname)
        settings.validate()


if __name__ == "__main__":
    unittest.main()
