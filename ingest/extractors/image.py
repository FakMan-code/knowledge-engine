"""Screenshots and photographed whiteboards, through OCR.

A lot of what a NOC actually knows lives in screenshots pasted into tickets.
OCR is worth having even though its output is rough.

Deliberately not here: describing an image with a vision model. Layer 1 is
deterministic, and a generated description is an interpretation that has to be
born marked as inferred. That belongs to layer 2, where the distinction between
observed and inferred is enforced. OCR stays because it transcribes what is
literally written in the pixels rather than opining about it.

Availability needs both Python packages and the tesseract binary, so the gap
report can tell the operator which of the two is missing.
"""

from __future__ import annotations

import io

from ingest.extractors.base import Extractor
from ingest.models import EmptyExtraction, ExtractedUnit

from core.provenance import Node

try:
    import pytesseract
except ImportError:  # optional dependency
    pytesseract = None

try:
    from PIL import Image
except ImportError:  # optional dependency
    Image = None

MIN_CONFIDENCE = 40.0


class ImageOCRExtractor(Extractor):
    name = "image-ocr"
    media_types = (
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/tiff",
    )
    extensions = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff")
    requirement = "pytesseract"
    install_hint = "pip install pytesseract Pillow, y instalar el binario tesseract"

    def available(self) -> bool:
        if pytesseract is None or Image is None:
            return False
        try:
            pytesseract.get_tesseract_version()
        except Exception:  # pytesseract raises its own error type when absent
            return False
        return True

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        self.require()
        with Image.open(io.BytesIO(data)) as image:
            found = pytesseract.image_to_data(
                image, output_type=pytesseract.Output.DICT
            )

        blocks: dict[int, list[str]] = {}
        boxes: dict[int, list[int]] = {}
        for index, word in enumerate(found.get("text", [])):
            cleaned = word.strip()
            if not cleaned:
                continue
            try:
                confidence = float(found["conf"][index])
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence < MIN_CONFIDENCE:
                continue
            block = int(found["block_num"][index])
            blocks.setdefault(block, []).append(cleaned)
            boxes.setdefault(
                block,
                [
                    found["left"][index],
                    found["top"][index],
                    found["left"][index] + found["width"][index],
                    found["top"][index] + found["height"][index],
                ],
            )

        units = []
        for number, (block, words) in enumerate(sorted(blocks.items()), start=1):
            text = " ".join(words)
            units.append(
                ExtractedUnit(
                    locator=Node(path=f"/ocr/bloque[{number}]"),
                    text=text,
                    meta={"bbox": boxes.get(block, []), "source": "ocr"},
                    verbatim=False,
                )
            )

        if not units:
            raise EmptyExtraction(
                "ocr_sin_texto", "el OCR no encontró texto legible en la imagen"
            )
        return units
