from __future__ import annotations

from research_auto.application.storage_types import ArtifactStorageGateway
from research_auto.domain.records import ParsedPaper
from research_auto.infrastructure.parsing.pdf_parser import parse_pdf_file


class PdfParserAdapter:
    def __init__(self, *, storage: ArtifactStorageGateway) -> None:
        self.storage = storage

    def parse(self, *, storage_uri: str) -> ParsedPaper:
        return parse_pdf_file(self.storage.read(storage_uri=storage_uri))
