"""Склад NanoLika: то, что не лезет в установщик, берётся с сервера.

Установщик — это то, что человек скачал один раз. OCR, PyMuPDF, тяжёлые MCP-серверы и редкие
навыки живут на складе (``https://cloud.nanolika.ru/store``, описание — ``docs/store.md``), а
этот инструмент — единственный вход к нему для модели:

* ``list`` — что есть, с отметкой, что уже стоит и чего не хватает этой сборке;
* ``addon`` — поставить капсулу кода: скачать zip под эту платформу, сверить sha256, распаковать
  в ``<data_dir>/addons/<name>`` и добавить в ``sys.path``;
* ``skill`` — поставить навык (SKILL.md) со склада;
* ``mcp`` — завести MCP-сервер из описания на складе;
* ``status`` — что стоит сейчас.

Модель обязана начинать с этого инструмента, а не с интернета: в замороженном рантайме нет pip,
и ``pip install`` там не работает.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

from nanomuse.logger import logger
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment

#: где лежит склад, когда ничего другого не сказано
DEFAULT_STORE = "https://cloud.nanolika.ru/store"
TIMEOUT_S = 300.0
#: одна капсула — это код, который будет исполняться в этом процессе: больше 200 МБ не бывает
MAX_BYTES = 200 * 1024 * 1024


def platform_tag() -> str:
    """Имя платформы так, как оно в имени файла на складе: ``darwin-arm64``, ``win32-x64``."""
    machine = {"x86_64": "x64", "AMD64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        __import__("platform").machine(), "x64"
    )
    return f"{sys.platform}-{machine}"


def digest_of(path: Path) -> str:
    """sha256 файла."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_digest(path: Path, expected: str) -> None:
    """Сверить файл с тем, что обещал каталог. Исключение — и файл не ставится."""
    if not expected:
        return
    got = digest_of(path)
    if got != expected:
        raise RuntimeError(
            f"the downloaded file is not what the catalogue promised "
            f"(sha256 {got[:12]}… instead of {expected[:12]}…) — nothing was installed"
        )


def _human(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, n // 1024)} KB"


class Store(BaseTool):
    name: str = "store"
    description: str = (
        "The NanoLika store: the capabilities that are NOT in this build. Before saying you "
        "cannot read a scan, convert a document or do some other job, look here — `list` shows "
        "what is available (skills, code capsules, MCP servers) and which of it this build is "
        "missing. `addon` (name) downloads a code capsule for this platform, checks its sha256, "
        "unpacks it into the data directory and makes it importable — after that the job works. "
        "`skill` (name) installs a skill, `mcp` (name) adds an MCP server, `status` shows what is "
        "installed. Never try `pip install`: this runtime is frozen and has no pip; the store is "
        "the way. The catalogue itself: https://cloud.nanolika.ru/store/index.json."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "addon", "skill", "mcp", "status"],
            },
            "name": {"type": "string", "description": "Which package (from `list`)."},
            "all": {
                "type": "boolean",
                "description": "list: show everything, not only what this build is missing.",
            },
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.MODERATE
    #: the catalogue and the packages are fetched from here
    base_url: str = DEFAULT_STORE
    #: capsules land here and are added to ``sys.path``
    addons_dir: Path = Path("addons")
    #: what installs a skill and an MCP server; the service sets these (the CLI has no skills)
    install_skill: Any = None
    install_mcp: Any = None

    # ------------------------------------------------------------------ helpers
    def _index_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/index.json"

    def _installed(self, name: str) -> Path | None:
        folder = self.addons_dir / name
        return folder if (folder / "capsule.json").exists() else None

    def has_module(self, module: str) -> bool:
        """Есть ли модуль в этой сборке (не считая капсул)."""
        if module in sys.modules:
            return True
        try:
            import importlib.util

            return importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            return False

    async def _fetch_index(self) -> dict[str, Any]:
        import httpx

        url = self._index_url()
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "NanoLika"})
        if r.status_code >= 400:
            raise RuntimeError(f"the store did not answer: HTTP {r.status_code} for {url}")
        try:
            return r.json()
        except ValueError as exc:
            raise RuntimeError(f"the store's catalogue is not JSON: {exc}") from exc

    def _missing(self, package: dict[str, Any]) -> bool:
        """Не хватает ли этой сборке того, что даёт пакет."""
        if package["section"] != "capsules":
            return False
        if self._installed(package["name"]) is not None:
            return False
        modules = [m for m in package.get("modules") or [] if m[:1].isupper() or "_" not in m]
        return any(not self.has_module(m) for m in modules)

    # ------------------------------------------------------------------ the tool
    def assess(self, args: dict[str, Any]) -> CallAssessment:
        action = str(args.get("action") or "")
        name = str(args.get("name") or "")
        if action in ("list", "status"):
            return CallAssessment(risk=RiskLevel.SAFE, summary=f"store.{action}")
        # installs: code that will run in this process, from the network
        return CallAssessment(
            risk=RiskLevel.SENSITIVE,
            egress=True,
            egress_target="cloud.nanolika.ru",
            summary=f"store.{action} {name}".strip(),
            warnings=["this downloads code that runs inside the agent process"],
        )

    async def execute(
        self, action: str = "list", name: str = "", all: bool = False, **_: Any
    ) -> ToolResult:  # noqa: A002 — the model's schema names it `all`
        action = (action or "list").strip().lower()
        if action == "status":
            return ToolResult(output=self._status())
        try:
            index = self.cached_index() or await self._fetch_index()
        except Exception as exc:  # noqa: BLE001 — the store being down is a message
            return ToolResult.fail(
                f"could not reach the store at {self.base_url}: {type(exc).__name__}: {exc}. "
                "Say so to the user; do not try pip."
            )
        self.remember_index(index)
        packages: list[dict[str, Any]] = index.get("packages") or []

        if action == "list":
            return ToolResult(output=self._listing(packages, everything=bool(all)))
        if not name.strip():
            return ToolResult.fail(f"store.{action} needs a `name` (see store action=list)")
        wanted = name.strip().lower()
        package = next((p for p in packages if p["name"].lower() == wanted), None)
        if package is None:
            names = ", ".join(p["name"] for p in packages) or "nothing"
            return ToolResult.fail(f"the store has no '{name}'. It has: {names}")
        if action == "addon":
            return await self._install_addon(package)
        if action == "skill":
            return await self._install_skill(package)
        if action == "mcp":
            return await self._install_mcp(package)
        return ToolResult.fail(f"unknown action '{action}'")

    # ------------------------------------------------------------------ views
    def _listing(self, packages: list[dict[str, Any]], *, everything: bool) -> str:
        rows: list[str] = []
        missing = [p for p in packages if self._missing(p)]
        shown = packages if everything else (missing or packages)
        for package in shown:
            section = package["section"]
            label = package["name"] + (f" {package.get('version','')}".rstrip() if section == "capsules" else "")
            state = ""
            if section == "capsules":
                if self._installed(package["name"]) is not None:
                    state = " — installed"
                elif self._missing(package):
                    state = " — MISSING in this build"
                else:
                    state = " — not needed (this build has it)"
            size = _human(package.get("bytes") or 0)
            rows.append(f"- [{section}] {label} ({size}){state}: {package.get('description','')}")
        if not rows:
            return "the store is empty"
        head = (
            "The store, everything it has:"
            if everything
            else "The store: what this build is missing comes first."
        )
        tail = (
            "\n\nInstall with `store` action=addon|skill|mcp and the name. Never pip."
        )
        return head + "\n" + "\n".join(rows) + tail

    def _status(self) -> str:
        rows = []
        if self.addons_dir.is_dir():
            for folder in sorted(self.addons_dir.iterdir()):
                meta = folder / "capsule.json"
                if not meta.exists():
                    continue
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                except ValueError:
                    data = {}
                rows.append(f"- {data.get('name', folder.name)} {data.get('version','')}")
        importable = [p.name for p in self.addons_dir.iterdir()] if self.addons_dir.is_dir() else []
        body = "\n".join(rows) or "(no capsules installed)"
        return f"Installed capsules ({self.addons_dir}):\n{body}\nOn sys.path: {', '.join(importable) or 'nothing'}"

    def missing_lines(self, index: dict[str, Any] | None = None) -> str:
        """То, чего этой сборке не хватает, — строками для промпта.

        Каталог приходит снаружи (его уже скачал `list`), либо берётся из кэша: раздел
        промпта собирается на каждом запросе, а склад меняется раз в день. Нет сети — нет
        строк, и промпт просто молчит: раздел не должен ломать запрос."""
        index = index or self.cached_index()
        if not index:
            return ""
        lines = []
        for package in index.get("packages") or []:
            if not self._missing(package):
                continue
            size = _human(package.get("bytes") or 0)
            lines.append(
                f"- `{package['name']}` ({size}): {package.get('description', '')} — "
                f"`store` action=addon name={package['name']}"
            )
        if not lines:
            return ""
        return "\nMissing in this build right now:\n" + "\n".join(lines) + "\n"

    def cached_index(self) -> dict[str, Any] | None:
        """Каталог склада, скачанный не позже минуту назад (или None)."""
        cached = getattr(self, "_cache", None)
        if not cached:
            return None
        stamp, index = cached
        return index if (__import__("time").monotonic() - stamp) < 60 else None

    def remember_index(self, index: dict[str, Any]) -> None:
        self._cache = (__import__("time").monotonic(), index)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ installs
    async def _download(self, url: str, target: Path, expected_sha: str) -> int:
        """Скачать файл и сверить sha256 до того, как он куда-то попадёт."""
        import httpx

        target.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        partial = target.with_suffix(target.suffix + ".part")
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
            async with client.stream("GET", url, headers={"User-Agent": "NanoLika"}) as r:
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code} for {url}")
                with partial.open("wb") as handle:
                    async for chunk in r.aiter_bytes(1 << 18):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise RuntimeError(f"{url} is larger than {_human(MAX_BYTES)}")
                        handle.write(chunk)
        try:
            verify_digest(partial, expected_sha)
        except RuntimeError:
            partial.unlink(missing_ok=True)
            raise
        partial.replace(target)
        return total

    async def _install_addon(self, package: dict[str, Any]) -> ToolResult:
        files = package.get("files") or []
        tag = platform_tag()
        chosen = next((f for f in files if f["platform"] == tag), None)
        if chosen is None:
            have = ", ".join(f["platform"] for f in files) or "none"
            return ToolResult.fail(
                f"'{package['name']}' has no build for {tag} (it has: {have}). Tell the user "
                "this one is not available on their system."
            )
        folder = self.addons_dir / package["name"]
        if self._installed(package["name"]) is not None:
            return ToolResult(
                output=f"{package['name']} {package.get('version','')} is already installed in {folder}"
            )
        archive = self.addons_dir / f"{package['name']}-{tag}.zip"
        try:
            size = await self._download(chosen["url"], archive, chosen.get("sha256", ""))
            folder.parent.mkdir(parents=True, exist_ok=True)
            staging = folder.with_name(folder.name + ".incoming")
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            with zipfile.ZipFile(archive) as zf:
                for member in zf.namelist():
                    # nothing may escape the capsule's own folder
                    if member.startswith("/") or ".." in Path(member).parts:
                        raise RuntimeError(f"the archive contains an unsafe path: {member}")
                zf.extractall(staging)
            shutil.rmtree(folder, ignore_errors=True)
            staging.replace(folder)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"could not install '{package['name']}': {type(exc).__name__}: {exc}")
        finally:
            archive.unlink(missing_ok=True)
        self.enable(package["name"])
        logger.info("store: installed capsule {} from {}", package["name"], chosen["url"])
        modules = ", ".join(package.get("modules") or []) or "(none listed)"
        return ToolResult(
            output=(
                f"{package['name']} {package.get('version','')} installed: {_human(size)} into "
                f"{folder}, importable now. Modules it provides: {modules}. "
                "Try the job again; nothing needs a restart."
            )
        )

    def enable(self, name: str) -> None:
        """Добавить капсулу в ``sys.path`` — без перезапуска процесса."""
        folder = self.addons_dir / name
        text = str(folder)
        if folder.is_dir() and text not in sys.path:
            sys.path.insert(0, text)

    def enable_all(self) -> None:
        """При старте: всё, что уже стоит на диске, снова импортируемо."""
        if not self.addons_dir.is_dir():
            return
        for folder in sorted(self.addons_dir.iterdir()):
            if (folder / "capsule.json").exists():
                self.enable(folder.name)

    async def _install_skill(self, package: dict[str, Any]) -> ToolResult:
        if self.install_skill is None:
            return ToolResult.fail("skills are not installed in this build (the CLI has none)")
        import httpx

        url = package.get("url") or ""
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "NanoLika"})
        if r.status_code >= 400:
            return ToolResult.fail(f"could not fetch the skill: HTTP {r.status_code} for {url}")
        try:
            result = self.install_skill(r.text)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"the skill was fetched but not accepted: {exc}")
        return ToolResult(output=f"skill '{package['name']}' installed: {result}")

    async def _install_mcp(self, package: dict[str, Any]) -> ToolResult:
        if self.install_mcp is None:
            return ToolResult.fail("MCP servers are not managed in this build")
        server = package.get("server") or {}
        if not (server.get("command") or server.get("url")):
            return ToolResult.fail(f"the store's description of '{package['name']}' has no command or url")
        try:
            await self.install_mcp(server)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"could not connect '{package['name']}': {exc}")
        return ToolResult(output=f"MCP server '{package['name']}' connected: {package.get('description','')}")


__all__ = ["DEFAULT_STORE", "Store", "digest_of", "platform_tag", "verify_digest"]
