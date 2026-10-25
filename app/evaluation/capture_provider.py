"""Explicit DeepSeek evaluation adapter; constructing it does not send requests."""

from urllib.parse import urlsplit

from openai import AsyncOpenAI


class DeepSeekCaptureProvider:
    evidence_mode = "live_llm"

    def __init__(self, *, model, api_key, base_url, timeout_seconds, http_client=None):
        endpoint = urlsplit(base_url)
        if (
            endpoint.scheme != "https"
            or endpoint.hostname != "api.deepseek.com"
            or endpoint.port not in {None, 443}
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
            or endpoint.path.rstrip("/") not in {"", "/v1"}
            or not model.strip()
            or not api_key.strip()
            or timeout_seconds <= 0
        ):
            raise ValueError("explicit valid DeepSeek evaluation configuration required")
        self.model_version = model
        self.receipts = []
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
            http_client=http_client,
        )

    async def complete(self, messages, max_output_tokens):
        if type(max_output_tokens) is not int or max_output_tokens < 1:
            raise ValueError("positive output token cap required")
        receipt = {
            "attempt": len(self.receipts) + 1,
            "requested_model": self.model_version,
            "max_output_tokens": max_output_tokens,
            "reasoning_effort": "none",
        }
        self.receipts.append(receipt)
        try:
            response = await self.client.chat.completions.create(
                model=self.model_version,
                messages=messages,
                max_tokens=max_output_tokens,
                reasoning_effort="none",
                stream=False,
            )
            receipt.update(
                response_id=response.id,
                response_model=response.model,
                usage=response.usage.model_dump(mode="json") if response.usage else None,
                finish_reason=response.choices[0].finish_reason if response.choices else None,
            )
            if not response.choices or response.choices[0].finish_reason != "stop":
                raise ValueError("incomplete provider output")
            content = response.choices[0].message.content
            if not content or not content.strip():
                raise ValueError("empty provider output")
            return content, response.usage.total_tokens if response.usage else None
        except BaseException as exc:
            receipt.update(
                error_type=type(exc).__name__, http_status=getattr(exc, "status_code", None)
            )
            raise

    async def close(self):
        await self.client.close()
