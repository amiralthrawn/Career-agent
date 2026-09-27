"""OpenRouter adapter, behind `LLMClient`. NOT wired by default.

`app.api.drafts.get_llm_client` only builds this class when the operator has explicitly set
`LLM_ENABLED=true`, given `OPENROUTER_MODEL` and stored the `openrouter_api_key` secret. Until
then, nothing in this file is ever instantiated, and no test in this repository's automated
suite (`pytest`) ever performs a real network call to OpenRouter: the transport is injected
(`connect`), so unit tests exercise this class's error handling with a fake connection, never a
socket. A separate, manual script (`scripts/manual/openrouter_smoke_test.py`) exists for a human
to run a real call on demand, with synthetic data only.

The API key is read from the existing `SecretStore` (`app.core.secrets`, name `OPENROUTER_API_KEY`)
at call time, never at construction, and never logged, never included in an exception, never
persisted anywhere by this module. No new secret mechanism is introduced, and `OPENROUTER_MODEL`
only ever supplies the DEFAULT model of a request (`GenerationRequest.model`, when given, still
takes precedence): there is no routing, scoring or fallback logic between models here, and none
is added by this step - switching models is an environment change, not a code change.

Uses `http.client` (standard library) rather than `requests`/`httpx`: this project makes no
network call outside step 1's mail sending, and does not add a new HTTP dependency for one call
site kept behind a disabled-by-default switch.
"""

import json
import time
from collections.abc import Callable
from http.client import HTTPSConnection

from app.core.secrets import OPENROUTER_API_KEY, SecretStore, SecretStoreError
from app.integrations.http_transport import HTTPConnection
from app.integrations.llm.ports import (
    GenerationRequest,
    GenerationResult,
    LLMError,
    TokenUsage,
)
from app.models.enums import LLMCallStatus, LLMErrorCode

API_HOST = "openrouter.ai"
API_PATH = "/api/v1/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 30.0


def _default_connect(timeout: float) -> Callable[[], HTTPConnection]:
    return lambda: HTTPSConnection(API_HOST, timeout=timeout)


class OpenRouterClient:
    def __init__(
        self,
        secrets: SecretStore,
        model: str,
        *,
        referer: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        connect: Callable[[], HTTPConnection] | None = None,
    ) -> None:
        self._secrets = secrets
        self._default_model = model
        self._referer = referer  # OpenRouter's optional attribution header; no secret
        # A single explicit timeout covers connect + read: one slow or wedged call cannot hang
        # a draft generation request indefinitely.
        self._connect = connect or _default_connect(timeout)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        api_key = self._resolve_key()
        model = request.model or self._default_model
        started = time.monotonic()
        _, response_bytes = self._call(api_key, model, request)
        duration_ms = round((time.monotonic() - started) * 1000)
        result = _parse(response_bytes, model)
        return GenerationResult(
            text=result.text,
            model=result.model,
            status=result.status,
            usage=result.usage,
            duration_ms=duration_ms,
        )

    def _resolve_key(self) -> str:
        try:
            api_key = self._secrets.get(OPENROUTER_API_KEY)
        except SecretStoreError:
            raise LLMError(LLMErrorCode.UNAVAILABLE) from None
        if not api_key:
            raise LLMError(LLMErrorCode.UNAUTHORIZED)
        return api_key

    def _call(self, api_key: str, model: str, request: GenerationRequest) -> tuple[bytes, bytes]:
        payload = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": json.dumps(dict(request.context))},
                ],
                "max_tokens": request.max_output_tokens,
                "temperature": request.temperature,
            }
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            **({"HTTP-Referer": self._referer} if self._referer else {}),
        }

        connection = self._connect()
        try:
            connection.request("POST", API_PATH, body=payload, headers=headers)
            response = connection.getresponse()
            content = response.read()
        except TimeoutError:
            raise LLMError(LLMErrorCode.TIMEOUT) from None
        except OSError:
            # Any other transport failure: DNS, TLS, connection reset, a malformed HTTP reply.
            raise LLMError(LLMErrorCode.UNAVAILABLE) from None
        finally:
            connection.close()

        _raise_for_status(response.status)
        return payload, content


def _raise_for_status(status: int) -> None:
    if status < 400:
        return
    if status in (401, 403):
        raise LLMError(LLMErrorCode.UNAUTHORIZED)
    if status == 429:
        raise LLMError(LLMErrorCode.RATE_LIMITED)
    if status >= 500:
        raise LLMError(LLMErrorCode.UNAVAILABLE)
    raise LLMError(LLMErrorCode.OTHER)  # 400, 404, or another 4xx: a request-shape problem


def _parse(body: bytes, requested_model: str) -> GenerationResult:
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        raise LLMError(LLMErrorCode.INVALID_RESPONSE) from None
    if not isinstance(data, dict):
        raise LLMError(LLMErrorCode.INVALID_RESPONSE)
    if "error" in data:
        # OpenRouter (notably the `:free` router) can answer HTTP 200 with an error envelope
        # when every free upstream failed: never a fabricated draft, always an explicit LLMError.
        raise LLMError(_error_envelope_code(data["error"]))

    try:
        text = data["choices"][0]["message"]["content"]
        finish_reason = data["choices"][0].get("finish_reason")
        usage_raw = data.get("usage") or {}
    except (KeyError, IndexError, TypeError):
        raise LLMError(LLMErrorCode.INVALID_RESPONSE) from None
    if not isinstance(text, str) or not text.strip():
        raise LLMError(LLMErrorCode.INVALID_RESPONSE)  # no exploitable content
    return GenerationResult(
        text=text,
        model=str(data.get("model") or requested_model),
        status=LLMCallStatus.TRUNCATED if finish_reason == "length" else LLMCallStatus.OK,
        usage=TokenUsage(usage_raw.get("prompt_tokens"), usage_raw.get("completion_tokens")),
    )


def _error_envelope_code(error: object) -> LLMErrorCode:
    code = error.get("code") if isinstance(error, dict) else None
    if isinstance(code, int):
        if code in (401, 403):
            return LLMErrorCode.UNAUTHORIZED
        if code == 429:
            return LLMErrorCode.RATE_LIMITED
        if code >= 500:
            return LLMErrorCode.UNAVAILABLE
    return LLMErrorCode.OTHER
