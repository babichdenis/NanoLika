"""Optional client for account- and conversation-bound sync file copies.

It is deliberately not wired into the running synchronizer yet. Callers must
persist the operation ID until upload is acknowledged, and must not substitute
public upload links when this service is unavailable.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from typing import Any
from urllib.parse import quote

import httpx

from nanomuse.cloud import CloudClient, CloudError

MAX_BYTES = 5 * 1024 * 1024
_CID = re.compile(r"[a-z0-9][a-z0-9._:-]{3,63}\Z")
_OPERATION = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")


def _cid(value: str) -> str:
    if not isinstance(value, str) or not _CID.fullmatch(value):
        raise ValueError("invalid conversation id")
    return value


def _id(value: str) -> str:
    try:
        result = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid file id") from None
    if value != result:
        raise ValueError("invalid file id")
    return result


class PrivateSyncFiles:
    def __init__(self, cloud: CloudClient):
        self.cloud = cloud

    def _headers(self) -> dict[str, str]:
        token = self.cloud.api_key
        if not token:
            raise CloudError(401, "signed_out", "Sign in to access the file")
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    async def _read(response: httpx.Response, limit: int) -> bytes:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(chunk) > limit - len(body):
                raise CloudError(502, "bad_response", "The cloud response is too large")
            body.extend(chunk)
        return bytes(body)

    @staticmethod
    async def _check(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            body = await PrivateSyncFiles._read(response, 65536)
            parsed = json.loads(body)
        except (ValueError, httpx.HTTPError):
            parsed = {}
        detail = parsed.get("error") if isinstance(parsed, dict) else None
        detail = detail if isinstance(detail, dict) else {}
        raise CloudError(response.status_code, str(detail.get("code") or f"http_{response.status_code}"), str(detail.get("message") or "The cloud could not access this file"))

    async def upload(self, cid: str, operation: str, name: str, mime: str, data: bytes) -> dict[str, Any]:
        cid = _cid(cid)
        if not isinstance(operation, str) or not _OPERATION.fullmatch(operation):
            raise ValueError("invalid operation id")
        if not isinstance(data, bytes) or len(data) > MAX_BYTES:
            raise ValueError("file exceeds size limit")
        if not isinstance(name, str) or len(name.encode("utf-8")) > 512:
            raise ValueError("invalid file name")
        headers = {**self._headers(), "Idempotency-Key": operation, "Content-Type": mime,
                   "X-File-Name-Base64": base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii")}
        try:
            async with self.cloud._client.stream(
                "POST",
                f"{self.cloud.base_url}/v1/sync/conversations/{quote(cid, safe='')}/files",
                headers=headers, content=data,
            ) as response:
                await self._check(response)
                result = json.loads(await self._read(response, 65536))
        except httpx.HTTPError as exc:
            raise CloudError(0, "offline", "Cannot reach the cloud") from exc
        except ValueError:
            raise CloudError(502, "bad_response", "Invalid cloud file response") from None
        if not isinstance(result, dict) or result.get("cid") != cid or result.get("size") != len(data):
            raise CloudError(502, "bad_response", "Invalid cloud file response")
        try:
            _id(result.get("file_id"))
        except ValueError:
            raise CloudError(502, "bad_response", "Invalid cloud file response") from None
        return result

    async def download(self, cid: str, file_id: str, size: int) -> bytes:
        cid, file_id = _cid(cid), _id(file_id)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > MAX_BYTES:
            raise ValueError("invalid file size")
        url = f"{self.cloud.base_url}/v1/sync/conversations/{quote(cid, safe='')}/files/{file_id}"
        try:
            async with self.cloud._client.stream("GET", url, headers=self._headers()) as response:
                await self._check(response)
                body = await self._read(response, size)
        except httpx.HTTPError as exc:
            raise CloudError(0, "offline", "Cannot reach the cloud") from exc
        if len(body) != size:
            raise CloudError(502, "bad_response", "The cloud returned an unexpected file size")
        return bytes(body)
