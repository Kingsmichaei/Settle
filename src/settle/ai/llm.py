"""A minimal structured-output LLM client, with a no-op implementation for rules-only mode."""

from __future__ import annotations

import logging
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

RULES = "rules"


class LLMClient(Protocol):
    @property
    def name(self) -> str:
        """Identifies the model in audit records, e.g. ``gemini:gemini-3.5-flash-lite``."""
        ...

    def structured(
        self, *, system: str, prompt: str, schema: type[M], max_tokens: int = 1024
    ) -> M | None:
        """Ask the model for an answer shaped like ``schema``; ``None`` if it can't deliver."""
        ...


class NoLLM:
    """Rules-only mode: every call returns ``None`` and the agent uses its fallback."""

    @property
    def name(self) -> str:
        return RULES

    def structured(
        self, *, system: str, prompt: str, schema: type[M], max_tokens: int = 1024
    ) -> M | None:
        return None


class GeminiLLM:
    """Google Gemini via the generateContent REST API, in JSON mode with a response schema,
    so the model returns JSON shaped like the Pydantic model. The JSON is still validated
    here; anything off falls back to the agent's rules.

    Docs: https://ai.google.dev/api/generate-content
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com",
        timeout_seconds: float = 45.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
            # A header rather than ?key=, so the key never ends up in access logs.
            headers={"x-goog-api-key": api_key, "content-type": "application/json"},
        )

    @property
    def name(self) -> str:
        return f"gemini:{self._model}"

    def structured(
        self, *, system: str, prompt: str, schema: type[M], max_tokens: int = 1024
    ) -> M | None:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": to_gemini_schema(schema.model_json_schema()),
                "maxOutputTokens": max_tokens,
                "temperature": 0.2,  # recommendations should be consistent, not creative
            },
        }
        try:
            response = self._http.post(f"/v1beta/models/{self._model}:generateContent", json=body)
            response.raise_for_status()
            text = _answer_text(response.json())
            return schema.model_validate_json(text)
        except (httpx.HTTPError, LookupError, ValueError, ValidationError) as exc:
            logger.warning("LLM call failed, using the rule-based fallback: %s", exc)
            return None

    def close(self) -> None:
        self._http.close()


def _answer_text(payload: dict[str, Any]) -> str:
    """The model's text, skipping any "thought" parts. Raises if the answer was blocked."""
    candidates = payload.get("candidates") or []
    if not candidates:
        reason = (payload.get("promptFeedback") or {}).get("blockReason", "no candidates")
        raise ValueError(f"Gemini returned no answer: {reason}")
    parts = candidates[0].get("content", {}).get("parts", [])
    texts = [part["text"] for part in parts if "text" in part and not part.get("thought")]
    if not texts:
        raise ValueError(f"Gemini answer has no text ({candidates[0].get('finishReason')})")
    return "".join(texts)


# The subset of JSON Schema that Gemini's responseSchema (an OpenAPI 3.0 schema) accepts.
# Pydantic adds keywords such as "title" and "additionalProperties"; those are dropped here
# and the full constraints are enforced by Pydantic when the answer comes back.
_SCHEMA_KEYS = frozenset(
    {
        "description",
        "enum",
        "items",
        "properties",
        "required",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
    }
)


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a Pydantic JSON schema to Gemini's schema format (inlining any $refs)."""
    definitions: dict[str, Any] = schema.get("$defs", {})

    def convert(node: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in node:
            return convert(definitions[node["$ref"].rsplit("/", 1)[-1]])
        if "anyOf" in node:  # Optional[X] arrives as anyOf [X, null]
            options = [option for option in node["anyOf"] if option.get("type") != "null"]
            converted = convert(options[0]) if options else {"type": "STRING"}
            return {**converted, "nullable": True}
        out: dict[str, Any] = {}
        if "type" in node:
            out["type"] = str(node["type"]).upper()
        for key in _SCHEMA_KEYS & node.keys():
            value = node[key]
            if key == "properties":
                value = {name: convert(child) for name, child in value.items()}
            elif key == "items":
                value = convert(value)
            out[key] = value
        return out

    return convert(schema)
