"""The store: capabilities that are not in the installer (docs/store.md).

What is checked here is the contract the model relies on: a missing capsule is named in the
prompt, an installed one stops being named, a catalogue that does not answer never breaks the
prompt, and `pip` is never the answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanomuse.schema import RiskLevel
from nanomuse.tools.store import DEFAULT_STORE, Store, platform_tag

INDEX = {
    "version": 1,
    "base": DEFAULT_STORE,
    "packages": [
        {
            "section": "capsules",
            "name": "ocr-vision",
            "version": "1.0.0",
            "description": "текст с картинки",
            "modules": ["Vision", "ocr_vision"],
            "files": [
                {
                    "platform": platform_tag(),
                    "url": f"{DEFAULT_STORE}/capsules/ocr-vision/1.0.0/darwin-x64.zip",
                    "bytes": 3_261_626,
                    "sha256": "00" * 32,
                }
            ],
            "bytes": 3_261_626,
            "install": "addon",
        },
        {
            "section": "skills",
            "name": "scan-to-text",
            "description": "читать сканы",
            "url": f"{DEFAULT_STORE}/skills/scan-to-text/SKILL.md",
            "bytes": 1200,
            "install": "skill",
        },
    ],
    "problems": [],
}


def make(tmp_path: Path, index: dict | None = None) -> Store:
    store = Store(addons_dir=tmp_path / "addons")
    if index is not None:
        store.remember_index(index)
    return store


def test_platform_tag_is_the_name_of_a_file():
    tag = platform_tag()
    assert "-" in tag
    assert tag.split("-")[0] in ("darwin", "win32", "linux")


def test_a_missing_capsule_is_named_for_the_prompt(tmp_path: Path):
    store = make(tmp_path, INDEX)
    lines = store.missing_lines()
    assert "ocr-vision" in lines
    assert "action=addon" in lines
    # a skill gives modules nobody imports, so it is never "missing" — it is installable
    assert "scan-to-text" not in lines


def test_an_installed_capsule_stops_being_named(tmp_path: Path):
    store = make(tmp_path, INDEX)
    folder = store.addons_dir / "ocr-vision"
    folder.mkdir(parents=True)
    (folder / "capsule.json").write_text('{"name": "ocr-vision"}', encoding="utf-8")
    assert "ocr-vision" not in store.missing_lines()


def test_an_offline_store_does_not_break_the_prompt(tmp_path: Path):
    store = make(tmp_path)  # nothing cached, and the address below answers nothing
    store.base_url = "http://127.0.0.1:9/store"
    assert store.missing_lines() == ""


def test_the_prompt_names_the_store_and_forbids_pip(tmp_path: Path):
    from nanomuse import prompts

    text = prompts.STORE_SECTION.format(base=DEFAULT_STORE, missing="- ocr-vision (3.1 MB)")
    assert DEFAULT_STORE in text
    assert "no pip" in text.lower() or "there is no pip" in text.lower()
    assert "ocr-vision" in text


def test_listing_says_what_is_missing_and_what_is_installed(tmp_path: Path):
    store = make(tmp_path, INDEX)
    listing = store._listing(INDEX["packages"], everything=False)
    assert "MISSING" in listing and "ocr-vision" in listing
    assert "pip" in listing


async def test_install_refuses_an_unknown_name(tmp_path: Path):
    store = make(tmp_path, INDEX)
    r = await store.execute(action="addon", name="nope")
    assert not r.ok and "no 'nope'" in r.error


async def test_install_refuses_a_platform_it_has_no_build_for(tmp_path: Path, monkeypatch):
    import nanomuse.tools.store as module

    monkeypatch.setattr(module, "platform_tag", lambda: "plan9-mips")
    store = make(tmp_path, INDEX)
    monkeypatch.setattr(store, "_fetch_index", _returning(INDEX))
    r = await store.execute(action="addon", name="ocr-vision")
    assert not r.ok and "no build for plan9-mips" in r.error


def test_a_wrong_digest_installs_nothing(tmp_path: Path):
    from nanomuse.tools.store import verify_digest

    good = tmp_path / "capsule.zip"
    good.write_bytes(b"not really a zip")
    from nanomuse.tools.store import digest_of

    verify_digest(good, digest_of(good))  # the promised digest: accepted
    verify_digest(good, "")  # nothing promised: accepted
    with pytest.raises(RuntimeError, match="not what the catalogue promised"):
        verify_digest(good, "00" * 32)


def test_assessment_separates_looking_from_installing(tmp_path: Path):
    store = make(tmp_path, INDEX)
    assert store.assess({"action": "list"}).risk == RiskLevel.SAFE
    install = store.assess({"action": "addon", "name": "ocr-vision"})
    assert install.risk == RiskLevel.SENSITIVE and install.egress


def test_status_lists_installed_capsules(tmp_path: Path):
    store = make(tmp_path)
    folder = store.addons_dir / "ocr-vision"
    folder.mkdir(parents=True)
    (folder / "capsule.json").write_text(
        json.dumps({"name": "ocr-vision", "version": "1.0.0"}), encoding="utf-8"
    )
    store.enable_all()
    assert "ocr-vision 1.0.0" in store._status()
    import sys

    assert str(folder) in sys.path
    sys.path.remove(str(folder))


def _returning(value):
    async def inner(*_a, **_kw):
        return value

    return inner
