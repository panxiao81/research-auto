from __future__ import annotations

from research_auto.parsers import ParsedPaper, parse_pdf_file


class PdfParserAdapter:
    def parse(self, *, local_path: str) -> ParsedPaper:
        return parse_pdf_file(local_path)
