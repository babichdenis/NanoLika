"""Вложения аккаунта в общем бакете S3 (reg.ru), тот же бакет, что у приложения.

Приложение уже выкладывает туда файлы, которые показала Лика, и бот забирает их в
Telegram/MAX по ссылке. Веб-клиенту нужен тот же путь для картинок и документов,
которые человек прикладывает сам: релей кладёт объект в `files/<uuid>/<имя>` и
отдаёт presigned-ссылку. Ключи живут только на сервере (S3_ACCESS_KEY/S3_SECRET_KEY).

Подпись — AWS SigV4, портирована из `harness/dsh-nanomuse/src/share.ts`, чтобы веб и
приложение писали в один бакет одинаково.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import httpx

_SAFE = re.compile(r"[^\w.\- ()\u4e00-\u9fff]+", re.UNICODE)


def enabled(settings) -> bool:
    return bool(settings.s3_access_key and settings.s3_secret_key)


def _encode(value: str) -> str:
    """AWS wants every byte outside A-Z a-z 0-9 - _ . ~ escaped."""
    return quote(value, safe="-_.~")


def _encode_path(value: str) -> str:
    return "/".join(_encode(part) for part in value.split("/"))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes | str, value: str) -> bytes:
    raw = key.encode() if isinstance(key, str) else key
    return hmac.new(raw, value.encode(), hashlib.sha256).digest()


def _signing_key(secret: str, day: str, region: str) -> bytes:
    return _hmac(_hmac(_hmac(_hmac(f"AWS4{secret}", day), region), "s3"), "aws4_request")


def _now() -> tuple[str, str]:
    iso = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return iso, iso[:8]


def _path(settings, key: str) -> str:
    base = urlsplit(settings.s3_endpoint).path.rstrip("/")
    return f"{base}/{_encode(settings.s3_bucket)}/{_encode_path(key)}"


def safe_name(name: str) -> str:
    cleaned = _SAFE.sub("_", (name or "file").strip())[:120].strip("._ ")
    return cleaned or "file"


def new_key(name: str) -> str:
    return f"files/{uuid.uuid4()}/{safe_name(name)}"


def presign_get(settings, key: str, name: str) -> str:
    """Ссылка на скачивание; имя в Content-Disposition делает её файлом, а не страницей."""
    amz, day = _now()
    scope = f"{day}/{settings.s3_region}/s3/aws4_request"
    path = _path(settings, key)
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{settings.s3_access_key}/{scope}",
        "X-Amz-Date": amz,
        "X-Amz-Expires": str(max(1, min(settings.s3_link_seconds, 7 * 24 * 60 * 60))),
        "X-Amz-SignedHeaders": "host",
        "response-content-disposition": f'attachment; filename="{name.replace(chr(34), "_").replace(chr(92), "_")}"',
    }
    query = "&".join(f"{_encode(k)}={_encode(params[k])}" for k in sorted(params))
    host = urlsplit(settings.s3_endpoint).hostname or ""
    canonical = f"GET\n{path}\n{query}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD"
    to_sign = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{_digest(canonical.encode())}"
    signature = hmac.new(
        _signing_key(settings.s3_secret_key, day, settings.s3_region), to_sign.encode(), hashlib.sha256
    ).hexdigest()
    endpoint = settings.s3_endpoint.rstrip("/")
    return f"{endpoint}{path}?{query}&X-Amz-Signature={signature}"


async def put_object(settings, key: str, data: bytes, content_type: str) -> None:
    amz, day = _now()
    scope = f"{day}/{settings.s3_region}/s3/aws4_request"
    path = _path(settings, key)
    digest = _digest(data)
    headers = {
        "host": urlsplit(settings.s3_endpoint).hostname or "",
        "content-type": content_type,
        "x-amz-content-sha256": digest,
        "x-amz-date": amz,
    }
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
    canonical = f"PUT\n{path}\n\n{canonical_headers}\n{signed}\n{digest}"
    to_sign = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{_digest(canonical.encode())}"
    signature = hmac.new(
        _signing_key(settings.s3_secret_key, day, settings.s3_region), to_sign.encode(), hashlib.sha256
    ).hexdigest()
    authorization = f"AWS4-HMAC-SHA256 Credential={settings.s3_access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"
    url = f"{settings.s3_endpoint.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        response = await client.put(
            url,
            content=data,
            headers={
                "Content-Type": content_type,
                "x-amz-content-sha256": digest,
                "x-amz-date": amz,
                "Authorization": authorization,
            },
        )
    if response.status_code >= 300:
        raise RuntimeError(f"S3 PUT {response.status_code}: {response.text[:200]}")


async def store(settings, data: bytes, content_type: str, name: str) -> tuple[str, str]:
    """Кладёт файл и возвращает (ключ, presigned-ссылка)."""
    key = new_key(name)
    await put_object(settings, key, data, content_type)
    return key, presign_get(settings, key, safe_name(name))
