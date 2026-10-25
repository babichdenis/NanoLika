"""OCR капсулы ``ocr-vision``: текст с картинки через системный Vision (macOS).

Капсула ставится со склада (docs/store.md) и живёт в ``<data_dir>/addons/ocr-vision``;
её корень попадает в ``sys.path``, поэтому ``import Vision`` работает без pip и без сборки.

Этот файл — часть капсулы, а не рантайма: он знает только про системный OCR и ничего про
агента. Рантайм зовёт :func:`words`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Языки по умолчанию: то, на чём пишут Денису и его документам. Vision принимает список,
#: порядок — приоритет распознавания.
LANGS = ("ru-RU", "en-US")


@dataclass(frozen=True)
class Word:
    """Одно слово на картинке: что и где."""

    text: str
    confidence: float
    #: рамка в пикселях картинки: x0, y0, x1, y1 (y растёт вниз, как у пикселей)
    box: tuple[float, float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 3),
            "box": [round(v, 1) for v in self.box],
        }


def available() -> tuple[bool, str]:
    """Есть ли OCR на этой машине. ``(False, причина)`` вместо исключения: рантайм решает,
    что сказать человеку."""
    import sys

    if sys.platform != "darwin":
        return False, "the Vision OCR capsule works on macOS only"
    try:
        import Quartz  # noqa: F401
        import Vision  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — капсула битая или не распаковалась
        return False, f"the capsule is not usable here: {type(exc).__name__}: {exc}"
    return True, ""


def words(path: str | Path, langs: tuple[str, ...] = LANGS, *, accurate: bool = True) -> list[Word]:
    """Слова с картинки, сверху вниз и слева направо.

    ``accurate`` включает медленное распознавание (лучше на сканах); для скриншотов хватает
    быстрого. Кириллица распознаётся в обоих режимах.
    """
    import Quartz
    import Vision
    from Foundation import NSURL

    url = NSURL.fileURLWithPath_(str(path))
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    if source is None:
        raise ValueError(f"cannot read the image: {path}")
    image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if image is None:
        raise ValueError(f"cannot decode the image: {path}")
    height = Quartz.CGImageGetHeight(image)
    width = Quartz.CGImageGetWidth(image)

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(
        Vision.VNRequestTextRecognitionLevelAccurate
        if accurate
        else Vision.VNRequestTextRecognitionLevelFast
    )
    request.setRecognitionLanguages_(list(langs))
    # галочка «исправлять язык» портит артикулы, суммы и номера счетов
    request.setUsesLanguageCorrection_(False)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision refused the image: {error}")

    found: list[Word] = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        candidate = candidates[0]
        text = str(candidate.string() or "").strip()
        if not text:
            continue
        # Vision отдаёт рамку в долях картинки и с началом координат внизу слева
        rect = observation.boundingBox()
        x0 = float(rect.origin.x) * width
        x1 = (float(rect.origin.x) + float(rect.size.width)) * width
        y0 = (1.0 - float(rect.origin.y) - float(rect.size.height)) * height
        y1 = (1.0 - float(rect.origin.y)) * height
        found.append(Word(text=text, confidence=float(candidate.confidence()), box=(x0, y0, x1, y1)))

    # в документы люди смотрят сверху вниз; Vision отдаёт в своём порядке
    found.sort(key=lambda w: (round(w.box[1] / 8), w.box[0]))
    return found


def text(path: str | Path, langs: tuple[str, ...] = LANGS) -> str:
    """Всё распознанное как текст, построчно: слова одной строки склеиваются пробелом."""
    lines: dict[int, list[Word]] = {}
    for word in words(path, langs):
        lines.setdefault(round(word.box[1] / 8), []).append(word)
    return "\n".join(
        " ".join(word.text for word in sorted(row, key=lambda w: w.box[0]))
        for row in (lines[key] for key in sorted(lines))
    )
