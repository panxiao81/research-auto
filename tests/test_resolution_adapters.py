from __future__ import annotations

from research_auto.domain.records import ArtifactRecord
from research_auto.infrastructure.resolution.adapters import ResolverAdapter


def test_resolver_adapter_uses_extracted_doi_and_best_pdf_label(monkeypatch) -> None:
    monkeypatch.setattr(
        "research_auto.infrastructure.resolution.adapters.resolve_detail_page",
        lambda detail_url: [
            ArtifactRecord(
                artifact_kind="doi",
                label="DOI",
                resolution_reason=None,
                source_url=detail_url,
                resolved_url="https://doi.org/10.1234/example",
                downloadable=False,
                mime_type=None,
            ),
            ArtifactRecord(
                artifact_kind="publisher_pdf",
                label="Publisher PDF",
                resolution_reason=None,
                source_url=detail_url,
                resolved_url="https://example.com/paper.pdf",
                downloadable=True,
                mime_type="application/pdf",
            ),
        ],
    )
    monkeypatch.setattr(
        "research_auto.infrastructure.resolution.adapters.search_arxiv_fallback",
        lambda canonical_title, doi: (_ for _ in ()).throw(AssertionError("unexpected arxiv fallback")),
    )

    result = ResolverAdapter().resolve(
        detail_url="https://example.com/detail",
        canonical_title="Example Paper",
        known_doi="10.9999/ignored",
    )

    assert result.known_doi == "10.1234/example"
    assert result.best_pdf_url == "https://example.com/paper.pdf"
    assert result.best_pdf_label == "Publisher PDF"


def test_resolver_adapter_marks_arxiv_fallback_when_detail_page_access_fails(monkeypatch) -> None:
    fallback = ArtifactRecord(
        artifact_kind="fallback_to_arxiv",
        label="arXiv PDF",
        resolution_reason=None,
        source_url="https://arxiv.org/abs/1234.5678",
        resolved_url="https://arxiv.org/pdf/1234.5678.pdf",
        downloadable=True,
        mime_type="application/pdf",
    )
    captured: dict[str, str | None] = {}

    def fake_search_arxiv_fallback(canonical_title: str, doi: str | None):
        captured["canonical_title"] = canonical_title
        captured["doi"] = doi
        return fallback

    monkeypatch.setattr(
        "research_auto.infrastructure.resolution.adapters.resolve_detail_page",
        lambda detail_url: (_ for _ in ()).throw(RuntimeError("timeout")),
    )
    monkeypatch.setattr(
        "research_auto.infrastructure.resolution.adapters.search_arxiv_fallback",
        fake_search_arxiv_fallback,
    )

    result = ResolverAdapter().resolve(
        detail_url="https://example.com/detail",
        canonical_title="Example Paper",
        known_doi="10.1234/example",
    )

    assert captured == {
        "canonical_title": "Example Paper",
        "doi": "10.1234/example",
    }
    assert result.best_pdf_url == "https://arxiv.org/pdf/1234.5678.pdf"
    assert result.best_pdf_label == "arXiv PDF"
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.artifact_kind == "fallback_to_arxiv"
    assert artifact.label == "arXiv PDF"
    assert artifact.resolution_reason == "detail_page_access_failed"
    assert artifact.source_url == "https://arxiv.org/abs/1234.5678"
    assert artifact.resolved_url == "https://arxiv.org/pdf/1234.5678.pdf"
    assert artifact.downloadable is True
    assert artifact.mime_type == "application/pdf"
