from __future__ import annotations

from research_auto.domain.records import ParsedPaper
from research_auto.infrastructure.parsing.pdf_parser import parse_pdf_file


class PdfParserAdapter:
    def parse(self, *, local_path: str) -> ParsedPaper:
        return parse_pdf_file(local_path)
