from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Protocol


class DocumentLoadError(ValueError):
    """A source document could not be converted into trusted plain text."""


class DocumentNotFoundError(DocumentLoadError):
    """The requested source path does not exist."""


class DocumentPathError(DocumentLoadError):
    """The requested source path is not a regular file."""


class UnsupportedDocumentFormatError(DocumentLoadError):
    """No registered loader owns the source file extension."""


class DocumentDecodingError(DocumentLoadError):
    """The source bytes cannot be decoded by the selected loader."""


class EmptyDocumentError(DocumentLoadError):
    """The selected loader produced no usable text."""


class DocumentDependencyError(DocumentLoadError):
    """A format-specific runtime dependency is unavailable."""


class DocumentMetadataError(DocumentLoadError):
    """Trusted ingestion metadata is missing or unreadable."""


class EncryptedDocumentError(DocumentLoadError):
    """The document cannot be opened without a password."""


class InvalidDocumentError(DocumentLoadError):
    """The file is not a valid instance of its registered format."""


class OCRRequiredError(DocumentLoadError):
    """The document does not contain enough native text for safe parsing."""


@dataclass(frozen=True, slots=True)
class LoadedDocument:
    """Format-neutral text returned by a document loader."""

    source_path: Path
    text: str
    media_type: str
    loader_name: str
    metadata_text: str | None = None
    metadata_source_path: Path | None = None
    page_count: int | None = None
    line_page_numbers: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise EmptyDocumentError(f"document contains no usable text: {self.source_path}")
        if not self.media_type.strip():
            raise ValueError("media_type must not be blank")
        if not self.loader_name.strip():
            raise ValueError("loader_name must not be blank")
        if self.metadata_text is not None and not self.metadata_text.strip():
            raise DocumentMetadataError("metadata_text must not be blank")
        if self.metadata_source_path is not None and self.metadata_text is None:
            raise ValueError("metadata_source_path requires metadata_text")
        if self.page_count is not None and self.page_count < 1:
            raise ValueError("page_count must be greater than zero")
        if self.line_page_numbers:
            if self.page_count is None:
                raise ValueError("line_page_numbers require page_count")
            if len(self.line_page_numbers) != len(self.text.splitlines()):
                raise ValueError("line_page_numbers must align with text lines")
            if any(page < 1 or page > self.page_count for page in self.line_page_numbers):
                raise ValueError("line_page_numbers must be within page_count")


class DocumentLoader(Protocol):
    """Extension point that converts one source format into normalized text."""

    @property
    def name(self) -> str:
        """Stable implementation name used for diagnostics."""

    @property
    def media_type(self) -> str:
        """Media type produced by this loader."""

    @property
    def supported_extensions(self) -> frozenset[str]:
        """Lower-case file extensions owned by this loader, including the dot."""

    def load(self, path: Path) -> LoadedDocument:
        """Extract normalized text from one regular file."""


def _normalize_extension(extension: str) -> str:
    normalized = extension.strip().lower()
    if not normalized.startswith(".") or len(normalized) == 1:
        raise ValueError("document extensions must start with '.' and contain a suffix")
    return normalized


def _validate_source_path(path: Path) -> None:
    if not path.exists():
        raise DocumentNotFoundError(f"document file does not exist: {path}")
    if not path.is_file():
        raise DocumentPathError(f"document path is not a file: {path}")


class MarkdownDocumentLoader:
    """Load UTF-8 Markdown while accepting an optional byte-order mark."""

    name = "markdown"
    media_type = "text/markdown"
    supported_extensions = frozenset({".md"})

    def load(self, path: Path) -> LoadedDocument:
        source_path = Path(path)
        _validate_source_path(source_path)

        if source_path.suffix.lower() not in self.supported_extensions:
            raise UnsupportedDocumentFormatError(
                f"markdown loader does not support extension: {source_path.suffix.lower()}"
            )

        try:
            text = source_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentDecodingError(f"document is not valid UTF-8 text: {source_path}") from exc
        except OSError as exc:
            raise DocumentLoadError(f"cannot read document: {source_path}: {exc}") from exc

        return LoadedDocument(
            source_path=source_path,
            text=text,
            media_type=self.media_type,
            loader_name=self.name,
        )


_CHINESE_NUMBER_PATTERN = r"[一二三四五六七八九十百千零〇两0-9]+"
_PLAIN_CHAPTER_PATTERN = re.compile(
    rf"^(?P<heading>第{_CHINESE_NUMBER_PATTERN}章(?:[ \u3000]+.*?)?)$"
)
_PLAIN_ARTICLE_PATTERN = re.compile(
    rf"^(?P<label>第{_CHINESE_NUMBER_PATTERN}条)(?:[ \u3000]+(?P<remainder>.*?))?$"
)
_SENTENCE_PUNCTUATION = frozenset("。！？；;!?.")
PDF_METADATA_SIDECAR_SUFFIX = ".metadata.yaml"
DEFAULT_PDF_MIN_TEXT_CHARACTERS = 20


class _PDFPage(Protocol):
    def get_text(self, option: str, *, sort: bool) -> str:
        """Extract page text."""


class _PDFDocument(Protocol):
    page_count: int
    needs_pass: bool

    def __getitem__(self, page_number: int) -> _PDFPage:
        """Load a zero-based page."""

    def close(self) -> None:
        """Release native PDF resources."""


class _PDFBackend(Protocol):
    def open(self, filename: str) -> _PDFDocument:
        """Open one PDF document."""


def pdf_metadata_sidecar_path(path: str | Path) -> Path:
    """Return the trusted sidecar path for one PDF source."""

    source_path = Path(path)
    return source_path.with_name(f"{source_path.stem}{PDF_METADATA_SIDECAR_SUFFIX}")


def _looks_like_article_title(text: str) -> bool:
    return len(text) <= 24 and not any(mark in text for mark in _SENTENCE_PUNCTUATION)


def _normalize_pdf_line(line: str) -> tuple[str, ...]:
    normalized = " ".join(line.replace("\u3000", " ").split())
    if not normalized:
        return ("",)
    if normalized.startswith("#"):
        return (normalized,)

    chapter_match = _PLAIN_CHAPTER_PATTERN.fullmatch(normalized)
    if chapter_match is not None:
        return (f"## {chapter_match.group('heading')}",)

    article_match = _PLAIN_ARTICLE_PATTERN.fullmatch(normalized)
    if article_match is None:
        return (normalized,)

    label = article_match.group("label")
    remainder = (article_match.group("remainder") or "").strip()
    if not remainder:
        return (f"### {label}",)
    if _looks_like_article_title(remainder):
        return (f"### {label} {remainder}",)
    return (f"### {label}", remainder)


def _append_pdf_line(
    lines: list[str],
    page_numbers: list[int],
    *,
    line: str,
    page_number: int,
) -> None:
    if not line and (not lines or not lines[-1]):
        return
    lines.append(line)
    page_numbers.append(page_number)


def _trim_pdf_lines(lines: list[str], page_numbers: list[int]) -> None:
    while lines and not lines[0]:
        lines.pop(0)
        page_numbers.pop(0)
    while lines and not lines[-1]:
        lines.pop()
        page_numbers.pop()


class PDFDocumentLoader:
    """Extract native PDF text and attach trusted sidecar metadata."""

    name = "pymupdf"
    media_type = "application/pdf"
    supported_extensions = frozenset({".pdf"})

    def __init__(
        self,
        *,
        minimum_text_characters: int = DEFAULT_PDF_MIN_TEXT_CHARACTERS,
        backend: _PDFBackend | None = None,
    ) -> None:
        if minimum_text_characters < 1:
            raise ValueError("minimum_text_characters must be greater than zero")
        self._minimum_text_characters = minimum_text_characters
        self._configured_backend = backend

    @property
    def minimum_text_characters(self) -> int:
        return self._minimum_text_characters

    def _backend(self) -> _PDFBackend:
        if self._configured_backend is not None:
            return self._configured_backend
        try:
            import pymupdf
        except ModuleNotFoundError as exc:
            raise DocumentDependencyError(
                "PDF parsing requires PyMuPDF; install the project dependencies"
            ) from exc
        return pymupdf

    def _load_metadata(self, source_path: Path) -> tuple[str, Path]:
        metadata_path = pdf_metadata_sidecar_path(source_path)
        if not metadata_path.exists():
            raise DocumentMetadataError(f"PDF metadata sidecar does not exist: {metadata_path}")
        if not metadata_path.is_file():
            raise DocumentMetadataError(f"PDF metadata sidecar is not a file: {metadata_path}")
        try:
            metadata_text = metadata_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentMetadataError(
                f"PDF metadata sidecar is not valid UTF-8: {metadata_path}"
            ) from exc
        except OSError as exc:
            raise DocumentMetadataError(
                f"cannot read PDF metadata sidecar: {metadata_path}: {exc}"
            ) from exc
        if not metadata_text.strip():
            raise DocumentMetadataError(f"PDF metadata sidecar is empty: {metadata_path}")
        return metadata_text, metadata_path

    def _extract_text(self, source_path: Path) -> tuple[str, int, tuple[int, ...]]:
        try:
            document = self._backend().open(str(source_path))
        except DocumentLoadError:
            raise
        except Exception as exc:
            raise InvalidDocumentError(f"cannot open PDF document: {source_path}: {exc}") from exc

        try:
            if document.needs_pass:
                raise EncryptedDocumentError(f"PDF document requires a password: {source_path}")
            if document.page_count < 1:
                raise InvalidDocumentError(f"PDF document contains no pages: {source_path}")

            lines: list[str] = []
            page_numbers: list[int] = []
            for zero_based_page in range(document.page_count):
                page_number = zero_based_page + 1
                page_text = document[zero_based_page].get_text("text", sort=True)
                if not isinstance(page_text, str):
                    raise InvalidDocumentError(
                        f"PDF page extraction returned non-text data: page {page_number}"
                    )
                if lines and lines[-1]:
                    _append_pdf_line(
                        lines,
                        page_numbers,
                        line="",
                        page_number=page_number - 1,
                    )
                for raw_line in page_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                    for normalized_line in _normalize_pdf_line(raw_line):
                        _append_pdf_line(
                            lines,
                            page_numbers,
                            line=normalized_line,
                            page_number=page_number,
                        )

            _trim_pdf_lines(lines, page_numbers)
            text = "\n".join(lines)
            native_character_count = sum(not character.isspace() for character in text)
            if native_character_count < self._minimum_text_characters:
                raise OCRRequiredError(
                    "PDF native text is below the configured threshold; OCR fallback is required: "
                    f"{native_character_count} < {self._minimum_text_characters}"
                )
            return text, document.page_count, tuple(page_numbers)
        except DocumentLoadError:
            raise
        except Exception as exc:
            raise InvalidDocumentError(f"cannot extract PDF text: {source_path}: {exc}") from exc
        finally:
            document.close()

    def load(self, path: Path) -> LoadedDocument:
        source_path = Path(path)
        _validate_source_path(source_path)
        if source_path.suffix.lower() not in self.supported_extensions:
            raise UnsupportedDocumentFormatError(
                f"PDF loader does not support extension: {source_path.suffix.lower()}"
            )

        metadata_text, metadata_path = self._load_metadata(source_path)
        text, page_count, line_page_numbers = self._extract_text(source_path)
        return LoadedDocument(
            source_path=source_path,
            text=text,
            media_type=self.media_type,
            loader_name=self.name,
            metadata_text=metadata_text,
            metadata_source_path=metadata_path,
            page_count=page_count,
            line_page_numbers=line_page_numbers,
        )


class DocumentLoaderRegistry:
    """Immutable extension-to-loader router shared by parsing and indexing."""

    def __init__(self, loaders: Iterable[DocumentLoader]) -> None:
        loader_list = tuple(loaders)
        if not loader_list:
            raise ValueError("at least one document loader is required")

        loaders_by_extension: dict[str, DocumentLoader] = {}
        for loader in loader_list:
            if not loader.name.strip():
                raise ValueError("document loader name must not be blank")
            if not loader.media_type.strip():
                raise ValueError("document loader media_type must not be blank")
            if not loader.supported_extensions:
                raise ValueError(f"document loader {loader.name!r} has no extensions")

            for extension in loader.supported_extensions:
                normalized = _normalize_extension(extension)
                if normalized in loaders_by_extension:
                    owner = loaders_by_extension[normalized]
                    raise ValueError(
                        f"document extension {normalized} is already registered by {owner.name}"
                    )
                loaders_by_extension[normalized] = loader

        self._loaders_by_extension = MappingProxyType(loaders_by_extension)

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return tuple(sorted(self._loaders_by_extension))

    def loader_for(self, path: str | Path) -> DocumentLoader:
        source_path = Path(path)
        extension = source_path.suffix.lower()
        loader = self._loaders_by_extension.get(extension)
        if loader is None:
            supported = ", ".join(self.supported_extensions)
            label = extension or "<no extension>"
            raise UnsupportedDocumentFormatError(
                f"unsupported document extension {label}; supported extensions: {supported}"
            )
        return loader

    def load(self, path: str | Path) -> LoadedDocument:
        source_path = Path(path)
        _validate_source_path(source_path)
        loaded = self.loader_for(source_path).load(source_path)
        if loaded.source_path != source_path:
            raise DocumentLoadError(
                "document loader returned a different source_path than the requested file"
            )
        return loaded

    def discover(self, directory: str | Path) -> list[Path]:
        """Return supported regular files in deterministic filename order."""

        source_directory = Path(directory)
        if not source_directory.exists():
            raise DocumentNotFoundError(f"document directory does not exist: {source_directory}")
        if not source_directory.is_dir():
            raise DocumentPathError(f"document path is not a directory: {source_directory}")

        return sorted(
            (
                path
                for path in source_directory.iterdir()
                if path.is_file() and path.suffix.lower() in self._loaders_by_extension
            ),
            key=lambda path: path.name,
        )


DEFAULT_DOCUMENT_LOADER_REGISTRY = DocumentLoaderRegistry(
    [
        MarkdownDocumentLoader(),
        PDFDocumentLoader(),
    ]
)


__all__ = [
    "DEFAULT_DOCUMENT_LOADER_REGISTRY",
    "DEFAULT_PDF_MIN_TEXT_CHARACTERS",
    "DocumentDecodingError",
    "DocumentDependencyError",
    "DocumentLoadError",
    "DocumentLoader",
    "DocumentLoaderRegistry",
    "DocumentMetadataError",
    "DocumentNotFoundError",
    "DocumentPathError",
    "EmptyDocumentError",
    "EncryptedDocumentError",
    "InvalidDocumentError",
    "LoadedDocument",
    "MarkdownDocumentLoader",
    "OCRRequiredError",
    "PDFDocumentLoader",
    "PDF_METADATA_SIDECAR_SUFFIX",
    "UnsupportedDocumentFormatError",
    "pdf_metadata_sidecar_path",
]
