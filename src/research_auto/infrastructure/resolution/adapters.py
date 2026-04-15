from __future__ import annotations

from typing import Any

from research_auto.application.ports import DownloadResult, ResolutionResult
from research_auto.infrastructure.resolution.service import (
    apply_arxiv_fallback_reason,
    download_artifact,
    extract_doi,
    infer_arxiv_fallback_reason,
    pick_best_urls,
    resolve_detail_page,
    search_arxiv_fallback,
)


class ResolverAdapter:
    def resolve(
        self, *, detail_url: str | None, canonical_title: str, known_doi: str | None
    ) -> ResolutionResult:
        artifacts: list[Any] = []
        detail_access_failed = False
        if detail_url:
            try:
                artifacts = resolve_detail_page(detail_url)
            except Exception:  # noqa: BLE001
                detail_access_failed = True
        extracted_doi = next(
            (
                extract_doi(artifact.resolved_url)
                for artifact in artifacts
                if artifact.artifact_kind == "doi"
            ),
            None,
        )
        effective_doi = extracted_doi or known_doi
        if not pick_best_urls(artifacts)[0]:
            arxiv_artifact = search_arxiv_fallback(canonical_title, effective_doi)
            if arxiv_artifact is not None:
                artifacts.append(
                    apply_arxiv_fallback_reason(
                        arxiv_artifact,
                        infer_arxiv_fallback_reason(
                            artifacts, detail_access_failed=detail_access_failed
                        ),
                    )
                )
        best_pdf_url, best_landing_url = pick_best_urls(artifacts)
        best_pdf_artifact = next(
            (
                artifact
                for artifact in artifacts
                if artifact.resolved_url == best_pdf_url
            ),
            None,
        )
        return ResolutionResult(
            artifacts=artifacts,
            best_pdf_url=best_pdf_url,
            best_landing_url=best_landing_url,
            known_doi=effective_doi,
            best_pdf_label=best_pdf_artifact.label if best_pdf_artifact else None,
        )


class FilesystemDownloadAdapter:
    def download(
        self, *, url: str, artifact_root: str, paper_id: str, label: str | None
    ) -> DownloadResult:
        result = download_artifact(url, artifact_root, paper_id, label)
        return DownloadResult(
            local_path=result["local_path"],
            checksum_sha256=result["checksum_sha256"],
            byte_size=result["byte_size"],
            mime_type=result["mime_type"],
        )
