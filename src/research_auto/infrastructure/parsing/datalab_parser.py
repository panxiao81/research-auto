from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from datalab_sdk import DatalabClient
from datalab_sdk.exceptions import DatalabError

from research_auto.domain.records import ParsedPaper
from research_auto.infrastructure.parsing.pdf_parser import chunk_text, extract_abstract, normalize_text

PARSER_VERSION = "datalab-v1"
DEFAULT_BASE_URL = "https://www.datalab.to"


class DatalabParserFallback(Exception):
    pass


class DatalabParser:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 60.0,
        client_factory: Callable[..., object] = DatalabClient,
    ) -> None:
        if not api_key:
            raise ValueError("Datalab API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client_factory(
            api_key=api_key,
            base_url=self.base_url,
            timeout=timeout_seconds,
        )

    def parse(self, source: str | Path | BinaryIO) -> ParsedPaper:
        try:
            with _source_file_path(source) as file_path:
                result = self.client.convert(file_path=file_path)
        except DatalabError as exc:
            raise DatalabParserFallback(f"Datalab request failed: {exc}") from exc

        if getattr(result, "success", True) is False:
            error_message = getattr(result, "error", None) or "conversion failed"
            raise DatalabParserFallback(f"Datalab request failed: {error_message}")

        source_text = getattr(result, "markdown", None) or ""
        full_text = normalize_text(source_text)
        if not full_text:
            raise DatalabParserFallback("Datalab returned empty markdown")

        content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
        abstract = extract_abstract(full_text)
        chunks = chunk_text(full_text)
        return ParsedPaper(
            parser_version=PARSER_VERSION,
            source_text=source_text,
            full_text=full_text,
            abstract_text=abstract,
            page_count=getattr(result, "page_count", None) or 0,
            content_hash=content_hash,
            chunks=chunks,
        )


@contextmanager
def _source_file_path(source: str | Path | BinaryIO) -> Iterator[str | Path]:
    if isinstance(source, str):
        yield source
        return
    if isinstance(source, Path):
        yield source
        return

    data = source.read()
    suffix = Path(getattr(source, "name", "document.pdf")).suffix or ".pdf"
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        handle.write(data)
        handle.close()
        yield handle.name
    finally:
        os.unlink(handle.name)
