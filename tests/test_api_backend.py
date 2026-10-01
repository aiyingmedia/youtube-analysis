"""API 後端的請求組裝與錯誤處理（不連網，用假 client）。"""

from types import SimpleNamespace

import anthropic
import pytest
from pydantic import BaseModel

from ytreel.llm import FALLBACK_BETA, AnthropicBackend, LLMError


class Tiny(BaseModel):
    name: str


def _resp(stop_reason="end_turn", text='{"name": "甲"}', parsed=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=None,
        content=[SimpleNamespace(type="text", text=text)],
        parsed_output=parsed,
    )


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response = response or _resp()
        self.error = error
        self.calls = []

    def _record(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            err, self.error = self.error, None  # 只失敗第一次
            raise err
        return self.response

    create = parse = _record


class FakeClient:
    def __init__(self, beta=None, plain=None):
        self.beta = SimpleNamespace(messages=beta or FakeMessages())
        self.messages = plain or FakeMessages()


def _backend(client, **kw):
    return AnthropicBackend("claude-opus-5-5", client=client, **kw)


def _bad_request(msg):
    return anthropic.BadRequestError(
        msg,
        response=SimpleNamespace(status_code=400, headers={}, request=None),
        body=None,
    )


# --- 請求參數 -----------------------------------------------------------

def test_json_call_sends_structured_output_and_adaptive_thinking():
    client = FakeClient()
    got = _backend(client).complete_json(
        system="SYS", prompt="USER", output_format=Tiny, max_tokens=1234
    )
    assert got.name == "甲"
    kwargs = client.beta.messages.calls[0]
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_format"] is Tiny
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["max_tokens"] == 1234
    assert kwargs["system"] == "SYS"


def test_refusal_fallback_is_enabled_by_default():
    client = FakeClient()
    _backend(client).complete_json(
        system="s", prompt="p", output_format=Tiny, max_tokens=10
    )
    kwargs = client.beta.messages.calls[0]
    assert kwargs["betas"] == [FALLBACK_BETA]
    assert kwargs["fallbacks"] == "default"


def test_fallback_can_be_turned_off():
    client = FakeClient()
    _backend(client, use_fallbacks=False).complete_json(
        system="s", prompt="p", output_format=Tiny, max_tokens=10
    )
    assert client.beta.messages.calls == []
    assert "fallbacks" not in client.messages.calls[0]


def test_cache_control_only_when_requested():
    client = FakeClient()
    b = _backend(client)
    b.complete_json(system="s", prompt="p", output_format=Tiny, max_tokens=10, cache=True)
    assert client.beta.messages.calls[0]["cache_control"] == {"type": "ephemeral"}
    b.complete_json(system="s", prompt="p", output_format=Tiny, max_tokens=10, cache=False)
    assert "cache_control" not in client.beta.messages.calls[1]


def test_text_call_uses_requested_effort_and_no_thinking():
    client = FakeClient()
    _backend(client).complete_text(
        system="s", prompt="p", max_tokens=100, effort="low"
    )
    kwargs = client.beta.messages.calls[0]
    assert kwargs["output_config"] == {"effort": "low"}
    assert "thinking" not in kwargs


def test_prefers_parsed_output_when_sdk_supplies_it():
    client = FakeClient(beta=FakeMessages(response=_resp(parsed=Tiny(name="乙"))))
    got = _backend(client).complete_json(
        system="s", prompt="p", output_format=Tiny, max_tokens=10
    )
    assert got.name == "乙"


# --- beta 不可用時的降級 -------------------------------------------------

def test_degrades_to_plain_path_when_beta_unsupported():
    client = FakeClient(beta=FakeMessages(error=_bad_request("unsupported beta header")))
    backend = _backend(client)
    got = backend.complete_json(system="s", prompt="p", output_format=Tiny, max_tokens=10)
    assert got.name == "甲"
    assert len(client.messages.calls) == 1
    assert backend.use_fallbacks is False  # 記住了，之後不再重試


def test_degradation_is_remembered_across_calls():
    client = FakeClient(beta=FakeMessages(error=_bad_request("fallbacks not enabled")))
    backend = _backend(client)
    for _ in range(3):
        backend.complete_json(system="s", prompt="p", output_format=Tiny, max_tokens=10)
    assert len(client.beta.messages.calls) == 1
    assert len(client.messages.calls) == 3


def test_unrelated_bad_request_is_not_swallowed():
    client = FakeClient(beta=FakeMessages(error=_bad_request("max_tokens is too large")))
    with pytest.raises(LLMError, match="400"):
        _backend(client).complete_json(
            system="s", prompt="p", output_format=Tiny, max_tokens=10
        )
    assert client.messages.calls == []


# --- stop_reason 處理 ----------------------------------------------------

def test_refusal_is_reported_clearly():
    client = FakeClient(beta=FakeMessages(response=_resp(stop_reason="refusal")))
    with pytest.raises(LLMError, match="安全分類器"):
        _backend(client).complete_json(
            system="s", prompt="p", output_format=Tiny, max_tokens=10
        )


def test_truncation_suggests_raising_max_tokens():
    client = FakeClient(beta=FakeMessages(response=_resp(stop_reason="max_tokens")))
    with pytest.raises(LLMError, match="max_tokens 截斷"):
        _backend(client).complete_json(
            system="s", prompt="p", output_format=Tiny, max_tokens=10
        )


def test_empty_text_response_is_an_error():
    client = FakeClient(beta=FakeMessages(response=_resp(text="   ")))
    with pytest.raises(LLMError, match="空內容"):
        _backend(client).complete_text(system="s", prompt="p", max_tokens=10)
