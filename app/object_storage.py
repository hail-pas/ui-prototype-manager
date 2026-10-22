from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic
from typing import BinaryIO, Iterator, Protocol

_PRESIGNED_URL_REFRESH_MARGIN_SECONDS = 60
_PRESIGNED_URL_CACHE_MAX_ENTRIES = 4096


class ObjectStorageConfigurationError(RuntimeError):
    pass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ObjectStorageConfigurationError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ObjectStorageConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


@dataclass(frozen=True)
class ObjectStorageSettings:
    bucket: str | None
    provider: str
    endpoint_url: str | None
    browser_endpoint_url: str | None
    region: str
    access_key: str | None
    secret_key: str | None
    prefix: str
    addressing_style: str
    signature_version: str
    direct_read: bool
    presign_ttl_seconds: int
    browser_use_cname: bool

    @property
    def configured(self) -> bool:
        return bool(self.bucket)

    def validate(self) -> None:
        if not self.configured:
            return
        if self.provider not in {"s3", "oss"}:
            raise ObjectStorageConfigurationError("UIPM_S3_PROVIDER must be s3 or oss")
        if self.addressing_style not in {"auto", "path", "virtual"}:
            raise ObjectStorageConfigurationError(
                "UIPM_S3_ADDRESSING_STYLE must be auto, path or virtual"
            )
        if self.provider == "oss":
            if not self.endpoint_url:
                raise ObjectStorageConfigurationError(
                    "UIPM_S3_ENDPOINT_URL is required when UIPM_S3_PROVIDER=oss"
                )
            if not self.access_key or not self.secret_key:
                raise ObjectStorageConfigurationError(
                    "OSS requires UIPM_S3_ACCESS_KEY_ID and UIPM_S3_SECRET_ACCESS_KEY"
                )
            if self.direct_read and not self.browser_use_cname:
                raise ObjectStorageConfigurationError(
                    "UIPM_OSS_CNAME is required for direct OSS reads. Bind an HTTPS "
                    "custom domain to the bucket; no additional application service "
                    "is needed."
                )


def object_storage_settings() -> ObjectStorageSettings:
    provider = os.getenv("UIPM_S3_PROVIDER", "s3").strip().lower() or "s3"
    region = os.getenv("UIPM_S3_REGION", "us-east-1").strip() or "us-east-1"
    default_addressing_style = "virtual" if provider == "oss" else "auto"
    endpoint_url = os.getenv("UIPM_S3_ENDPOINT_URL") or None
    browser_endpoint_url = os.getenv("UIPM_S3_BROWSER_ENDPOINT_URL") or None
    browser_use_cname = False

    if provider == "oss":
        public_endpoint = f"https://oss-{region}.aliyuncs.com"
        endpoint_url = endpoint_url or public_endpoint
        oss_cname = (os.getenv("UIPM_OSS_CNAME") or "").strip().rstrip("/") or None
        if oss_cname:
            browser_endpoint_url = oss_cname
            browser_use_cname = True
        else:
            browser_endpoint_url = public_endpoint

    return ObjectStorageSettings(
        bucket=os.getenv("UIPM_S3_BUCKET") or None,
        provider=provider,
        endpoint_url=endpoint_url,
        browser_endpoint_url=browser_endpoint_url,
        region=region,
        access_key=os.getenv("UIPM_S3_ACCESS_KEY_ID") or None,
        secret_key=os.getenv("UIPM_S3_SECRET_ACCESS_KEY") or None,
        prefix=os.getenv("UIPM_S3_PREFIX", "uipm").strip("/"),
        addressing_style=(
            os.getenv("UIPM_S3_ADDRESSING_STYLE", default_addressing_style)
            .strip()
            .lower()
            or default_addressing_style
        ),
        signature_version=os.getenv("UIPM_S3_SIGNATURE_VERSION", "s3v4").strip()
        or "s3v4",
        direct_read=_env_bool("UIPM_S3_DIRECT_READ", True),
        presign_ttl_seconds=_env_int(
            "UIPM_S3_PRESIGN_TTL_SECONDS", 3600, minimum=60, maximum=604800
        ),
        browser_use_cname=browser_use_cname,
    )


@dataclass(frozen=True)
class PresignedObject:
    url: str
    expires_at: str


@dataclass(frozen=True)
class _PresignedCacheEntry:
    value: PresignedObject
    reusable_until: float


_presigned_url_cache: dict[tuple[ObjectStorageSettings, str], _PresignedCacheEntry] = {}
_presigned_url_cache_lock = Lock()


def _cached_presigned_object(
    settings: ObjectStorageSettings,
    key: str,
    signer: Callable[[], PresignedObject],
) -> PresignedObject:
    cache_key = (settings, key)
    now = monotonic()
    with _presigned_url_cache_lock:
        cached = _presigned_url_cache.get(cache_key)
        if cached and cached.reusable_until > now:
            return cached.value

        _presigned_url_cache.pop(cache_key, None)
        stale_keys = [
            item_key
            for item_key, entry in _presigned_url_cache.items()
            if entry.reusable_until <= now
        ]
        for stale_key in stale_keys:
            _presigned_url_cache.pop(stale_key, None)
        while len(_presigned_url_cache) >= _PRESIGNED_URL_CACHE_MAX_ENTRIES:
            _presigned_url_cache.pop(next(iter(_presigned_url_cache)))

        value = signer()
        refresh_margin = min(
            _PRESIGNED_URL_REFRESH_MARGIN_SECONDS,
            max(1, settings.presign_ttl_seconds // 10),
        )
        _presigned_url_cache[cache_key] = _PresignedCacheEntry(
            value=value,
            reusable_until=now + settings.presign_ttl_seconds - refresh_margin,
        )
        return value


def _invalidate_presigned_object(settings: ObjectStorageSettings, key: str) -> None:
    with _presigned_url_cache_lock:
        _presigned_url_cache.pop((settings, key), None)


def _clear_presigned_url_cache() -> None:
    with _presigned_url_cache_lock:
        _presigned_url_cache.clear()


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes, *, media_type: str) -> None: ...

    def put_fileobj(
        self,
        key: str,
        fileobj: BinaryIO,
        *,
        size: int,
        media_type: str,
    ) -> None: ...

    def read(self, key: str) -> bytes: ...

    def iter_bytes(
        self,
        key: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> Iterator[bytes]: ...

    def delete(self, key: str) -> None: ...

    def delete_many(self, keys: list[str]) -> None: ...

    def presign_get(self, key: str) -> PresignedObject: ...


class S3ObjectStorage:
    def __init__(self, settings: ObjectStorageSettings):
        self.settings = settings

    def _client(self, *, browser: bool = False):
        import boto3
        from botocore.config import Config

        endpoint_url = self.settings.endpoint_url
        if browser and self.settings.browser_endpoint_url:
            endpoint_url = self.settings.browser_endpoint_url
        kwargs = {
            "region_name": self.settings.region,
            "config": Config(
                signature_version=self.settings.signature_version,
                s3={"addressing_style": self.settings.addressing_style},
            ),
        }
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if self.settings.access_key:
            kwargs["aws_access_key_id"] = self.settings.access_key
        if self.settings.secret_key:
            kwargs["aws_secret_access_key"] = self.settings.secret_key
        return boto3.client("s3", **kwargs)

    def put(self, key: str, data: bytes, *, media_type: str) -> None:
        self._client().put_object(
            Bucket=self.settings.bucket,
            Key=key,
            Body=data,
            ContentType=media_type,
            ContentDisposition="inline",
            CacheControl=f"private, max-age={self.settings.presign_ttl_seconds}",
        )
        _invalidate_presigned_object(self.settings, key)

    def put_fileobj(
        self,
        key: str,
        fileobj: BinaryIO,
        *,
        size: int,
        media_type: str,
    ) -> None:
        del size  # boto3 determines the transfer size from the seekable input.
        self._client().upload_fileobj(
            fileobj,
            self.settings.bucket,
            key,
            ExtraArgs={
                "ContentType": media_type,
                "ContentDisposition": "inline",
                "CacheControl": f"private, max-age={self.settings.presign_ttl_seconds}",
            },
        )
        _invalidate_presigned_object(self.settings, key)

    def read(self, key: str) -> bytes:
        obj = self._client().get_object(Bucket=self.settings.bucket, Key=key)
        return obj["Body"].read()

    def iter_bytes(
        self,
        key: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> Iterator[bytes]:
        params = {"Bucket": self.settings.bucket, "Key": key}
        if start is not None:
            params["Range"] = f"bytes={start}-{'' if end is None else end}"
        body = self._client().get_object(**params)["Body"]
        try:
            while True:
                chunk = body.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            body.close()

    def delete(self, key: str) -> None:
        self._client().delete_object(Bucket=self.settings.bucket, Key=key)
        _invalidate_presigned_object(self.settings, key)

    def delete_many(self, keys: list[str]) -> None:
        client = self._client()
        for offset in range(0, len(keys), 1000):
            batch = keys[offset : offset + 1000]
            if not batch:
                continue
            client.delete_objects(
                Bucket=self.settings.bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
            for key in batch:
                _invalidate_presigned_object(self.settings, key)

    def presign_get(self, key: str) -> PresignedObject:
        def sign() -> PresignedObject:
            url = self._client(browser=True).generate_presigned_url(
                "get_object",
                Params={"Bucket": self.settings.bucket, "Key": key},
                ExpiresIn=self.settings.presign_ttl_seconds,
            )
            return _presigned_object(url, self.settings.presign_ttl_seconds)

        return _cached_presigned_object(self.settings, key, sign)


class OssObjectStorage:
    def __init__(self, settings: ObjectStorageSettings):
        self.settings = settings

    def _bucket(self, *, browser: bool = False):
        import oss2

        credentials = oss2.credentials.StaticCredentialsProvider(
            self.settings.access_key,
            self.settings.secret_key,
        )
        auth = oss2.ProviderAuthV4(credentials)
        endpoint = self.settings.endpoint_url
        is_cname = False
        if browser:
            endpoint = self.settings.browser_endpoint_url
            is_cname = self.settings.browser_use_cname
        return oss2.Bucket(
            auth,
            endpoint,
            self.settings.bucket,
            region=self.settings.region,
            is_cname=is_cname,
        )

    def put(self, key: str, data: bytes, *, media_type: str) -> None:
        self._bucket().put_object(
            key,
            data,
            headers={
                "Content-Type": media_type,
                "Content-Disposition": "inline",
                "Cache-Control": f"private, max-age={self.settings.presign_ttl_seconds}",
            },
        )
        _invalidate_presigned_object(self.settings, key)

    def put_fileobj(
        self,
        key: str,
        fileobj: BinaryIO,
        *,
        size: int,
        media_type: str,
    ) -> None:
        self._bucket().put_object(
            key,
            fileobj,
            headers={
                "Content-Type": media_type,
                "Content-Length": str(size),
                "Content-Disposition": "inline",
                "Cache-Control": f"private, max-age={self.settings.presign_ttl_seconds}",
            },
        )
        _invalidate_presigned_object(self.settings, key)

    def read(self, key: str) -> bytes:
        return self._bucket().get_object(key).read()

    def iter_bytes(
        self,
        key: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> Iterator[bytes]:
        byte_range = None if start is None else (start, end)
        body = self._bucket().get_object(key, byte_range=byte_range)
        try:
            while True:
                chunk = body.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            close = getattr(body, "close", None)
            if close:
                close()

    def delete(self, key: str) -> None:
        self._bucket().delete_object(key)
        _invalidate_presigned_object(self.settings, key)

    def delete_many(self, keys: list[str]) -> None:
        bucket = self._bucket()
        for offset in range(0, len(keys), 1000):
            batch = keys[offset : offset + 1000]
            if not batch:
                continue
            bucket.batch_delete_objects(batch)
            for key in batch:
                _invalidate_presigned_object(self.settings, key)

    def presign_get(self, key: str) -> PresignedObject:
        def sign() -> PresignedObject:
            url = self._bucket(browser=True).sign_url(
                "GET",
                key,
                self.settings.presign_ttl_seconds,
                slash_safe=True,
            )
            return _presigned_object(url, self.settings.presign_ttl_seconds)

        return _cached_presigned_object(self.settings, key, sign)


def _presigned_object(url: str, ttl_seconds: int) -> PresignedObject:
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    return PresignedObject(url=url, expires_at=expires_at.isoformat())


def object_storage() -> ObjectStorage:
    settings = object_storage_settings()
    settings.validate()
    if not settings.configured:
        raise ObjectStorageConfigurationError(
            "S3 is not configured. Set UIPM_S3_BUCKET first."
        )
    if settings.provider == "oss":
        return OssObjectStorage(settings)
    return S3ObjectStorage(settings)
