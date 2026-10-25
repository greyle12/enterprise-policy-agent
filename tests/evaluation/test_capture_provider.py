import asyncio
import json

import httpx
import pytest

from app.evaluation.agent_capture import BudgetedCaptureClient
from app.evaluation.capture_provider import DeepSeekCaptureProvider
from app.evaluation.judge import JudgeBudget


def provider(handler):
    return DeepSeekCaptureProvider(
        model="deepseek-v4-flash",
        api_key="private-fixture-key",
        base_url="https://api.deepseek.com",
        timeout_seconds=1,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def response(content="fixture output", finish="stop", usage=True):
    payload = {
        "id": "fixture-response",
        "object": "chat.completion",
        "created": 0,
        "model": "fixture-response-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": "private-reasoning",
                },
            }
        ],
    }
    if usage:
        payload["usage"] = {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}
    return httpx.Response(200, json=payload)


async def test_sdk_wire_contract_and_receipt_privacy():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        assert request.url.path == "/chat/completions"
        return response()

    p = provider(handler)
    try:
        text, tokens = await p.complete([{"role": "user", "content": "synthetic"}], 256)
    finally:
        await p.close()
    assert text == "fixture output" and tokens == 40
    assert requests[0]["max_tokens"] == 256 and "max_completion_tokens" not in requests[0]
    assert requests[0]["reasoning_effort"] == "none" and requests[0]["stream"] is False
    assert p.receipts[0]["response_model"] == "fixture-response-model"
    assert "private-fixture-key" not in json.dumps(p.receipts)
    assert "private-reasoning" not in json.dumps(p.receipts)


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_http_error_has_no_retry_or_raw_error_in_receipt(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json={"error": {"message": "private-error-body", "type": "fixture"}}
        )

    p = provider(handler)
    try:
        with pytest.raises(Exception):
            await p.complete([{"role": "user", "content": "synthetic"}], 100)
    finally:
        await p.close()
    assert len(calls) == 1 and p.receipts[0]["http_status"] == status
    assert "private-error-body" not in json.dumps(p.receipts)


@pytest.mark.parametrize(
    "content,finish",
    [("partial", "length"), ("", "stop"), (None, "stop"), ("blocked", "content_filter")],
)
async def test_unusable_response_fails_closed(content, finish):
    p = provider(lambda _: response(content, finish))
    try:
        with pytest.raises(ValueError):
            await p.complete([{"role": "user", "content": "synthetic"}], 100)
    finally:
        await p.close()


async def test_missing_usage_stays_unknown():
    p = provider(lambda _: response(usage=False))
    try:
        assert (await p.complete([{"role": "user", "content": "synthetic"}], 100))[1] is None
    finally:
        await p.close()


async def test_timeout_cancels_sdk_and_keeps_budget():
    async def handler(request):
        await asyncio.sleep(10)
        return response()

    p = provider(handler)
    c = BudgetedCaptureClient(
        p,
        JudgeBudget(
            token_limit=10000,
            cost_limit=1,
            max_output_tokens=100,
            max_usd_per_million=1,
            timeout_seconds=0.01,
            max_calls=1,
        ),
    )
    try:
        with pytest.raises(TimeoutError):
            await c.chat([{"role": "user", "content": "synthetic"}])
    finally:
        await p.close()
    assert c.calls == 1 and c.reserved_tokens > 0
    assert len(p.receipts) == 1 and p.receipts[0]["error_type"] == "CancelledError"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.deepseek.com",
        "https://example.org",
        "https://user:secret@api.deepseek.com",
        "https://api.deepseek.com:1234",
        "https://api.deepseek.com/evil",
    ],
)
def test_invalid_endpoint_rejected_before_client(url):
    with pytest.raises(ValueError):
        DeepSeekCaptureProvider(model="fixture", api_key="fixture", base_url=url, timeout_seconds=1)
