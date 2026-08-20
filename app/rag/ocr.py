from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from statistics import fmean
from typing import Protocol

DEFAULT_OCR_MIN_CONFIDENCE = 0.80
DEFAULT_OCR_MIN_TEXT_CHARACTERS = 20
DEFAULT_OCR_TIMEOUT_SECONDS = 15.0
DEFAULT_OCR_MAX_IMAGE_BYTES = 15 * 1024 * 1024
DEFAULT_OCR_MAX_IMAGE_PIXELS = 40_000_000


class OCRError(ValueError):
    """OCR could not produce trusted text for ingestion."""


class OCRDependencyError(OCRError):
    """The configured OCR implementation or native engine is unavailable."""


class OCRExecutionError(OCRError):
    """The OCR engine failed or exceeded its execution boundary."""


class OCRQualityError(OCRError):
    """OCR output did not satisfy the ingestion quality gate."""


@dataclass(frozen=True, slots=True)
class OCRImage:
    """One bounded raster unit submitted to an OCR provider."""

    data: bytes
    media_type: str
    source_path: Path
    container_media_type: str
    unit_kind: str
    unit_number: int

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("OCR image data must not be empty")
        if not self.media_type.startswith("image/"):
            raise ValueError("OCR media_type must be an image type")
        if not self.container_media_type.strip():
            raise ValueError("container_media_type must not be blank")
        if self.unit_kind not in {"page", "block"}:
            raise ValueError("unit_kind must be 'page' or 'block'")
        if self.unit_number < 1:
            raise ValueError("unit_number must be greater than zero")


@dataclass(frozen=True, slots=True)
class OCRResult:
    """Text and provider confidence for one OCR image."""

    text: str
    confidence: float
    engine: str
    language: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("OCR confidence must be between zero and one")
        if not self.engine.strip():
            raise ValueError("OCR engine must not be blank")
        if not self.language.strip():
            raise ValueError("OCR language must not be blank")


class OCRProvider(Protocol):
    """Minimal provider boundary used by format-specific document loaders."""

    def recognize(self, image: OCRImage) -> OCRResult:
        """Recognize one image without mutating the source document."""


@dataclass(frozen=True, slots=True)
class OCRQualityGate:
    """Reject blank or low-confidence OCR before parsing and indexing."""

    minimum_confidence: float = DEFAULT_OCR_MIN_CONFIDENCE
    minimum_text_characters: int = DEFAULT_OCR_MIN_TEXT_CHARACTERS

    def __post_init__(self) -> None:
        if not 0.0 <= self.minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between zero and one")
        if self.minimum_text_characters < 1:
            raise ValueError("minimum_text_characters must be greater than zero")

    def accept(self, result: OCRResult, *, image: OCRImage) -> str:
        text = result.text.strip()
        character_count = sum(not character.isspace() for character in text)
        if character_count < self.minimum_text_characters:
            raise OCRQualityError(
                f"OCR text is below the quality threshold for {image.unit_kind} "
                f"{image.unit_number}: {character_count} < {self.minimum_text_characters}"
            )
        if result.confidence < self.minimum_confidence:
            raise OCRQualityError(
                f"OCR confidence is below the quality threshold for {image.unit_kind} "
                f"{image.unit_number}: {result.confidence:.3f} < "
                f"{self.minimum_confidence:.3f}"
            )
        return text


class TesseractOCRProvider:
    """Local pytesseract adapter with timeout, image, and confidence boundaries."""

    def __init__(
        self,
        *,
        language: str = "chi_sim+eng",
        command: str | None = None,
        timeout_seconds: float = DEFAULT_OCR_TIMEOUT_SECONDS,
        page_segmentation_mode: int = 6,
        max_image_bytes: int = DEFAULT_OCR_MAX_IMAGE_BYTES,
        max_image_pixels: int = DEFAULT_OCR_MAX_IMAGE_PIXELS,
    ) -> None:
        if not language.strip():
            raise ValueError("language must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if page_segmentation_mode < 0 or page_segmentation_mode > 13:
            raise ValueError("page_segmentation_mode must be between 0 and 13")
        if max_image_bytes < 1 or max_image_pixels < 1:
            raise ValueError("OCR image limits must be greater than zero")
        self._language = language.strip()
        self._command = command.strip() if command else None
        self._timeout_seconds = timeout_seconds
        self._config = f"--psm {page_segmentation_mode}"
        self._max_image_bytes = max_image_bytes
        self._max_image_pixels = max_image_pixels

    def recognize(self, image: OCRImage) -> OCRResult:
        if len(image.data) > self._max_image_bytes:
            raise OCRExecutionError(
                f"OCR image exceeds byte limit: {len(image.data)} > {self._max_image_bytes}"
            )
        try:
            from PIL import Image, UnidentifiedImageError
            import pytesseract
        except ModuleNotFoundError as exc:
            raise OCRDependencyError(
                "Tesseract OCR requires the optional 'ocr' dependencies"
            ) from exc

        if self._command is not None:
            pytesseract.pytesseract.tesseract_cmd = self._command

        try:
            with Image.open(BytesIO(image.data)) as opened_image:
                opened_image.load()
                if opened_image.width * opened_image.height > self._max_image_pixels:
                    raise OCRExecutionError(
                        "OCR image exceeds pixel limit: "
                        f"{opened_image.width * opened_image.height} > {self._max_image_pixels}"
                    )
                data = pytesseract.image_to_data(
                    opened_image.convert("RGB"),
                    lang=self._language,
                    config=self._config,
                    output_type=pytesseract.Output.DICT,
                    timeout=self._timeout_seconds,
                )
        except OCRExecutionError:
            raise
        except UnidentifiedImageError as exc:
            raise OCRExecutionError("OCR input is not a supported raster image") from exc
        except pytesseract.TesseractNotFoundError as exc:
            raise OCRDependencyError(
                "Tesseract executable was not found; install it or configure its command path"
            ) from exc
        except RuntimeError as exc:
            raise OCRExecutionError("Tesseract OCR timed out or failed") from exc
        except Exception as exc:
            raise OCRExecutionError(f"Tesseract OCR failed: {type(exc).__name__}") from exc

        return _tesseract_result(data, language=self._language)


def _tesseract_result(data: Mapping[str, list[object]], *, language: str) -> OCRResult:
    required = {"text", "conf", "page_num", "block_num", "par_num", "line_num"}
    if not required.issubset(data):
        raise OCRExecutionError("Tesseract output is missing required fields")

    lines: dict[tuple[int, int, int, int], list[str]] = defaultdict(list)
    confidences: list[float] = []
    row_count = len(data["text"])
    if any(len(data[field]) != row_count for field in required):
        raise OCRExecutionError("Tesseract output fields have inconsistent lengths")

    for index in range(row_count):
        word = str(data["text"][index]).strip()
        if not word:
            continue
        try:
            confidence = float(data["conf"][index])
            key = tuple(
                int(data[field][index])
                for field in ("page_num", "block_num", "par_num", "line_num")
            )
        except (TypeError, ValueError) as exc:
            raise OCRExecutionError("Tesseract output contains invalid coordinates") from exc
        lines[key].append(word)
        if confidence >= 0:
            confidences.append(confidence / 100.0)

    text = "\n".join(" ".join(lines[key]) for key in sorted(lines))
    confidence = fmean(confidences) if confidences else 0.0
    return OCRResult(
        text=text,
        confidence=confidence,
        engine="tesseract",
        language=language,
    )


__all__ = [
    "DEFAULT_OCR_MAX_IMAGE_BYTES",
    "DEFAULT_OCR_MAX_IMAGE_PIXELS",
    "DEFAULT_OCR_MIN_CONFIDENCE",
    "DEFAULT_OCR_MIN_TEXT_CHARACTERS",
    "DEFAULT_OCR_TIMEOUT_SECONDS",
    "OCRDependencyError",
    "OCRError",
    "OCRExecutionError",
    "OCRImage",
    "OCRProvider",
    "OCRQualityError",
    "OCRQualityGate",
    "OCRResult",
    "TesseractOCRProvider",
]
