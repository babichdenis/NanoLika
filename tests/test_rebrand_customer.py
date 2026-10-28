"""Display branding must not change the native wire and storage identifiers."""

import importlib.util
from pathlib import Path


def test_native_display_branding_preserves_identifiers(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "rebrand_under_test", Path(__file__).resolve().parents[1] / "scripts/rebrand.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "android/src/ios/NanoMuse"
    root.mkdir(parents=True)
    source = root / "Fixture.swift"
    source.write_text(
        'class NanoMuseCloud {}\nlet title = "nanoMuse Cloud"\nlet event = "nanoMuse.openChat"\nlet agent = "nanoMuse/1.0"\nlet repo = "https://github.com/nano-muse/nanoMuse"\n'
    )
    module.customer_branding(tmp_path)
    after = source.read_text()
    assert 'title = "nanoLika Cloud"' in after
    assert "class NanoMuseCloud" in after
    assert '"nanoMuse.openChat"' in after
    assert '"nanoMuse/1.0"' in after
    assert "github.com/nano-muse/nanoMuse" in after
    module.customer_branding(tmp_path)
    assert source.read_text() == after


def test_localized_upstream_spots_follow_catalogue_without_renaming_storage(tmp_path):
    import json

    spec = importlib.util.spec_from_file_location(
        "rebrand_upstream_under_test", Path(__file__).resolve().parents[1] / "scripts/rebrand.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "android/src/ios"
    source = root / "Agent/Intents/Fixture.swift"
    source.parent.mkdir(parents=True)
    source.write_text(
        'class NanoMuseIntent {}\n'
        '// nanoMuse: localized notification title.\n'
        'let title = AppLocalized(\n    "nanoMuse: Retrying")\n'
        'let event = "nanoMuse.openChat"\nlet storage = "nanoMuse"\n'
    )
    catalogue = root / "Localizable.xcstrings"
    catalogue.write_text(json.dumps({"strings": {"nanoMuse: Retrying": {"shouldTranslate": False}}}))
    module.customer_branding(tmp_path)
    after = source.read_text()
    assert '"nanoLika: Retrying"' in after
    assert "class NanoMuseIntent" in after
    assert '"nanoMuse.openChat"' in after and 'storage = "nanoMuse"' in after
    assert "nanoLika: Retrying" in json.loads(catalogue.read_text())["strings"]
    catalog_after = catalogue.read_bytes()
    module.customer_branding(tmp_path)
    assert source.read_text() == after and catalogue.read_bytes() == catalog_after
