"""NanoLika Cloud from the runtime: sign in with a code, look at the account, the models
the relay offers, and the hub's address.

The relay (``cloud/`` in this repository, ``docs/cloud.md``) is one more OpenAI-compatible
provider to the agent — ``<base_url>/v1`` with the account key — so signing in here is the
same first step the phone offers: *Sign in — free*, with a phone number or an e-mail. The key is kept in the
vault under :data:`CLOUD_KEY`; nothing here ever shows it to the model.
"""

from __future__ import annotations

import asyncio
import base64
import platform
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from nanomuse import __version__

CLOUD_KEY = "NANOMUSE_CLOUD_KEY"
# The relay's defaults (contract C4): one model for the chat, one for the hands — the GUI
# operator wants a model that sees pictures and grounds well, the chat wants a fast,
# cheap one with tools. Two settings; the relay's ``for`` field on each model says which
# it is meant for, and these are the fallbacks when the relay does not say.
DEFAULT_CHAT_MODEL = "deepseek-v4.1-flash"
DEFAULT_GUI_MODEL = "qwen3.8-27b"
DEFAULT_MODEL = DEFAULT_CHAT_MODEL

#: The relay's refusals that mean "the balance is short", not "the request is wrong".
#: Tools answer them with one sentence and never repeat the provider's own numbers.
FUNDS_CODES = frozenset(
    {"insufficient_funds", "allowance_exhausted", "out_of_tokens", "daily_cap", "budget_exhausted"}
)
FUNDS_MESSAGE = (
    "Not enough money on the balance for this request. Tell the person its price and what "
    "is left, and that a top-up in the cabinet opens the paid models and larger sizes. Do "
    "not retry the same request."
)

def charged_of(
    accounting: dict[str, Any] | None = None, headers: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """What the relay says a call took, as ``{"uy": …, "rub": …}`` (keys it did not send
    stay out). The units are the relay's own; the rubles are what the cabinet shows."""
    said = dict(accounting or {})
    head = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    out: dict[str, Any] = {}
    for key, raw in (("uy", said.get("charged")), ("rub", said.get("charged_rub"))):
        try:
            if raw is not None and str(raw).strip():
                out[key] = float(raw)
        except (TypeError, ValueError):
            pass
    for key, name in (("uy", "x-nanomuse-charged"), ("rub", "x-nanomuse-charged-rub")):
        if key in out:
            continue
        try:
            raw = head.get(name)
            if raw:
                out[key] = float(raw)
        except (TypeError, ValueError):
            pass
    if "uy" in out and float(out["uy"]).is_integer():
        out["uy"] = int(out["uy"])
    return out


def charged_line(
    accounting: dict[str, Any] | None = None, headers: Mapping[str, str] | None = None
) -> str:
    """`charged ₽0.12` for a chat answer: rubles when the relay sent them, its own units
    otherwise, and nothing at all when it said nothing (an older relay)."""
    said = charged_of(accounting, headers)
    if "rub" in said:
        rub = float(said["rub"])
        # a phrase costs kopecks: rounding 0.004 to "₽0.00" reads as free
        return f"charged ₽{rub:.2f}" if rub >= 0.01 else f"charged ₽{rub:.4f}"
    if "uy" in said:
        return f"charged {said['uy']}"
    return ""


def funds_line(exc: "CloudError") -> str:
    """A money refusal as the person should hear it.

    The relay's own message for a priced request already names the price and the balance
    ("This generation costs ₽639.15; the balance has ₽12.30") — that is the warning the
    person decides on, so it wins over the generic sentence. The code stays at the end:
    a log and the relay's money card read it.
    """
    said = (exc.message or "").strip()
    return f"{said} ({exc.code})" if said else f"{FUNDS_MESSAGE} ({exc.code})"


MESSAGES = {
    "bad_identifier": "Enter a mainland phone number or an e-mail address.",
    "phone_region": "Codes reach mainland China numbers only for now; elsewhere, sign in with an e-mail address.",
    "code_wrong": "That code is not right.",
    "code_expired": "That code has expired; ask for a new one.",
    "code_too_often": "Too many codes were sent; wait a few minutes.",
    "not_invited": "This relay is private; that address is not on its list.",
    "domain_limited": "Today's sign-up limit for addresses at that domain is reached on this relay. Try again tomorrow, or use another address.",
    "channel_unsupported": "This relay does not send codes to that kind of address. Use the other kind (e-mail or phone number), or sign in with a password.",
    "send_failed": "The code could not be sent; try again in a moment.",
    "bad_key": "Sign in again.",
    "out_of_tokens": "This account has used its tokens.",
    "account_disabled": "This account is disabled.",
    "model_not_offered": "That model is not offered here.",
    "rate_limited": "Too many requests; slow down a little.",
    "allowance_exhausted": "The free allowance is used up. Invite a friend (the relay adds to both your allowances) or add your own model key; your sign-in and your devices keep working either way.",
    "daily_cap": "Today's token quota is used up; it comes back tomorrow.",
    "upstream": "The model provider did not answer.",
    "upstream_unconfigured": "NanoLika Cloud has no model key configured.",
    "offline": "NanoLika Cloud cannot be reached.",
    "bad_credentials": "That address and password do not match.",
    "no_password": "This account has no password yet; sign in with a code and set one under Account.",
    "locked": "Too many wrong passwords; wait a while or sign in with a code.",
    "password_short": "Use at least 8 characters.",
    "password_weak": "Choose a stronger password.",
    "password_wrong": "That is not the current password.",
    "password_required": "Enter the current password.",
    "no_session": "That sign-in is already gone.",
}


class CloudError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        # what the relay said beside the words (an ``allowance_exhausted`` carries what is
        # left, the invite link, the own-key guide)
        self.extra: dict[str, Any] = dict(extra or {})

    def describe(self) -> str:
        return MESSAGES.get(self.code, self.message or self.code)


def hub_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/v1/hub"


def model_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/v1"


#: How long the menu is trusted. A model that leaves it is caught by the 404 retry in the
#: tools and in the voice, not by an empty cache on every call.
MODELS_TTL_S = 300.0


class CloudClient:
    """The relay's account API. One instance per relay; the key may change (sign in/out)."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        # the menu, read once in a while rather than before every picture and phrase
        self._models_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._shelf_cache: tuple[float, dict[str, Any]] | None = None
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "User-Agent": f"NanoLika/{__version__} ({platform.system()})",
                "Accept": "application/json",
            },
        )

    @property
    def hub_url(self) -> str:
        return hub_url(self.base_url)

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        token: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        headers = {}
        tok = self.api_key if token is None else token
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        try:
            response = await self._client.request(
                method, self.base_url + path, json=body, headers=headers, timeout=timeout
            )
        except httpx.TimeoutException:
            raise CloudError(0, "timeout", f"{self.base_url} did not answer in time") from None
        except httpx.HTTPError as exc:
            raise CloudError(0, "offline", f"Cannot reach {self.base_url}: {exc}") from None
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            err = body.get("error") if isinstance(body, dict) else None
            if not isinstance(err, dict):
                err = {}
            raise CloudError(
                response.status_code,
                str(err.get("code") or f"http_{response.status_code}"),
                str(err.get("message") or response.text[:200]),
                extra={k: v for k, v in err.items() if k not in ("code", "message", "type")},
            )
        if not response.content.strip():
            return {}
        try:
            data = response.json()
        except ValueError:
            raise CloudError(
                response.status_code, "bad_response", f"{self.base_url} did not answer with JSON"
            ) from None
        return data if isinstance(data, dict) else {"data": data}

    # ------------------------------------------------------------------ account
    async def request_code(self, identifier: str) -> None:
        await self._request("POST", "/v1/auth/code", {"identifier": identifier}, token="")

    async def verify(
        self, identifier: str, code: str, device: str, invite: str = ""
    ) -> dict[str, Any]:
        """→ ``{api_key, account{channel,hint}, tokens{…}, models[…]}``; the key is kept on
        this client from then on. ``invite`` is a friend's code; it counts for a new account
        only and the relay ignores it otherwise."""
        body: dict[str, Any] = {"identifier": identifier, "code": code, "device": device}
        if invite.strip():
            body["invite"] = invite.strip()
        data = await self._request("POST", "/v1/auth/verify", body, token="")
        key = str(data.get("api_key") or "")
        if key:
            self.api_key = key
        return data

    async def login(self, identifier: str, password: str, device: str) -> dict[str, Any]:
        """The password way in, for accounts that set one; same reply as ``verify``."""
        data = await self._request(
            "POST",
            "/v1/auth/login",
            {"identifier": identifier, "password": password, "device": device},
            token="",
        )
        key = str(data.get("api_key") or "")
        if key:
            self.api_key = key
        return data

    async def set_password(self, password: str, current: str | None = None) -> None:
        """Set or change the account password (``current`` when one exists, unless this key
        came from a code sign-in just now); an empty password with ``current`` removes it."""
        body: dict[str, Any] = {"password": password}
        if current is not None:
            body["current"] = current
        await self._request("POST", "/v1/auth/password", body)

    async def me(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/me")

    async def config(self) -> dict[str, Any]:
        """The relay's public figures (relay 0.15, ``GET /v1/config``): the allowance a new
        account gets, the invite bonus, whether sign-up is open, the links. No key needed;
        an older relay answers 404, which comes back as a :class:`CloudError`."""
        return await self._request("GET", "/v1/config", token="")

    async def nudges(self) -> dict[str, Any]:
        """When the app may ask for a star on GitHub (relay 0.18, ``GET /v1/nudges``, contract
        C1): the policy the operator set, or the relay's defaults. No key needed; an older
        relay answers 404, which comes back as a :class:`CloudError`. The runtime keeps a
        copy through :class:`nanomuse.nudges.NudgesPolicy`, which reads once a day."""
        return await self._request("GET", "/v1/nudges", token="")

    async def profile(self, with_face: bool = True) -> dict[str, Any]:
        """The agent's name and look as the account's devices share it (``rev`` 0 = none yet);
        without the face's pictures when ``with_face`` is false."""
        return await self._request("GET", "/v1/me/profile" + ("" if with_face else "?face=false"))

    async def put_profile(self, body: dict[str, Any]) -> dict[str, Any]:
        """This device's name and look for the account (last writer wins); the new ``rev``."""
        return await self._request("PUT", "/v1/me/profile", body)

    async def put_tz(self, tz: str) -> None:
        """Tell the account this device's clock: an IANA name, so a bot on a relay in another
        country can speak in the person's time (``GET /v1/me`` returns it)."""
        await self._request("PUT", "/v1/me/tz", {"tz": tz})

    async def set_contribute(self, on: bool) -> dict[str, Any]:
        """Keep (or stop keeping) this account's chat turns for the community's model."""
        return await self._request("POST", "/v1/me/contribute", {"on": on})

    async def delete_samples(self) -> int:
        data = await self._request("DELETE", "/v1/me/samples")
        return int(data.get("deleted") or 0)

    async def sessions(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/v1/me/sessions")
        sessions = data.get("sessions")
        return sessions if isinstance(sessions, list) else []

    async def revoke_session(self, prefix: str) -> None:
        await self._request("DELETE", f"/v1/me/sessions/{prefix}")

    async def sign_out_all(self, everything: bool = False) -> int:
        data = await self._request("POST", "/v1/auth/sign-out-all", {"all": everything})
        if everything:
            self.api_key = ""
        return int(data.get("signed_out") or 0)

    async def events(self, limit: int = 50) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/v1/me/events?limit={int(limit)}")
        events = data.get("events")
        return events if isinstance(events, list) else []

    async def sign_out(self) -> None:
        try:
            await self._request("POST", "/v1/auth/sign-out", {})
        finally:
            self.api_key = ""

    async def delete_account(self) -> None:
        try:
            await self._request("POST", "/v1/auth/delete", {})
        finally:
            self.api_key = ""

    async def devices(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/v1/devices")
        devices = data.get("devices")
        return devices if isinstance(devices, list) else []

    async def me_models(self) -> dict[str, Any]:
        """The menu with the account's shelf: what it keeps per kind and its default.

        Kept with the menu (:data:`MODELS_TTL_S`): a picture or a clip asks for the shelf
        before it asks the menu, and both change on the operator's scale, not per call."""
        now = time.monotonic()
        if self._shelf_cache is not None:
            at, held = self._shelf_cache
            if now - at < MODELS_TTL_S:
                return held
        said = await self._request("GET", "/v1/me/models")
        self._shelf_cache = (now, said if isinstance(said, dict) else {})
        return self._shelf_cache[1]

    async def image(self, model: str, prompt: str, *, size: str = "") -> tuple[bytes, dict[str, Any]]:
        """Draw one picture: PNG bytes and the relay's own accounting."""
        body: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
        if size:
            body["size"] = size
        # A picture takes the provider tens of seconds and the relay waits up to its own
        # 180 s; the client's general 30 s gave up first and the person saw a timeout for
        # a picture the relay went on to buy.
        said = await self._request("POST", "/v1/images/generations", body, timeout=240.0)
        data = said.get("data")
        first = data[0] if isinstance(data, list) and data else {}
        raw = str((first or {}).get("b64_json") or "")
        if not raw:
            raise CloudError(502, "upstream", "The relay returned no picture")
        try:
            png = base64.b64decode(raw)
        except (ValueError, TypeError):
            raise CloudError(502, "bad_response", "The relay's picture was not a picture") from None
        accounting = said.get("nanomuse")
        return png, accounting if isinstance(accounting, dict) else {}

    async def models(self, *, fresh: bool = False) -> list[dict[str, Any]]:
        """The relay's menu, kept for :data:`MODELS_TTL_S`.

        A picture, a clip and a phrase each read it to find their model; asking three
        times a turn on a menu that changes on the operator's scale was pure latency.
        `fresh=True` asks again (a 404 from a paid route, a person pressing refresh)."""
        now = time.monotonic()
        if not fresh and self._models_cache is not None:
            at, rows = self._models_cache
            if now - at < MODELS_TTL_S:
                return rows
        data = await self._request("GET", "/v1/models")
        models = data.get("data")
        rows = models if isinstance(models, list) else []
        self._models_cache = (now, rows)
        return rows

    def forget_models(self) -> None:
        """Drop the menu and the shelf (a model that left them answered 404, or the key
        changed)."""
        self._models_cache = None
        self._shelf_cache = None

    async def quote(self, model: str, **params: Any) -> dict[str, Any]:
        """What one exact request costs, in the relay's rubles: size, seconds, resolution,
        characters or tracks. Nothing is charged and no provider is named."""
        return await self._request("POST", "/v1/models/quote", {"model": model, **params})

    async def video(
        self,
        model: str,
        prompt: str,
        *,
        seconds: int = 5,
        size: str = "",
        resolution: str = "",
    ) -> tuple[bytes, dict[str, Any]]:
        """One clip: submit, wait for the provider's task, keep the file and its charge.

        The relay speaks DashScope's asynchronous shape and copies the finished clip into
        its own storage, so `nanomuse.media_url` is a link that outlives the provider's."""
        parameters: dict[str, Any] = {"duration": int(seconds)}
        if size:
            parameters["size"] = size
        if resolution:
            parameters["resolution"] = resolution
        said = await self._request(
            "POST",
            "/api/v1/services/aigc/video-generation/video-synthesis",
            {"model": model, "input": {"prompt": prompt}, "parameters": parameters},
            timeout=180.0,
        )
        output = said.get("output") if isinstance(said, dict) else None
        task = str((output or {}).get("task_id") or "")
        if not task:
            raise CloudError(502, "upstream", "The relay did not name the clip's task")
        deadline = time.monotonic() + 15 * 60
        while time.monotonic() < deadline:
            await asyncio.sleep(4)
            state = await self._request("GET", f"/api/v1/tasks/{quote(task, safe='')}", timeout=60.0)
            output = state.get("output") if isinstance(state, dict) else None
            output = output if isinstance(output, dict) else {}
            status = str(output.get("task_status") or "")
            if status == "SUCCEEDED":
                meta = state.get("nanomuse") if isinstance(state.get("nanomuse"), dict) else {}
                url = str(meta.get("media_url") or output.get("video_url") or "")
                if not url:
                    raise CloudError(502, "upstream", "The relay returned no clip")
                try:
                    response = await self._client.get(url, timeout=300.0, follow_redirects=True)
                except httpx.HTTPError as exc:
                    raise CloudError(0, "offline", "The clip could not be downloaded") from exc
                if response.status_code >= 400:
                    raise CloudError(502, "upstream", "The clip could not be downloaded")
                return response.content, {
                    "charged": meta.get("charged"),
                    "url": url,
                    "seconds": int(seconds),
                }
            if status in ("FAILED", "CANCELED"):
                raise CloudError(502, "upstream", str(output.get("message") or "the provider could not make this clip"))
        raise CloudError(504, "timeout", "The clip took longer than fifteen minutes")

    async def upload(self, name: str, data: bytes, mime: str) -> str:
        """Put a file the agent made into the account's storage; the stable link back.

        The link is what a chat or another device can show: the relay's own signed link,
        not a presigned one that expires. An empty string means the relay has no storage."""
        try:
            response = await self._audio(
                "/v1/attachments",
                data=None,
                files={"file": (name, data, mime)},
                timeout=180.0,
            )
            said = response.json()
        except (CloudError, ValueError):
            return ""
        return str(said.get("media_url") or "") if isinstance(said, dict) else ""

    async def music(self, model: str, prompt: str) -> tuple[bytes, str, dict[str, str]]:
        """One music track by description: bytes, type and the relay's own headers."""
        response = await self._audio(
            "/v1/audio/music", json={"model": model, "prompt": prompt}, timeout=300.0
        )
        media = (response.headers.get("content-type") or "audio/mpeg").split(";")[0].strip()
        return response.content, media, dict(response.headers)

    async def estimate(
        self,
        images: int = 0,
        image_model: str = "",
        size: str = "",
        clips: int = 0,
        video_model: str = "",
    ) -> dict[str, Any]:
        """What ``images`` pictures and ``clips`` short videos would cost against the pool
        (``cny``, ``left_cny``, ``affordable``, ``unlimited``); nothing is charged."""
        params: dict[str, str] = {"images": str(int(images))}
        if image_model:
            params["image_model"] = image_model
        if size:
            params["size"] = size
        if clips:
            params["clips"] = str(int(clips))
            if video_model:
                params["video_model"] = video_model
        # model ids and sizes are typed by people and relayed by the app: encoded, so an
        # "&" or a space in one cannot change the question asked
        return await self._request("GET", "/v1/estimate?" + urlencode(params))

    async def _audio(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: float = 180.0,
    ) -> httpx.Response:
        """Один вызов звука: сырой ответ или CloudError с кодом релея."""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            response = await self._client.post(
                self.base_url + path, json=json, data=data, files=files, headers=headers, timeout=timeout
            )
        except httpx.TimeoutException:
            raise CloudError(0, "timeout", f"{self.base_url} did not answer in time") from None
        except httpx.HTTPError as exc:
            raise CloudError(0, "offline", f"Cannot reach {self.base_url}: {exc}") from None
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            err = body.get("error") if isinstance(body, dict) else None
            if not isinstance(err, dict):
                err = {}
            raise CloudError(
                response.status_code,
                str(err.get("code") or f"http_{response.status_code}"),
                str(err.get("message") or response.text[:200]),
                extra={k: v for k, v in err.items() if k not in ("code", "message", "type")},
            )
        return response

    async def speech(
        self, model: str, text: str, *, voice: str = "", fmt: str = "mp3", speed: float | None = None
    ) -> tuple[bytes, str, dict[str, str]]:
        """Текст в звук: байты, тип содержимого и заголовки с фактическим списанием."""
        payload: dict[str, Any] = {"model": model, "input": text, "response_format": fmt}
        if voice:
            payload["voice"] = voice
        if speed:
            payload["speed"] = speed
        response = await self._audio("/v1/audio/speech", json=payload)
        media = (response.headers.get("content-type") or "audio/mpeg").split(";")[0].strip()
        return response.content, media, dict(response.headers)

    async def transcribe(
        self, model: str, filename: str, data: bytes, mime: str, *, language: str = ""
    ) -> dict[str, Any]:
        """Запись в текст: ответ релея как есть (text, duration, nanomuse)."""
        form: dict[str, str] = {"model": model}
        if language:
            form["language"] = language
        response = await self._audio(
            "/v1/audio/transcriptions",
            data=form,
            files={"file": (filename or "recording.webm", data, mime or "audio/webm")},
            timeout=300.0,
        )
        try:
            said = response.json()
        except ValueError:
            raise CloudError(502, "upstream", "The transcription provider sent no words") from None
        return said if isinstance(said, dict) else {"text": ""}

    @staticmethod
    def models_for(models: list[dict[str, Any]], purpose: str = "chat") -> list[dict[str, Any]]:
        """The relay's models meant for ``purpose`` — ``chat`` or ``gui`` (contract C4).

        A model that carries ``"for": ["chat"]`` / ``["gui"]`` / ``["chat", "gui"]`` (under
        ``nanomuse``, where the relay keeps its flags, or at the top) is taken at its word.
        A relay that does not say yet gets the old reading: every text-out model is a chat
        model, and a chat model whose id says it sees (``qwen*-vl``, ``qwen3.8-*``) is a
        hands model too."""
        from nanomuse.llm.vision import model_takes_images

        out = []
        for m in models:
            uses = CloudClient._lanes(m)
            if uses is not None:
                # said — `[]` is a picture or video model, in no picker
                if purpose in uses:
                    out.append(m)
                continue
            text_out = (m.get("architecture") or {}).get("output_modalities", ["text"]) == ["text"]
            if not text_out:
                continue
            if purpose == "chat" or model_takes_images(str(m.get("id") or "")):
                out.append(m)
        return out

    @staticmethod
    def _lanes(m: dict[str, Any]) -> list[str] | None:
        """The lanes a menu entry is for — ``nanomuse.for`` first, a top-level ``for`` as
        the fallback; ``None`` when the relay did not say (``[]`` is "none": a picture or
        video model)."""
        flags = m.get("nanomuse") or {}
        uses = flags.get("for")
        if uses is None:
            uses = m.get("for")
        if isinstance(uses, str):
            uses = [uses]
        return [str(u) for u in uses] if isinstance(uses, list) else None

    @staticmethod
    def recommended_model(models: list[dict[str, Any]], purpose: str = "chat") -> str:
        """The relay's recommended model for ``purpose`` (``nanomuse.recommended_for``, or
        ``recommended`` on a relay that does not split the lanes), else the default when it
        is offered, else the first one meant for it, else the default anyway."""
        fitting = CloudClient.models_for(models, purpose)
        default = DEFAULT_GUI_MODEL if purpose == "gui" else DEFAULT_CHAT_MODEL
        for m in fitting:
            flags = m.get("nanomuse") or {}
            rec_for = flags.get("recommended_for")
            if isinstance(rec_for, str):
                rec_for = [rec_for]
            if isinstance(rec_for, list):
                if purpose in rec_for:
                    return str(m["id"])
                continue
            if flags.get("recommended") and (purpose == "chat" or not CloudClient._lanes(m)):
                return str(m["id"])
        if any(m.get("id") == default for m in fitting):
            return default
        return str(fitting[0]["id"]) if fitting else default


__all__ = [
    "CLOUD_KEY",
    "DEFAULT_CHAT_MODEL",
    "DEFAULT_GUI_MODEL",
    "DEFAULT_MODEL",
    "MESSAGES",
    "CloudClient",
    "CloudError",
    "hub_url",
    "model_url",
]
