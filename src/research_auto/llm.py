from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import litellm
from litellm.llms.chatgpt.common_utils import (
    CHATGPT_API_BASE,
    ensure_chatgpt_session_id,
    get_chatgpt_default_headers,
    get_chatgpt_default_instructions,
)
from litellm.llms.custom_httpx.http_handler import _get_httpx_client

from research_auto.config import Settings


PROMPT_VERSION = "summary-v3"
_provider_singletons: dict[tuple[str, ...], LLMProvider] = {}


@dataclass(slots=True)
class PaperSummary:
    problem: str
    research_question: str
    research_question_zh: str
    method: str
    evaluation: str
    results: str
    conclusions: str
    conclusions_zh: str
    future_work: str
    future_work_zh: str
    takeaway: str
    summary_short: str
    summary_long: str
    summary_short_zh: str
    summary_long_zh: str
    contributions: list[str]
    limitations: list[str]
    tags: list[str]
    raw_response: dict[str, Any]


@dataclass(slots=True)
class QuestionAnswer:
    answer: str
    answer_zh: str
    evidence_quotes: list[str]
    confidence: str
    raw_response: dict[str, Any]


def fallback_answer_from_summary(
    *, question: str, summary_row: dict[str, Any] | None, chunk_quotes: list[str]
) -> QuestionAnswer:
    if not summary_row:
        return QuestionAnswer(
            answer="I could not answer from the stored summaries, and the live model request was unavailable.",
            answer_zh="我无法从已存储的摘要中回答这个问题，而且实时模型请求当前不可用。",
            evidence_quotes=chunk_quotes[:3],
            confidence="low",
            raw_response={"fallback": "no_summary"},
        )

    q = question.lower()
    mappings = [
        (
            ["research question", "question", "研究问题"],
            "research_question",
            "research_question_zh",
        ),
        (["conclusion", "conclusions", "结论"], "conclusions", "conclusions_zh"),
        (
            ["future work", "future", "未来工作", "后续工作"],
            "future_work",
            "future_work_zh",
        ),
        (["method", "approach", "方法"], "method", None),
        (["result", "results", "finding", "结果"], "results", None),
    ]
    for keywords, en_key, zh_key in mappings:
        if any(keyword in q for keyword in keywords):
            answer = summary_row.get(en_key) or summary_row.get("summary_long") or ""
            answer_zh = (
                summary_row.get(zh_key) or summary_row.get("summary_long_zh") or ""
            )
            return QuestionAnswer(
                answer=answer,
                answer_zh=answer_zh,
                evidence_quotes=chunk_quotes[:3],
                confidence="medium",
                raw_response={"fallback": en_key},
            )

    return QuestionAnswer(
        answer=summary_row.get("summary_long")
        or summary_row.get("summary_short")
        or "",
        answer_zh=summary_row.get("summary_long_zh")
        or summary_row.get("summary_short_zh")
        or "",
        evidence_quotes=chunk_quotes[:3],
        confidence="medium",
        raw_response={"fallback": "summary_long"},
    )


class LLMProvider:
    provider_name = "base"

    def summarize(
        self, *, title: str, abstract: str | None, chunks: list[str]
    ) -> PaperSummary:
        raise NotImplementedError

    def answer_question(
        self, *, question: str, paper_context: str, chunk_quotes: list[str]
    ) -> QuestionAnswer:
        raise NotImplementedError


class MockProvider(LLMProvider):
    provider_name = "mock"

    def summarize(
        self, *, title: str, abstract: str | None, chunks: list[str]
    ) -> PaperSummary:
        source = abstract or (chunks[0][:1200] if chunks else "")
        problem = f"This paper studies {title}."
        research_question = f"What research question does {title} address, and how convincing are its conclusions?"
        research_question_zh = (
            f"这篇论文试图回答什么研究问题，以及它的结论是否有说服力？"
        )
        method = source[:240].strip() or "Method details were not extracted."
        evaluation = "Evaluation details are not available in mock mode."
        results = "Results are not available in mock mode."
        conclusions = "Conclusions are not available in mock mode."
        conclusions_zh = "当前为 mock 模式，未生成可靠的中文结论。"
        future_work = "Future work is not available in mock mode."
        future_work_zh = "当前为 mock 模式，未生成可靠的中文未来工作总结。"
        takeaway = "Use a real LLM provider for high-quality research reading output."
        summary_short = f"{title}: {source[:260].strip()}"
        summary_long = (
            source[:2000].strip() or f"No extracted text available for {title}."
        )
        summary_short_zh = f"{title}：{source[:120].strip()}"
        summary_long_zh = "当前为 mock 摘要，请切换到真实模型以获得高质量中文总结。"
        contributions = [summary_long[:200].strip()] if summary_long else []
        limitations = [
            "Mock summary provider used; replace with a configured LLM provider for higher quality output."
        ]
        tags = infer_tags(title, abstract or source)
        return PaperSummary(
            problem,
            research_question,
            research_question_zh,
            method,
            evaluation,
            results,
            conclusions,
            conclusions_zh,
            future_work,
            future_work_zh,
            takeaway,
            summary_short,
            summary_long,
            summary_short_zh,
            summary_long_zh,
            contributions,
            limitations,
            tags,
            {"provider": "mock"},
        )

    def answer_question(
        self, *, question: str, paper_context: str, chunk_quotes: list[str]
    ) -> QuestionAnswer:
        answer = f"Mock answer for question: {question}"
        answer_zh = f"针对问题的 mock 回答：{question}"
        return QuestionAnswer(
            answer=answer,
            answer_zh=answer_zh,
            evidence_quotes=chunk_quotes[:3],
            confidence="low",
            raw_response={"provider": "mock"},
        )


class LiteLLMProvider(LLMProvider):
    provider_name = "litellm"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def summarize(
        self, *, title: str, abstract: str | None, chunks: list[str]
    ) -> PaperSummary:
        if self.settings.llm_provider == "codex_oauth":
            return self._summarize_via_chatgpt_responses(
                title=title, abstract=abstract, chunks=chunks
            )
        if self.settings.llm_provider == "github_copilot_oauth":
            return self._summarize_via_litellm_responses(
                title=title, abstract=abstract, chunks=chunks
            )

        env_overrides, cleanup = litellm_env_for_settings(self.settings)
        previous = apply_env_overrides(env_overrides)
        try:
            response = litellm.completion(
                model=litellm_model_name(self.settings),
                messages=[
                    {"role": "system", "content": "Return strict JSON only."},
                    {
                        "role": "user",
                        "content": build_prompt(
                            title=title, abstract=abstract, chunks=chunks
                        ),
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "paper_summary",
                        "strict": True,
                        "schema": summary_schema(),
                    },
                },
            )
        finally:
            restore_env(previous)
            if cleanup is not None:
                cleanup.cleanup()

        content = response.choices[0].message.content
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        data = json.loads(content)
        return summary_from_json(data, raw_response=safe_model_dump(response))

    def _summarize_via_litellm_responses(
        self, *, title: str, abstract: str | None, chunks: list[str]
    ) -> PaperSummary:
        env_overrides, cleanup = litellm_env_for_settings(self.settings)
        previous = apply_env_overrides(env_overrides)
        try:
            response = litellm.responses(
                model=litellm_model_name(self.settings),
                input=[
                    {
                        "role": "user",
                        "content": build_prompt(
                            title=title, abstract=abstract, chunks=chunks
                        ),
                    }
                ],
                text={"format": summary_schema_text_format()},
                store=False,
            )
        finally:
            restore_env(previous)
            if cleanup is not None:
                cleanup.cleanup()
        data = extract_json_from_litellm_responses(response)
        return summary_from_json(data, raw_response=safe_model_dump(response))

    def _summarize_via_chatgpt_responses(
        self, *, title: str, abstract: str | None, chunks: list[str]
    ) -> PaperSummary:
        env_overrides, cleanup = litellm_env_for_settings(self.settings)
        previous = apply_env_overrides(env_overrides)
        try:
            auth = load_codex_auth(self.settings)
            session_id = ensure_chatgpt_session_id({})
            headers = get_chatgpt_default_headers(
                auth["access_token"], auth.get("account_id"), session_id
            )
            body = {
                "model": self.settings.llm_model,
                "input": [
                    {
                        "role": "user",
                        "content": build_prompt(
                            title=title, abstract=abstract, chunks=chunks
                        ),
                    }
                ],
                "instructions": f"{get_chatgpt_default_instructions()}\n\nReturn strict JSON only.",
                "store": False,
                "stream": True,
                "text": {"format": summary_schema_text_format()},
            }
            client = _get_httpx_client()
            response = client.post(
                f"{CHATGPT_API_BASE}/responses",
                headers=headers,
                json=body,
                timeout=180,
            )
            response.raise_for_status()
            data = extract_json_from_sse_body(response.text)
            return summary_from_json(data, raw_response={"body": response.text})
        finally:
            restore_env(previous)
            if cleanup is not None:
                cleanup.cleanup()

    def answer_question(
        self, *, question: str, paper_context: str, chunk_quotes: list[str]
    ) -> QuestionAnswer:
        if self.settings.llm_provider == "codex_oauth":
            return self._answer_via_chatgpt_responses(
                question=question,
                paper_context=paper_context,
                chunk_quotes=chunk_quotes,
            )
        if self.settings.llm_provider == "github_copilot_oauth":
            return self._answer_via_litellm_responses(
                question=question,
                paper_context=paper_context,
                chunk_quotes=chunk_quotes,
            )

        env_overrides, cleanup = litellm_env_for_settings(self.settings)
        previous = apply_env_overrides(env_overrides)
        try:
            response = litellm.completion(
                model=litellm_model_name(self.settings),
                messages=[
                    {"role": "system", "content": "Return strict JSON only."},
                    {
                        "role": "user",
                        "content": build_qa_prompt(
                            question=question,
                            paper_context=paper_context,
                            chunk_quotes=chunk_quotes,
                        ),
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "paper_answer",
                        "strict": True,
                        "schema": qa_schema(),
                    },
                },
            )
        finally:
            restore_env(previous)
            if cleanup is not None:
                cleanup.cleanup()
        content = response.choices[0].message.content
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        data = json.loads(content)
        return answer_from_json(data, raw_response=safe_model_dump(response))

    def _answer_via_litellm_responses(
        self, *, question: str, paper_context: str, chunk_quotes: list[str]
    ) -> QuestionAnswer:
        env_overrides, cleanup = litellm_env_for_settings(self.settings)
        previous = apply_env_overrides(env_overrides)
        try:
            response = litellm.responses(
                model=litellm_model_name(self.settings),
                input=[
                    {
                        "role": "user",
                        "content": build_qa_prompt(
                            question=question,
                            paper_context=paper_context,
                            chunk_quotes=chunk_quotes,
                        ),
                    }
                ],
                text={"format": qa_schema_text_format()},
                store=False,
            )
        finally:
            restore_env(previous)
            if cleanup is not None:
                cleanup.cleanup()
        data = extract_json_from_litellm_responses(response)
        return answer_from_json(data, raw_response=safe_model_dump(response))

    def _answer_via_chatgpt_responses(
        self, *, question: str, paper_context: str, chunk_quotes: list[str]
    ) -> QuestionAnswer:
        env_overrides, cleanup = litellm_env_for_settings(self.settings)
        previous = apply_env_overrides(env_overrides)
        try:
            auth = load_codex_auth(self.settings)
            session_id = ensure_chatgpt_session_id({})
            headers = get_chatgpt_default_headers(
                auth["access_token"], auth.get("account_id"), session_id
            )
            body = {
                "model": self.settings.llm_model,
                "input": [
                    {
                        "role": "user",
                        "content": build_qa_prompt(
                            question=question,
                            paper_context=paper_context,
                            chunk_quotes=chunk_quotes,
                        ),
                    }
                ],
                "instructions": f"{get_chatgpt_default_instructions()}\n\nReturn strict JSON only.",
                "store": False,
                "stream": True,
                "text": {"format": qa_schema_text_format()},
            }
            client = _get_httpx_client()
            response = client.post(
                f"{CHATGPT_API_BASE}/responses",
                headers=headers,
                json=body,
                timeout=180,
            )
            response.raise_for_status()
            data = extract_json_from_sse_body(response.text)
            return answer_from_json(data, raw_response={"body": response.text})
        finally:
            restore_env(previous)
            if cleanup is not None:
                cleanup.cleanup()


class GitHubModelsCLIProvider(LLMProvider):
    provider_name = "github_models_cli"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def summarize(
        self, *, title: str, abstract: str | None, chunks: list[str]
    ) -> PaperSummary:
        prompt = build_prompt(title=title, abstract=abstract, chunks=chunks)
        completed = subprocess.run(
            ["gh", "models", "run", self.settings.llm_model, prompt],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(completed.stdout)
        return summary_from_json(data, raw_response={"stdout": completed.stdout})

    def answer_question(
        self, *, question: str, paper_context: str, chunk_quotes: list[str]
    ) -> QuestionAnswer:
        prompt = build_qa_prompt(
            question=question, paper_context=paper_context, chunk_quotes=chunk_quotes
        )
        completed = subprocess.run(
            ["gh", "models", "run", self.settings.llm_model, prompt],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(completed.stdout)
        return answer_from_json(data, raw_response={"stdout": completed.stdout})


class CodexCLIProvider(LLMProvider):
    provider_name = "codex_cli"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def summarize(
        self, *, title: str, abstract: str | None, chunks: list[str]
    ) -> PaperSummary:
        prompt = (
            build_prompt(title=title, abstract=abstract, chunks=chunks)
            + "\n\nReturn strict JSON only."
        )
        completed = subprocess.run(
            ["codex", "exec", "--model", self.settings.llm_model, prompt],
            check=True,
            capture_output=True,
            text=True,
        )
        data = extract_json_from_text(completed.stdout)
        data = ensure_chinese_fields(data, self.settings.llm_model)
        return summary_from_json(
            data, raw_response={"stdout": completed.stdout, "stderr": completed.stderr}
        )

    def answer_question(
        self, *, question: str, paper_context: str, chunk_quotes: list[str]
    ) -> QuestionAnswer:
        prompt = (
            build_qa_prompt(
                question=question,
                paper_context=paper_context,
                chunk_quotes=chunk_quotes,
            )
            + "\n\nReturn strict JSON only."
        )
        completed = subprocess.run(
            ["codex", "exec", "--model", self.settings.llm_model, prompt],
            check=True,
            capture_output=True,
            text=True,
        )
        data = extract_json_from_text(completed.stdout)
        data = ensure_chinese_answer_fields(data, self.settings.llm_model)
        return answer_from_json(
            data, raw_response={"stdout": completed.stdout, "stderr": completed.stderr}
        )


def build_provider(settings: Settings) -> LLMProvider:
    key = provider_singleton_key(settings)
    cached = _provider_singletons.get(key)
    if cached is not None:
        return cached

    if settings.llm_provider == "mock":
        provider = MockProvider()
    elif settings.llm_provider == "codex_cli":
        provider = CodexCLIProvider(settings)
    elif settings.llm_provider in {
        "litellm",
        "openai_compatible",
        "codex_oauth",
        "github_copilot_oauth",
    }:
        provider = LiteLLMProvider(settings)
    elif settings.llm_provider == "github_models_cli":
        provider = GitHubModelsCLIProvider(settings)
    else:
        raise ValueError(f"unsupported LLM provider: {settings.llm_provider}")

    _provider_singletons[key] = provider
    return provider


def provider_singleton_key(settings: Settings) -> tuple[str, ...]:
    return (
        settings.llm_provider,
        settings.llm_model,
        settings.llm_base_url,
        settings.github_copilot_base_url,
        settings.litellm_backend or "",
        settings.codex_auth_file,
        settings.codex_access_token or "",
        settings.codex_account_id or "",
        settings.github_copilot_token or "",
        settings.llm_api_key or "",
    )


def build_fallback_summary(
    *, title: str, abstract: str | None, chunks: list[str], error: str
) -> PaperSummary:
    base = MockProvider().summarize(title=title, abstract=abstract, chunks=chunks)
    base.raw_response = {"fallback": True, "error": error, **base.raw_response}
    return base


def litellm_model_name(settings: Settings) -> str:
    if settings.litellm_backend:
        return f"{settings.litellm_backend}/{settings.llm_model}"
    mapping = {
        "openai_compatible": settings.llm_model,
        "codex_oauth": f"chatgpt/{settings.llm_model}",
        "github_copilot_oauth": f"github_copilot/{settings.llm_model}",
        "litellm": settings.llm_model,
    }
    return mapping.get(settings.llm_provider, settings.llm_model)


def litellm_env_for_settings(
    settings: Settings,
) -> tuple[dict[str, str | None], tempfile.TemporaryDirectory[str] | None]:
    if settings.llm_provider == "openai_compatible":
        return {
            "OPENAI_API_KEY": settings.llm_api_key,
            "OPENAI_API_BASE": settings.llm_base_url,
        }, None
    if settings.llm_provider == "codex_oauth":
        auth = load_codex_auth(settings)
        tempdir = tempfile.TemporaryDirectory(prefix="research-auto-chatgpt-")
        auth_path = Path(tempdir.name) / "auth.json"
        auth_path.write_text(
            json.dumps(
                {
                    "access_token": auth.get("access_token"),
                    "refresh_token": auth.get("refresh_token"),
                    "id_token": auth.get("id_token"),
                    "account_id": auth.get("account_id"),
                    "expires_at": auth.get("expires_at"),
                }
            )
        )
        return {
            "CHATGPT_TOKEN_DIR": tempdir.name,
            "CHATGPT_AUTH_FILE": "auth.json",
            "CHATGPT_ORIGINATOR": "research-auto",
            "CHATGPT_USER_AGENT": "research-auto/0.1",
        }, tempdir
    if settings.llm_provider == "github_copilot_oauth":
        return {
            "GITHUB_COPILOT_API_KEY": settings.github_copilot_token,
            "GITHUB_COPILOT_BASE_URL": settings.github_copilot_base_url,
        }, None
    return {}, None


def apply_env_overrides(overrides: dict[str, str | None]) -> dict[str, str | None]:
    previous: dict[str, str | None] = {}
    for key, value in overrides.items():
        previous[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    return previous


def restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def build_prompt(*, title: str, abstract: str | None, chunks: list[str]) -> str:
    chunk_text = "\n\n---\n\n".join(chunks[:4])
    return (
        "Read the research paper content and produce strict JSON with these keys: "
        "problem, research_question, research_question_zh, method, evaluation, results, conclusions, conclusions_zh, future_work, future_work_zh, takeaway, summary_short, summary_long, summary_short_zh, summary_long_zh, contributions, limitations, tags. "
        "Use concise technical English for the English fields and natural fluent Simplified Chinese for the Chinese fields. "
        "contributions, limitations, and tags must be arrays of strings.\n\n"
        f"Title: {title}\n\n"
        f"Abstract: {abstract or 'N/A'}\n\n"
        f"Content:\n{chunk_text}"
    )


def summary_from_json(
    data: dict[str, Any], *, raw_response: dict[str, Any]
) -> PaperSummary:
    return PaperSummary(
        problem=str(data.get("problem") or "").strip(),
        research_question=str(data.get("research_question") or "").strip(),
        research_question_zh=str(data.get("research_question_zh") or "").strip(),
        method=str(data.get("method") or "").strip(),
        evaluation=str(data.get("evaluation") or "").strip(),
        results=str(data.get("results") or "").strip(),
        conclusions=str(data.get("conclusions") or "").strip(),
        conclusions_zh=str(data.get("conclusions_zh") or "").strip(),
        future_work=str(data.get("future_work") or "").strip(),
        future_work_zh=str(data.get("future_work_zh") or "").strip(),
        takeaway=str(data.get("takeaway") or "").strip(),
        summary_short=str(data.get("summary_short") or "").strip(),
        summary_long=str(data.get("summary_long") or "").strip(),
        summary_short_zh=str(data.get("summary_short_zh") or "").strip(),
        summary_long_zh=str(data.get("summary_long_zh") or "").strip(),
        contributions=[
            str(item).strip()
            for item in data.get("contributions", [])
            if str(item).strip()
        ],
        limitations=[
            str(item).strip()
            for item in data.get("limitations", [])
            if str(item).strip()
        ],
        tags=[str(item).strip() for item in data.get("tags", []) if str(item).strip()],
        raw_response=raw_response,
    )


def summary_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "problem": {"type": "string"},
            "research_question": {"type": "string"},
            "research_question_zh": {"type": "string"},
            "method": {"type": "string"},
            "evaluation": {"type": "string"},
            "results": {"type": "string"},
            "conclusions": {"type": "string"},
            "conclusions_zh": {"type": "string"},
            "future_work": {"type": "string"},
            "future_work_zh": {"type": "string"},
            "takeaway": {"type": "string"},
            "summary_short": {"type": "string"},
            "summary_long": {"type": "string"},
            "summary_short_zh": {"type": "string"},
            "summary_long_zh": {"type": "string"},
            "contributions": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "problem",
            "research_question",
            "research_question_zh",
            "method",
            "evaluation",
            "results",
            "conclusions",
            "conclusions_zh",
            "future_work",
            "future_work_zh",
            "takeaway",
            "summary_short",
            "summary_long",
            "summary_short_zh",
            "summary_long_zh",
            "contributions",
            "limitations",
            "tags",
        ],
        "additionalProperties": False,
    }


def summary_schema_text_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "paper_summary",
        "strict": True,
        "schema": summary_schema(),
    }


def qa_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "answer_zh": {"type": "string"},
            "evidence_quotes": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string"},
        },
        "required": ["answer", "answer_zh", "evidence_quotes", "confidence"],
        "additionalProperties": False,
    }


def qa_schema_text_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "paper_answer",
        "strict": True,
        "schema": qa_schema(),
    }


def load_codex_auth(settings: Settings) -> dict[str, str | None]:
    if settings.codex_access_token:
        return {
            "access_token": settings.codex_access_token,
            "account_id": settings.codex_account_id,
            "refresh_token": None,
            "id_token": None,
            "expires_at": None,
        }

    path = Path(settings.codex_auth_file).expanduser()
    if not path.exists():
        return {"access_token": None, "account_id": None}

    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {"access_token": None, "account_id": None}

    tokens = payload.get("tokens") or {}
    access_token = tokens.get("access_token")
    return {
        "access_token": access_token,
        "account_id": tokens.get("account_id"),
        "refresh_token": tokens.get("refresh_token"),
        "id_token": tokens.get("id_token"),
        "expires_at": str(decode_exp(access_token))
        if access_token and decode_exp(access_token)
        else None,
    }


def decode_exp(token: str | None) -> int | None:
    if not token:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(
            __import__("base64").urlsafe_b64decode(payload.encode()).decode()
        )
        exp = data.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None


def infer_tags(title: str, text: str) -> list[str]:
    lowered = f"{title} {text}".lower()
    tags: list[str] = []
    for tag, needles in {
        "llm": ["llm", "large language model", "genai"],
        "testing": ["test", "fuzz", "bug"],
        "security": ["security", "vulnerability", "secret", "privacy"],
        "analysis": ["analysis", "static", "dynamic", "verification"],
    }.items():
        if any(needle in lowered for needle in needles):
            tags.append(tag)
    return tags


def safe_model_dump(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if isinstance(response, dict):
        return response
    return {"response": str(response)}


def extract_json_from_text(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in Codex CLI output")
    return json.loads(text[start : end + 1])


def extract_json_from_sse_body(body_text: str) -> dict[str, Any]:
    completed_text = ""
    for line in body_text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type == "response.output_text.done":
            text = event.get("text")
            if isinstance(text, str) and text.strip():
                completed_text = text
        elif event_type == "response.output_text.delta" and not completed_text:
            delta = event.get("delta")
            if isinstance(delta, str):
                completed_text += delta
    if not completed_text.strip():
        raise ValueError("No output_text found in SSE response body")
    return json.loads(completed_text)


def extract_json_from_litellm_responses(response: Any) -> dict[str, Any]:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return json.loads(output_text)

    payload = safe_model_dump(response)

    text = payload.get("output_text")
    if isinstance(text, str) and text.strip():
        return json.loads(text)

    output_items = payload.get("output")
    if isinstance(output_items, list):
        for item in output_items:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text" and isinstance(
                    part.get("text"), str
                ):
                    candidate = part.get("text", "").strip()
                    if candidate:
                        return json.loads(candidate)

    raise ValueError("No JSON output_text found in LiteLLM responses payload")


def ensure_chinese_fields(data: dict[str, Any], model: str) -> dict[str, Any]:
    required_pairs = [
        ("research_question", "research_question_zh"),
        ("conclusions", "conclusions_zh"),
        ("future_work", "future_work_zh"),
    ]
    missing_pairs = [
        (en_key, zh_key)
        for en_key, zh_key in required_pairs
        if data.get(en_key) and not data.get(zh_key)
    ]
    missing = [zh_key for _, zh_key in missing_pairs]
    if not missing:
        return data

    payload = {zh_key: data.get(en_key, "") for en_key, zh_key in missing_pairs}
    prompt = (
        "Translate the following research-summary fields into concise, natural Simplified Chinese. "
        "Return strict JSON only with the same *_zh keys.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    completed = subprocess.run(
        ["codex", "exec", "--model", model, prompt],
        check=True,
        capture_output=True,
        text=True,
    )
    translated = extract_json_from_text(completed.stdout)
    for key in missing:
        data[key] = translated.get(key, "")
    return data


def ensure_chinese_answer_fields(data: dict[str, Any], model: str) -> dict[str, Any]:
    if data.get("answer") and not data.get("answer_zh"):
        prompt = (
            "Translate the following answer into concise, natural Simplified Chinese. Return strict JSON only with key answer_zh.\n\n"
            + json.dumps({"answer_zh": data["answer"]}, ensure_ascii=False)
        )
        completed = subprocess.run(
            ["codex", "exec", "--model", model, prompt],
            check=True,
            capture_output=True,
            text=True,
        )
        translated = extract_json_from_text(completed.stdout)
        data["answer_zh"] = translated.get("answer_zh", "")
    return data


def build_qa_prompt(
    *, question: str, paper_context: str, chunk_quotes: list[str]
) -> str:
    quotes = "\n\n".join(
        f"Quote {idx + 1}: {quote}" for idx, quote in enumerate(chunk_quotes[:8])
    )
    return (
        "Answer the user's question using only the provided paper context. "
        "Return strict JSON with keys: answer, answer_zh, evidence_quotes, confidence. "
        "Use concise technical English for answer and fluent Simplified Chinese for answer_zh. "
        "evidence_quotes must contain verbatim supporting quotes from the provided context only. "
        "If the evidence is weak, say so in the answer and use low confidence.\n\n"
        f"Question: {question}\n\n"
        f"Context:\n{paper_context}\n\n"
        f"Candidate Evidence Quotes:\n{quotes}"
    )


def answer_from_json(
    data: dict[str, Any], *, raw_response: dict[str, Any]
) -> QuestionAnswer:
    return QuestionAnswer(
        answer=str(data.get("answer") or "").strip(),
        answer_zh=str(data.get("answer_zh") or "").strip(),
        evidence_quotes=[
            trim_quote(str(item).strip())
            for item in data.get("evidence_quotes", [])
            if str(item).strip()
        ],
        confidence=str(data.get("confidence") or "").strip(),
        raw_response=raw_response,
    )


def trim_quote(text: str, max_chars: int = 700) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."
