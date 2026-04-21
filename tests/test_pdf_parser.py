from __future__ import annotations

from io import BytesIO

from pypdf import PdfWriter

from research_auto.application.storage_types import StorageWriteResult
from research_auto.infrastructure.parsing.adapters import PdfParserAdapter


def test_pdf_parser_adapter_reads_from_storage() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    pdf_bytes = BytesIO()
    writer.write(pdf_bytes)
    pdf_bytes.seek(0)

    class FakeStorage:
        def write(
            self,
            *,
            paper_id: str,
            file_name: str,
            content: bytes,
            mime_type: str | None,
        ) -> StorageWriteResult:
            return StorageWriteResult(
                storage_uri=f"local://{paper_id}/{file_name}",
                storage_key=f"{paper_id}/{file_name}",
                byte_size=len(content),
                mime_type=mime_type,
                checksum_sha256="abc",
            )

        def read(self, *, storage_uri: str) -> BytesIO:
            assert storage_uri == "local://paper-1/paper.pdf"
            pdf_bytes.seek(0)
            return pdf_bytes

    adapter = PdfParserAdapter(storage=FakeStorage())
    parsed = adapter.parse(storage_uri="local://paper-1/paper.pdf")

    assert parsed.page_count == 1
