"""Bundled Lika resources stay consistent across client packages."""

import hashlib
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BrandAssetsTest(unittest.TestCase):
    def test_all_clients_bundle_the_canonical_avatar(self) -> None:
        for mood in ("idle", "working", "waiting", "happy", "error"):
            for extension in ("webp", "mp4"):
                if mood == "error" and extension == "mp4":
                    continue
                source = ROOT / "assets" / "brand" / "avatar" / f"{mood}.{extension}"
                expected = hashlib.sha256(source.read_bytes()).digest()
                for folder in (
                    "web/public/avatars",
                    "nanomuse/server/static/avatars",
                    "harness/dsh-nanomuse/assets",
                    "android/src/ios/NanoMuse/Resources",
                ):
                    path = ROOT / folder / f"dragon-{mood}.{extension}"
                    with self.subTest(path=str(path)):
                        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), expected)
                kind = "raw" if extension == "mp4" else "drawable-nodpi"
                prefix = "nm_motion" if extension == "mp4" else "nm_avatar"
                path = ROOT / "android/src/android/app/src/main/res" / kind
                self.assertEqual(
                    hashlib.sha256((path / f"{prefix}_{mood}.{extension}").read_bytes()).digest(),
                    expected,
                )

    def test_clips_are_small_h264_mp4_with_no_audio_track(self) -> None:
        for path in (ROOT / "assets/brand/avatar").glob("*.mp4"):
            data = path.read_bytes()
            with self.subTest(path=path.name):
                self.assertIn(b"ftyp", data[:32])
                self.assertIn(b"avcC", data)
                self.assertNotIn(b"soun", data)
                self.assertLess(len(data), 1_500_000)

    def test_retired_upstream_icon_candidates_are_gone(self) -> None:
        iconset = ROOT / "android/src/ios/Assets.xcassets/AppIcon.appiconset"
        self.assertEqual(list(iconset.glob("Icon-full*.png")), [])
        self.assertFalse((ROOT / "brand/icon-monogram-512.png").exists())


if __name__ == "__main__":
    unittest.main()


def test_avatar_only_generator_skips_rendering_and_retired_demo(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("sync_brand_test", ROOT / "scripts/sync-brand.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "BRAND", tmp_path / "assets/brand")

    def no_render(_):
        raise AssertionError("avatar-only sync must not render marks or icons")

    monkeypatch.setattr(module, "render_jobs", no_render)
    source = tmp_path / "assets/brand/avatar"
    source.mkdir(parents=True)
    (source / "idle.webp").write_bytes(b"canonical-avatar")
    destinations = (
        "web/public/avatars/dragon-idle.webp",
        "harness/dsh-nanomuse/assets/dragon-idle.webp",
        "android/src/ios/NanoMuse/Resources/dragon-idle.webp",
        "android/src/android/app/src/main/res/drawable-nodpi/nm_avatar_idle.webp",
    )
    for dest in destinations:
        (tmp_path / dest).parent.mkdir(parents=True, exist_ok=True)
    module.main(avatars_only=True)
    for dest in destinations:
        assert (tmp_path / dest).read_bytes() == b"canonical-avatar"
    assert not (tmp_path / "demo").exists()
