"""Credential vault.

Secrets live encrypted on disk (Fernet / AES-128-CBC + HMAC). The model only
ever sees placeholders such as ``{{vault:EMAIL_PASSWORD}}``; the Sentinel
resolves them for tools that opted in (``accepts_secrets``) and connectors
resolve their own configuration right before use. Tool output is scrubbed of
any secret value before it is handed back to the model.

Key management: ``$NANOMUSE_VAULT_KEY`` (base64 Fernet key) or a key file at
``~/.nanolika/vault.key`` created on first use with ``0600`` permissions.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

PLACEHOLDER_RE = re.compile(r"\{\{\s*vault:([A-Za-z0-9_.-]+)\s*\}\}")
# a stored value shorter than this is never redacted from a text (see `redact`)
_REDACT_MIN_CHARS = 3


class VaultError(RuntimeError):
    pass


class CredentialVault:
    def __init__(self, path: Path, key_path: Path | None = None):
        self.path = Path(path)
        self.key_path = Path(key_path) if key_path else self.path.with_suffix(".key")
        self._fernet: Fernet | None = None
        self._cache: dict[str, str] | None = None

    # ------------------------------------------------------------------ key handling
    def _key(self) -> bytes:
        env_key = os.environ.get("NANOMUSE_VAULT_KEY")
        if env_key:
            return env_key.encode()
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        # the file exists with mode 0600 before the key is in it, so there is no moment
        # when another local user could read it (chmod after write_bytes left one)
        self.key_path.touch(mode=0o600, exist_ok=True)
        self.key_path.write_bytes(key)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:  # pragma: no cover - windows
            pass
        return key

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(self._key())
        return self._fernet

    # ------------------------------------------------------------------ persistence
    def _load(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = {}
            return self._cache
        try:
            raw = self.fernet.decrypt(self.path.read_bytes())
        except InvalidToken as exc:
            raise VaultError(
                f"cannot decrypt {self.path}: wrong key? (key file: {self.key_path})"
            ) from exc
        data = json.loads(raw.decode("utf-8"))
        self._cache = {str(k): str(v) for k, v in data.items()}
        return self._cache

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = self.fernet.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        tmp = self.path.with_suffix(".tmp")
        tmp.touch(mode=0o600, exist_ok=True)
        tmp.write_bytes(token)
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover
            pass
        tmp.replace(self.path)
        self._cache = dict(data)

    # ------------------------------------------------------------------ public API
    def set(self, name: str, value: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise VaultError("secret names may only contain letters, digits, '_', '.', '-'")
        data = dict(self._load())
        data[name] = value
        self._save(data)

    def get(self, name: str) -> str | None:
        return self._load().get(name)

    def delete(self, name: str) -> bool:
        data = dict(self._load())
        existed = data.pop(name, None) is not None
        if existed:
            self._save(data)
        return existed

    def names(self) -> list[str]:
        return sorted(self._load().keys())

    def has_placeholders(self, value: Any) -> bool:
        return bool(PLACEHOLDER_RE.search(json.dumps(value, ensure_ascii=False, default=str)))

    def resolve(self, value: Any, strict: bool = True) -> Any:
        """Replace ``{{vault:NAME}}`` placeholders inside strings / dicts / lists."""
        if isinstance(value, str):

            def repl(m: re.Match[str]) -> str:
                secret = self.get(m.group(1))
                if secret is None:
                    if strict:
                        raise VaultError(
                            f"secret '{m.group(1)}' is not in the vault "
                            f"(add it with: nanomuse vault set {m.group(1)})"
                        )
                    return m.group(0)
                return secret

            return PLACEHOLDER_RE.sub(repl, value)
        if isinstance(value, dict):
            return {k: self.resolve(v, strict) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v, strict) for v in value]
        return value

    def redact(self, text: str) -> str:
        """Replace any stored secret value appearing in ``text``.

        Longest first, so a secret that contains another is not left half in place. A
        secret shorter than six characters is matched only as a whole token: a PIN of
        ``1234`` still goes, while redacting it does not rewrite half of every word. A
        value of one or two characters (``a``, ``ok``, a label stored by mistake) is not
        redacted at all: it is no secret, and masking it would blank ordinary words in
        every tool result the model sees."""
        if not text:
            return text
        for name, secret in sorted(self._load().items(), key=lambda item: -len(item[1])):
            if len(secret) < _REDACT_MIN_CHARS:
                continue
            mark = f"[REDACTED:{name}]"
            if len(secret) >= 6:
                text = text.replace(secret, mark)
            else:
                text = re.sub(rf"(?<![\w.+-]){re.escape(secret)}(?![\w.+-])", mark, text)
        return text

    def redact_stream(self) -> SecretRedactingStream:
        """Return a streaming redactor that catches secrets split across deltas."""
        return SecretRedactingStream(self)


class SecretRedactingStream:
    """Redact provider deltas without releasing a secret split across chunk boundaries."""

    def __init__(self, vault: CredentialVault):
        self.vault = vault
        self.pending = ""
        self.closed = False

    def push(self, text: str) -> str:
        if self.closed:
            raise RuntimeError("redacting stream is already finished")
        if not text:
            return ""
        self.pending += text
        secrets = [value for value in self.vault._load().values() if len(value) >= 3]
        if not secrets:
            out, self.pending = self.pending, ""
            return out

        # Keep the longest value plus its token-boundary context. If a full match straddles
        # the safe boundary, retain it whole; otherwise its leading characters could escape
        # before a later delta completes the match.
        hold = max(map(len, secrets)) + 2
        safe_until = max(0, len(self.pending) - hold)
        if safe_until:
            for secret in secrets:
                pos = self.pending.find(secret)
                while pos >= 0:
                    end = pos + len(secret)
                    if pos < safe_until < end:
                        safe_until = pos
                    pos = self.pending.find(secret, pos + 1)

        ready, self.pending = self.pending[:safe_until], self.pending[safe_until:]
        return self.vault.redact(ready)

    def finish(self) -> str:
        if self.closed:
            return ""
        self.closed = True
        ready, self.pending = self.pending, ""
        return self.vault.redact(ready)


__all__ = ["CredentialVault", "SecretRedactingStream", "VaultError", "PLACEHOLDER_RE"]
