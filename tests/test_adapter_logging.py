from __future__ import annotations

import logging
from io import BytesIO
from types import SimpleNamespace

from research_auto.application.llm_types import PaperSummary
from research_auto.domain.records import ArtifactRecord, CrawlResult
from research_auto.infrastructure.crawlers.adapters import ResearchrCrawlerAdapter
from research_auto.infrastructure.job_logging import job_logging_context
from research_auto.infrastructure.llm.adapters import LiteLLMSummaryAdapter
from research_auto.infrastructure.parsing.adapters import PdfParserAdapter
from research_auto.infrastructure.resolution.adapters import (
    HttpDownloadAdapter,
    ResolverAdapter,
)
from research_auto.infrastructure.storage.adapters import (
    LocalArtifactStorageAdapter,
    S3ArtifactStorageAdapter,
)


def _job_context() -> dict[str, object]:
    return {
        "job_id": "job-1",
        "job_type": "parse_artifact",
        "attempt_id": "attempt-1",
        "worker_id": "worker-1",
        "payload": {"paper_id": "paper-1", "storage_uri": "local://paper-1/paper.pdf"},
    }


def test_crawler_adapter_logs_job_context_and_url(monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        "research_auto.infrastructure.crawlers.adapters.crawl_track_sync",
        lambda track_url, headless: (CrawlResult(discovered=0, paper_candidates=[]), "<html></html>"),
    )

    with job_logging_context(**_job_context()):
        with caplog.at_level(logging.INFO, logger="research_auto.infrastructure.crawlers.adapters"):
            ResearchrCrawlerAdapter().crawl_track(track_url="https://example.com/track", headless=True)

    assert any("adapter=crawler" in record.message and "event=start" in record.message for record in caplog.records)
    assert any("adapter=crawler" in record.message and "event=success" in record.message for record in caplog.records)
    assert any("job_id=job-1" in record.message and "payload={\"paper_id\":\"paper-1\",\"storage_uri\":\"local://paper-1/paper.pdf\"}" in record.message for record in caplog.records)


def test_resolver_and_downloader_adapters_log_job_context(monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        "research_auto.infrastructure.resolution.adapters.resolve_detail_page",
        lambda detail_url: [
            ArtifactRecord(
                artifact_kind="pdf",
                label="pdf",
                resolution_reason="direct",
                source_url=detail_url,
                resolved_url="https://example.com/paper.pdf",
                downloadable=True,
                mime_type="application/pdf",
            )
        ],
    )
    monkeypatch.setattr(
        "research_auto.infrastructure.resolution.adapters.pick_best_urls",
        lambda artifacts: ("https://example.com/paper.pdf", "https://example.com"),
    )
    monkeypatch.setattr(
        "research_auto.infrastructure.resolution.adapters.download_artifact",
        lambda url, label: {"content": b"%PDF-1.4", "file_name": "paper.pdf", "checksum_sha256": "abc", "byte_size": 8, "mime_type": "application/pdf"},
    )

    with job_logging_context(**_job_context()):
        with caplog.at_level(logging.INFO, logger="research_auto.infrastructure.resolution.adapters"):
            result = ResolverAdapter().resolve(
                detail_url="https://example.com/detail",
                canonical_title="Paper",
                known_doi=None,
            )
            HttpDownloadAdapter().download(url="https://example.com/paper.pdf", paper_id="paper-1", label="pdf")

    assert result.best_pdf_url == "https://example.com/paper.pdf"
    assert any("adapter=resolver" in record.message and "event=start" in record.message for record in caplog.records)
    assert any("adapter=resolver" in record.message and "event=success" in record.message for record in caplog.records)
    assert any("adapter=downloader" in record.message and "event=start" in record.message for record in caplog.records)
    assert any("adapter=downloader" in record.message and "event=success" in record.message for record in caplog.records)


def test_parser_and_summarizer_adapters_log_job_context(monkeypatch, caplog) -> None:
    class FakeStorage:
        def read(self, *, storage_uri: str) -> BytesIO:
            return BytesIO(b"%PDF-1.4")

    class FakeParser:
        def parse(self, source: str | BytesIO):
            return SimpleNamespace(
                parser_version="fake-v1",
                source_text="source",
                full_text="full",
                abstract_text="abstract",
                page_count=1,
                content_hash="hash",
                chunks=["chunk"],
            )

    class FakeProvider:
        provider_name = "mock"

        def summarize(self, *, title: str, abstract: str | None, chunks: list[str]) -> PaperSummary:
            return PaperSummary(
                problem="p",
                research_question="rq",
                research_question_zh="rq zh",
                method="m",
                evaluation="e",
                results="r",
                conclusions="c",
                conclusions_zh="c zh",
                future_work="f",
                future_work_zh="f zh",
                takeaway="t",
                summary_short="short",
                summary_long="long",
                summary_short_zh="short zh",
                summary_long_zh="long zh",
                contributions=[],
                limitations=[],
                tags=[],
                raw_response={},
            )

    monkeypatch.setattr(
        "research_auto.infrastructure.llm.adapters.build_provider",
        lambda settings: FakeProvider(),
    )

    with job_logging_context(**_job_context()):
        with caplog.at_level(logging.INFO, logger="research_auto.infrastructure.parsing.adapters"):
            PdfParserAdapter(storage=FakeStorage(), pypdf_parser=lambda source: FakeParser().parse(source)).parse(
                storage_uri="local://paper-1/paper.pdf"
            )
        with caplog.at_level(logging.INFO, logger="research_auto.infrastructure.llm.adapters"):
            LiteLLMSummaryAdapter(settings=SimpleNamespace()).summarize(
                title="Paper", abstract="Abstract", chunks=["chunk"]
            )

    assert any("adapter=parser" in record.message and "event=start" in record.message for record in caplog.records)
    assert any("adapter=parser" in record.message and "event=success" in record.message for record in caplog.records)
    assert any("adapter=summarizer" in record.message and "event=start" in record.message for record in caplog.records)
    assert any("adapter=summarizer" in record.message and "event=success" in record.message for record in caplog.records)


def test_storage_adapters_log_job_context(tmp_path, monkeypatch, caplog) -> None:
    class FakeS3:
        def __init__(self) -> None:
            self.objects: dict[tuple[str, str], bytes] = {}

        def upload_fileobj(self, fileobj, bucket, key):
            self.objects[(bucket, key)] = fileobj.read()

        def get_object(self, Bucket, Key):
            return {"Body": BytesIO(self.objects[(Bucket, Key)])}

    fake_s3 = FakeS3()
    monkeypatch.setattr(
        "research_auto.infrastructure.storage.adapters.boto3.client",
        lambda *args, **kwargs: fake_s3,
    )

    with job_logging_context(**_job_context()):
        with caplog.at_level(logging.INFO, logger="research_auto.infrastructure.storage.adapters"):
            local = LocalArtifactStorageAdapter(artifact_root=str(tmp_path))
            written = local.write(
                paper_id="paper-1",
                file_name="paper.pdf",
                content=b"%PDF-1.4",
                mime_type="application/pdf",
            )
            local.read(storage_uri=written.storage_uri)
            s3 = S3ArtifactStorageAdapter(bucket="papers", prefix="artifacts")
            written_s3 = s3.write(
                paper_id="paper-1",
                file_name="paper.pdf",
                content=b"%PDF-1.4",
                mime_type="application/pdf",
            )
            s3.read(storage_uri=written_s3.storage_uri)

    assert any("adapter=storage" in record.message and "event=start" in record.message for record in caplog.records)
    assert any("adapter=storage" in record.message and "event=success" in record.message for record in caplog.records)
