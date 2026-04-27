from __future__ import annotations

import logging
from typing import BinaryIO, Callable

from research_auto.application.storage_types import ArtifactStorageGateway
from research_auto.domain.records import ParsedPaper
from research_auto.infrastructure.parsing.datalab_parser import DatalabParserFallback
from research_auto.infrastructure.parsing.pdf_parser import parse_pdf_file
from research_auto.infrastructure.job_logging import adapter_log_message


logger = logging.getLogger(__name__)


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
        logger.info(adapter_log_message("parser", "start", storage_uri=storage_uri))
        try:
            source = self.storage.read(storage_uri=storage_uri)
            if self.datalab_parser is not None:
                try:
                    parsed = self.datalab_parser.parse(source)
                    logger.info(
                        adapter_log_message(
                            "parser",
                            "success",
                            storage_uri=storage_uri,
                            parser_version=parsed.parser_version,
                        )
                    )
                    return parsed
                except DatalabParserFallback:
                    if hasattr(source, "seek"):
                        source.seek(0)
            parsed = self.pypdf_parser(source)
        except Exception:  # noqa: BLE001
            logger.exception(adapter_log_message("parser", "error", storage_uri=storage_uri))
            raise
        logger.info(
            adapter_log_message(
                "parser", "success", storage_uri=storage_uri, parser_version=parsed.parser_version
            )
        )
        return parsed
