from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from datalab_sdk.exceptions import DatalabAPIError
from pypdf import PdfWriter

from research_auto.application.storage_types import StorageWriteResult
from research_auto.infrastructure.parsing.adapters import PdfParserAdapter
from research_auto.infrastructure.parsing.datalab_parser import (
    DatalabParser,
    DatalabParserFallback,
)
from research_auto.infrastructure.parsing.pdf_parser import parse_pdf_file


def test_pdf_parser_adapter_uses_pypdf_only_when_no_datalab_parser_is_configured() -> None:
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

    assert parsed.parser_version == "pypdf-v1"
    assert parsed.page_count == 1


def test_pdf_parser_adapter_prefers_configured_datalab_output() -> None:
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
            return BytesIO(b"%PDF-1.4\n%stub")

    class FakeDatalabParser:
        def parse(self, source: str | BytesIO):
            return SimpleNamespace(
                parser_version="datalab-v1",
                source_text="# Title\n\nLine   one\x00",
                full_text="# Title\n\nLine one",
                abstract_text=None,
                page_count=1,
                content_hash="abc",
                chunks=["# Title\n\nLine one"],
            )

    adapter = PdfParserAdapter(storage=FakeStorage(), datalab_parser=FakeDatalabParser())
    parsed = adapter.parse(storage_uri="local://paper-1/paper.pdf")

    assert parsed.parser_version == "datalab-v1"
    assert parsed.source_text == "# Title\n\nLine   one\x00"
    assert parsed.full_text == "# Title\n\nLine one"
    assert parsed.page_count == 1


def test_pdf_parser_adapter_falls_back_to_pypdf_for_expected_datalab_fallback() -> None:
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

    class FakeDatalabParser:
        def parse(self, source: str | BytesIO):
            raise DatalabParserFallback("markdown was empty")

    adapter = PdfParserAdapter(storage=FakeStorage(), datalab_parser=FakeDatalabParser())
    parsed = adapter.parse(storage_uri="local://paper-1/paper.pdf")

    assert parsed.parser_version == "pypdf-v1"
    assert parsed.page_count == 1


def test_pdf_parser_adapter_rewinds_stream_before_pypdf_fallback() -> None:
    class FakeStorage:
        def read(self, *, storage_uri: str) -> BytesIO:
            assert storage_uri == "local://paper-1/paper.pdf"
            return BytesIO(b"%PDF-1.4\n%stub")

    class FakeDatalabParser:
        def parse(self, source: str | BytesIO):
            assert source.read(5) == b"%PDF-"
            raise DatalabParserFallback("markdown was empty")

    def fake_pypdf_parser(source: str | BytesIO):
        assert source.read() == b"%PDF-1.4\n%stub"
        return SimpleNamespace(
            parser_version="pypdf-v1",
            source_text="source",
            full_text="full",
            abstract_text=None,
            page_count=1,
            content_hash="abc",
            chunks=["chunk"],
        )

    parsed = PdfParserAdapter(
        storage=FakeStorage(),
        datalab_parser=FakeDatalabParser(),
        pypdf_parser=fake_pypdf_parser,
    ).parse(storage_uri="local://paper-1/paper.pdf")

    assert parsed.full_text == "full"


def test_datalab_parser_keeps_markdown_source_text_and_normalizes_full_text() -> None:
    captured: dict[str, object] = {}
    raw_markdown = "\n# Title\n\nLine   one\x00\n"

    class FakeClient:
        def __init__(self, *, api_key: str, base_url: str, timeout: float) -> None:
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            captured["timeout"] = timeout

        def convert(self, file_path: str | Path) -> SimpleNamespace:
            path = Path(file_path)
            captured["file_path"] = path
            captured["file_bytes"] = path.read_bytes()
            return SimpleNamespace(markdown=raw_markdown, page_count=7)

    parsed = DatalabParser(
        api_key="test-key",
        base_url="https://api.example.com",
        timeout_seconds=12.5,
        client_factory=FakeClient,
    ).parse(BytesIO(b"%PDF-1.4\n%stub"))

    assert parsed.parser_version == "datalab-v1"
    assert parsed.source_text == raw_markdown
    assert parsed.full_text == "# Title\n\nLine one"
    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://api.example.com"
    assert captured["timeout"] == 12.5
    assert captured["file_path"].suffix == ".pdf"
    assert not captured["file_path"].exists()
    assert captured["file_bytes"] == b"%PDF-1.4\n%stub"
    assert parsed.page_count == 7


def test_datalab_parser_raises_fallback_for_empty_markdown() -> None:
    class FakeClient:
        def __init__(self, *, api_key: str, base_url: str, timeout: float) -> None:
            return None

        def convert(self, file_path: str | Path) -> SimpleNamespace:
            return SimpleNamespace(markdown="\n\n", page_count=None)

    parser = DatalabParser(api_key="test-key", client_factory=FakeClient)

    try:
        parser.parse(BytesIO(b"%PDF-1.4\n%stub"))
    except DatalabParserFallback as exc:
        assert str(exc) == "Datalab returned empty markdown"
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Expected DatalabParserFallback")


def test_datalab_parser_requires_explicit_api_key() -> None:
    try:
        DatalabParser()
    except ValueError as exc:
        assert str(exc) == "Datalab API key is required"
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Expected ValueError")


def test_datalab_parser_raises_fallback_for_sdk_error() -> None:
    class FakeClient:
        def __init__(self, *, api_key: str, base_url: str, timeout: float) -> None:
            return None

        def convert(self, file_path: str | Path) -> SimpleNamespace:
            raise DatalabAPIError("Bad Gateway", status_code=502, response_data=None)

    try:
        DatalabParser(api_key="test-key", client_factory=FakeClient).parse(
            BytesIO(b"%PDF-1.4\n%stub")
        )
    except DatalabParserFallback as exc:
        assert str(exc) == "Datalab request failed: Bad Gateway"
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Expected DatalabParserFallback")


def test_parse_pdf_file_keeps_raw_source_text_and_normalizes_full_text(monkeypatch) -> None:
    class FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        def __init__(self, source: str | BytesIO) -> None:
            self.pages = [
                FakePage("Abstract\nLine   one\n\n\nLine\x00two\n"),
                FakePage("1 Introduction\nBody\ttext\n"),
            ]

    monkeypatch.setattr(
        "research_auto.infrastructure.parsing.pdf_parser.PdfReader", FakeReader
    )

    parsed = parse_pdf_file("paper.pdf")

    assert parsed.source_text == (
        "Abstract\nLine   one\n\n\nLine two\n\n1 Introduction\nBody\ttext"
    )
    assert parsed.full_text == (
        "Abstract\nLine one\n\nLine two\n\n1 Introduction\nBody text"
    )
    assert parsed.abstract_text == "Line one"
