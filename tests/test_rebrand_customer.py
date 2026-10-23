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
