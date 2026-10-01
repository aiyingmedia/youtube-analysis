import json

import pytest
from pydantic import BaseModel

from ytreel.llm import (
    LLMError,
    NoLLMBackend,
    _cli_result_text,
    _extract_json,
    _loads_validated,
    build_backend,
    write_prompt_pack,
)


class Tiny(BaseModel):
    name: str
    count: int


def test_extract_json_from_fenced_block():
    assert _extract_json('說明\n```json\n{"a": 1}\n```\n尾巴') == '{"a": 1}'


def test_extract_json_from_bare_object():
    assert _extract_json('  {"a": 1}  ') == '{"a": 1}'


def test_extract_json_ignores_prose_around_object():
    assert _extract_json('好的，這是結果：{"a": 1} 希望有幫助') == '{"a": 1}'


def test_extract_json_raises_when_absent():
    with pytest.raises(LLMError):
        _extract_json("完全沒有 JSON")


def test_loads_validated_returns_model():
    got = _loads_validated('{"name": "甲", "count": 3}', Tiny)
    assert got.name == "甲" and got.count == 3


def test_loads_validated_rejects_wrong_shape():
    with pytest.raises(LLMError, match="不符結構"):
        _loads_validated('{"name": "甲"}', Tiny)


def test_loads_validated_rejects_broken_json():
    with pytest.raises(LLMError, match="無法解析"):
        _loads_validated('{"name": "甲", "count": }', Tiny)


def test_loads_validated_rejects_unterminated_object():
    with pytest.raises(LLMError, match="找不到 JSON"):
        _loads_validated('{"name": ', Tiny)


def test_cli_result_text_unwraps_result_field():
    assert _cli_result_text(json.dumps({"result": "答案", "is_error": False})) == "答案"


def test_cli_result_text_raises_on_error_flag():
    with pytest.raises(LLMError, match="回報錯誤"):
        _cli_result_text(json.dumps({"result": "boom", "is_error": True}))


def test_cli_result_text_accepts_plain_text():
    assert _cli_result_text("就是純文字") == "就是純文字"


def test_cli_result_text_rejects_empty():
    with pytest.raises(LLMError):
        _cli_result_text("   ")


def test_none_backend_raises_skipped_carrying_prompt():
    backend = build_backend("none")
    with pytest.raises(NoLLMBackend.Skipped) as exc:
        backend.complete_json(
            system="SYS", prompt="USER", output_format=Tiny, max_tokens=10
        )
    assert exc.value.system == "SYS" and exc.value.prompt == "USER"
    assert exc.value.schema["properties"]["count"]["type"] == "integer"


def test_write_prompt_pack(tmp_path):
    skipped = NoLLMBackend.Skipped("SYS", "USER", {"type": "object"})
    paths = write_prompt_pack(tmp_path, skipped)
    assert {p.name for p in paths} == {
        "prompt_system.txt", "prompt_user.md", "prompt_schema.json"
    }
    assert (tmp_path / "prompt_system.txt").read_text(encoding="utf-8") == "SYS"
    assert json.loads((tmp_path / "prompt_schema.json").read_text(encoding="utf-8"))


def test_unknown_backend_rejected():
    with pytest.raises(LLMError, match="未知的"):
        build_backend("gpt")
