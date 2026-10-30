"""Thin wrapper around the Gemini API.

- Tries the configured models in order and remembers the first one that
  exists for this API key (Google renames free-tier models often).
- Asks for JSON output that matches a schema; falls back to plain JSON mode
  if a model rejects the schema.
- Turns quota and server errors into LLMUnavailable so the app can answer
  the user politely instead of crashing.
"""

from __future__ import annotations

import json
import logging
import re
import time

from . import config

log = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    """Quota exhausted, no key, or the provider is down."""


class LLMError(Exception):
    """The model answered, but not with usable JSON."""


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise LLMError("model did not return JSON")
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError(f"bad JSON from model: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("model JSON is not an object")
    return data


class GeminiLLM:
    def __init__(self, api_key: str | None = None, models: list[str] | None = None):
        self.api_key = api_key if api_key is not None else config.GEMINI_API_KEY
        self.models = models or config.GEMINI_MODELS
        self.active_model: str | None = None
        self._schema_ok = True
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if not self.api_key:
                raise LLMUnavailable("GEMINI_API_KEY is not set")
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _call(self, model: str, system: str, prompt: str, schema: dict | None, temperature: float):
        from google.genai import types

        cfg = dict(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=1200,
            response_mime_type="application/json",
        )
        if schema is not None and self._schema_ok:
            cfg["response_json_schema"] = schema
        return self.client.models.generate_content(
            model=model, contents=prompt, config=types.GenerateContentConfig(**cfg)
        )

    def generate_json(self, system: str, prompt: str, schema: dict | None = None,
                      temperature: float = 0.1) -> dict:
        from google.genai import errors

        # The model that worked last time first, then the others. On the free
        # tier each model has its own quota, so when one runs out the next
        # one can still answer.
        candidates = [m for m in [self.active_model] + list(self.models) if m]
        candidates = list(dict.fromkeys(candidates))
        last_exc: Exception | None = None
        quota_hit = False
        for model in candidates:
            for attempt in range(2):
                try:
                    resp = self._call(model, system, prompt, schema, temperature)
                    self.active_model = model
                    return parse_json(resp.text)
                except errors.ClientError as exc:
                    code = getattr(exc, "code", 0)
                    msg = str(exc).lower()
                    if code == 429:
                        log.warning("model %s hit its rate limit or quota, trying next", model)
                        quota_hit, last_exc = True, exc
                        break  # next model
                    if code in (401, 403):
                        raise LLMUnavailable("API key rejected") from exc
                    if code == 404 or "not found" in msg or "is not supported" in msg:
                        log.warning("model %s not available, trying next", model)
                        last_exc = exc
                        break  # next model
                    if code == 400 and "schema" in msg and self._schema_ok:
                        log.warning("model rejected JSON schema, using plain JSON mode")
                        self._schema_ok = False
                        continue
                    raise LLMError(str(exc)) from exc
                except errors.ServerError as exc:
                    last_exc = exc
                    time.sleep(1.5 * (attempt + 1))  # transient, retry once
                except LLMError as exc:
                    last_exc = exc
                    if attempt == 0:
                        continue
                    raise
            else:
                continue
        if isinstance(last_exc, LLMError):
            raise last_exc
        if quota_hit:
            raise LLMUnavailable("rate limit or daily quota reached on every model")
        raise LLMUnavailable(f"no Gemini model answered: {last_exc}")


class FakeLLM:
    """Used by the tests: returns queued answers and records every prompt."""

    def __init__(self, responses: list[dict] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []
        self.active_model = "fake"

    def generate_json(self, system, prompt, schema=None, temperature=0.1):
        self.calls.append({"system": system, "prompt": prompt})
        if not self.responses:
            raise LLMUnavailable("no fake response queued")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
