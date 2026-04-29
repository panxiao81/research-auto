from __future__ import annotations

from research_auto.infrastructure.resolution.service import (
    ArtifactRecord,
    ArxivCandidate,
    _best_arxiv_artifact,
    apply_arxiv_fallback_reason,
    infer_arxiv_fallback_reason,
)


def _artifact(kind: str) -> ArtifactRecord:
    downloadable_kinds = {
        "direct_pdf",
        "publisher_pdf",
        "preprint",
        "attachment_pdf",
        "fallback_to_arxiv",
    }
    return ArtifactRecord(
        artifact_kind=kind,
        label=kind,
        resolution_reason=None,
        source_url=f"https://example.com/{kind}",
        resolved_url=(f"https://example.com/{kind}.pdf" if kind in downloadable_kinds else None),
        downloadable=kind in downloadable_kinds,
        mime_type="application/pdf" if kind in downloadable_kinds else None,
    )


def _candidate(
    *,
    title: str = "Example Paper",
    pdf_url: str = "https://arxiv.org/pdf/1234.5678.pdf",
    abs_url: str | None = "https://arxiv.org/abs/1234.5678",
    doi: str | None = None,
    score: float = 0.8,
) -> ArxivCandidate:
    return ArxivCandidate(
        title=title,
        pdf_url=pdf_url,
        abs_url=abs_url,
        doi=doi,
        score=score,
    )


def test_infer_arxiv_fallback_reason_for_detail_access_failure() -> None:
    assert (
        infer_arxiv_fallback_reason([], detail_access_failed=True)
        == "detail_page_access_failed"
    )


def test_infer_arxiv_fallback_reason_for_empty_artifacts() -> None:
    assert infer_arxiv_fallback_reason([]) == "no_links_on_detail_page"


def test_infer_arxiv_fallback_reason_for_landing_page_without_pdf() -> None:
    artifacts = [_artifact("doi"), _artifact("publication")]

    assert (
        infer_arxiv_fallback_reason(artifacts)
        == "landing_page_without_accessible_pdf"
    )


def test_infer_arxiv_fallback_reason_for_non_paper_links_only() -> None:
    artifacts = [_artifact("slides"), _artifact("poster"), _artifact("video")]

    assert infer_arxiv_fallback_reason(artifacts) == "non_paper_links_only"


def test_infer_arxiv_fallback_reason_for_mixed_non_pdf_links() -> None:
    artifacts = [_artifact("slides"), _artifact("github_repo")]

    assert infer_arxiv_fallback_reason(artifacts) == "no_accessible_paper_pdf"


def test_best_arxiv_artifact_returns_none_for_empty_candidates() -> None:
    assert _best_arxiv_artifact([], None) is None


def test_best_arxiv_artifact_rejects_low_score_without_matching_doi() -> None:
    candidate = _candidate(score=0.71, doi="10.1000/example")

    assert _best_arxiv_artifact([candidate], None) is None


def test_best_arxiv_artifact_allows_low_score_with_matching_doi() -> None:
    candidate = _candidate(score=0.71, doi="10.1000/example")

    artifact = _best_arxiv_artifact([candidate], "10.1000/example")

    assert artifact is not None
    assert artifact.artifact_kind == "fallback_to_arxiv"
    assert artifact.label == "https://arxiv.org/abs/1234.5678"
    assert artifact.source_url == "https://arxiv.org/abs/1234.5678"
    assert artifact.resolved_url == "https://arxiv.org/pdf/1234.5678.pdf"
    assert artifact.downloadable is True
    assert artifact.mime_type == "application/pdf"


def test_best_arxiv_artifact_prefers_highest_score_across_unique_pdfs() -> None:
    lower = _candidate(pdf_url="https://arxiv.org/pdf/lower.pdf", score=0.80)
    higher = _candidate(pdf_url="https://arxiv.org/pdf/higher.pdf", score=0.92)

    artifact = _best_arxiv_artifact([lower, higher], None)

    assert artifact is not None
    assert artifact.resolved_url == "https://arxiv.org/pdf/higher.pdf"


def test_apply_arxiv_fallback_reason_returns_updated_copy() -> None:
    original = ArtifactRecord(
        artifact_kind="fallback_to_arxiv",
        label="arXiv",
        resolution_reason=None,
        source_url="https://arxiv.org/abs/1234.5678",
        resolved_url="https://arxiv.org/pdf/1234.5678.pdf",
        downloadable=True,
        mime_type="application/pdf",
    )

    updated = apply_arxiv_fallback_reason(original, "detail_page_access_failed")

    assert updated is not original
    assert updated.resolution_reason == "detail_page_access_failed"
    assert updated.artifact_kind == original.artifact_kind
    assert updated.label == original.label
    assert updated.source_url == original.source_url
    assert updated.resolved_url == original.resolved_url
    assert updated.downloadable is original.downloadable
    assert updated.mime_type == original.mime_type
    assert original.resolution_reason is None
