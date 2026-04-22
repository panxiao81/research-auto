from __future__ import annotations

from typing import BinaryIO, Callable

from research_auto.application.storage_types import ArtifactStorageGateway
from research_auto.domain.records import ParsedPaper
from research_auto.infrastructure.parsing.datalab_parser import DatalabParserFallback
from research_auto.infrastructure.parsing.pdf_parser import parse_pdf_file


class PdfParserAdapter:
    def __init__(
        self,
        *,
        storage: ArtifactStorageGateway,
        datalab_parser: object | None = None,
        pypdf_parser: Callable[[str | BinaryIO], ParsedPaper] = parse_pdf_file,
    ) -> None:
        self.storage = storage
        self.datalab_parser = datalab_parser
        self.pypdf_parser = pypdf_parser

    def parse(self, *, storage_uri: str) -> ParsedPaper:
        source = self.storage.read(storage_uri=storage_uri)
        if self.datalab_parser is not None:
            try:
                return self.datalab_parser.parse(source)
            except DatalabParserFallback:
                if hasattr(source, "seek"):
                    source.seek(0)
        return self.pypdf_parser(source)
