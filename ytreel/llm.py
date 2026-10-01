"""LLM 後端。

三種：
- api    : Anthropic Messages API（需要 ANTHROPIC_API_KEY 或 `ant auth login`）
- cli    : 本機的 Claude Code CLI（`claude -p`），不用自己備 API key
- none   : 不叫模型，只把逐字稿與提示詞寫成檔案，讓你貼到任何模型裡

api 後端用結構化輸出（output_format）直接拿到驗證過的物件；cli 後端把 JSON
Schema 放進提示詞，再自己解析與驗證。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

DEFAULT_API_MODEL = "claude-opus-5-5"
DEFAULT_CLI_MODEL = "opus"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMBackend(Protocol):
    name: str

    def complete_text(
        self, *, system: str, prompt: str, max_tokens: int, effort: str = "medium"
    ) -> str: ...

    def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        output_format: type[T],
        max_tokens: int,
        effort: str = "high",
        cache: bool = False,
    ) -> T: ...


# --------------------------------------------------------------------------
# Anthropic API
# --------------------------------------------------------------------------

class AnthropicBackend:
    name = "api"

    def __init__(
        self,
        model: str = DEFAULT_API_MODEL,
        *,
        use_fallbacks: bool = True,
        timeout: float = 1800.0,
        client=None,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("找不到 anthropic 套件，請執行：pip install anthropic") from exc
        self._anthropic = anthropic
        self.model = model
        self.timeout = timeout
        # 伺服器端 refusal fallback：安全分類器擋下請求時，同一次呼叫會自動
        # 改用備援模型重跑，不會整支中斷。帳號沒開這個 beta 就自動退回。
        self.use_fallbacks = use_fallbacks
        if client is not None:
            self.client = client
            return
        try:
            self.client = anthropic.Anthropic(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - 多半是沒有憑證
            raise LLMError(_auth_hint(exc)) from exc

    # -- 內部：帶 fallback 的呼叫，失敗則退回一般路徑 -------------------
    def _call(self, fn_beta, fn_plain, kwargs: dict[str, Any]):
        if self.use_fallbacks:
            try:
                return fn_beta(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
            except self._anthropic.BadRequestError as exc:
                msg = str(exc).lower()
                if not any(k in msg for k in ("beta", "fallback", "unsupported")):
                    raise LLMError(_api_hint(exc)) from exc
                self.use_fallbacks = False  # 這個帳號沒開，之後都不用再試
            except self._anthropic.APIError as exc:
                raise LLMError(_api_hint(exc)) from exc
        try:
            return fn_plain(**kwargs)
        except self._anthropic.APIError as exc:
            raise LLMError(_api_hint(exc)) from exc

    def complete_text(
        self, *, system: str, prompt: str, max_tokens: int, effort: str = "medium"
    ) -> str:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort},
        )
        resp = self._call(
            self.client.beta.messages.create, self.client.messages.create, kwargs
        )
        _check_stop(resp)
        text = "".join(b.text for b in resp.content if b.type == "text")
        if not text.strip():
            raise LLMError("模型回了空內容")
        return text

    def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        output_format: type[T],
        max_tokens: int,
        effort: str = "high",
        cache: bool = False,
    ) -> T:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=output_format,
            output_config={"effort": effort},
            # 判斷密度高的任務，讓模型自己決定要想多久
            thinking={"type": "adaptive"},
        )
        if cache:
            # 逐字稿很長，快取起來讓重跑/第二次呼叫省錢
            kwargs["cache_control"] = {"type": "ephemeral"}
        resp = self._call(
            self.client.beta.messages.parse, self.client.messages.parse, kwargs
        )
        _check_stop(resp)
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            text = "".join(b.text for b in resp.content if b.type == "text")
            return _loads_validated(text, output_format)
        return parsed


def _check_stop(resp) -> None:
    reason = getattr(resp, "stop_reason", None)
    if reason == "refusal":
        details = getattr(resp, "stop_details", None)
        cat = getattr(details, "category", None) or "未指明"
        raise LLMError(
            f"模型的安全分類器拒絕了這個請求（類別：{cat}）。"
            "如果影片內容本身敏感，可以改用 --llm none 自行處理，或換一支影片。"
        )
    if reason == "max_tokens":
        raise LLMError(
            "輸出被 max_tokens 截斷。分析階段請加大 --max-output-tokens 或減少 --reels；"
            "校對階段請調小 --chunk-chars。"
        )


def _auth_hint(exc: Exception) -> str:
    return (
        f"無法建立 Anthropic client：{exc}\n"
        "請設定憑證其中之一：\n"
        "  export ANTHROPIC_API_KEY=sk-ant-...\n"
        "  ant auth login            # 會存成 profile，SDK 自動讀取\n"
        "或改用本機 Claude Code：--llm cli"
    )


def _api_hint(exc: Exception) -> str:
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError):
        return f"API 憑證無效或過期：{exc}\n請確認 ANTHROPIC_API_KEY，或執行 ant auth login。"
    if isinstance(exc, anthropic.NotFoundError):
        return f"找不到這個 model：{exc}\n請用 --model 指定一個你的帳號可用的模型。"
    if isinstance(exc, anthropic.RateLimitError):
        return f"觸發速率限制：{exc}\n稍等再試，或改用較小的 --chunk-chars。"
    if isinstance(exc, anthropic.APIConnectionError):
        return (
            f"連不到 api.anthropic.com：{exc}\n"
            "請檢查網路或 proxy 設定（HTTPS_PROXY）。"
        )
    if isinstance(exc, anthropic.APIStatusError):
        return f"API 回傳錯誤 {exc.status_code}：{exc}"
    return str(exc)


# --------------------------------------------------------------------------
# Claude Code CLI
# --------------------------------------------------------------------------

class ClaudeCliBackend:
    """用本機的 `claude -p`，沿用 Claude Code 已有的登入。"""

    name = "cli"

    def __init__(self, model: str = DEFAULT_CLI_MODEL, timeout: float = 1800.0) -> None:
        exe = shutil.which("claude")
        if not exe:
            raise LLMError(
                "找不到 claude CLI。請安裝 Claude Code，或改用 --llm api。"
            )
        self.exe = exe
        self.model = model
        self.timeout = timeout

    def _run(self, system: str, prompt: str) -> str:
        args = [
            self.exe,
            "-p",
            "--output-format", "json",
            "--system-prompt", system,
            "--restricted",          # 這是純文字任務，不需要能跑指令的工具
        ]
        if self.model:
            args += ["--model", self.model]
        try:
            proc = subprocess.run(
                args,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude CLI 逾時（{self.timeout:.0f} 秒）") from exc
        if proc.returncode != 0:
            detail = ((proc.stderr or "") + " " + (proc.stdout or "")).strip()[:600]
            raise LLMError(
                f"claude CLI 失敗（exit {proc.returncode}）：{detail or '沒有錯誤訊息'}\n"
                "可改用 --llm api 搭配 ANTHROPIC_API_KEY。"
            )
        return _cli_result_text(proc.stdout)

    def complete_text(
        self, *, system: str, prompt: str, max_tokens: int, effort: str = "medium"
    ) -> str:
        return self._run(system, prompt)

    def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        output_format: type[T],
        max_tokens: int,
        effort: str = "high",
        cache: bool = False,
    ) -> T:
        schema = json.dumps(output_format.model_json_schema(), ensure_ascii=False, indent=2)
        sys_json = (
            system
            + "\n\n輸出格式：只輸出一個符合下列 JSON Schema 的 JSON 物件。"
            "不要加 Markdown 程式碼框，不要加任何說明文字。\n\n"
            + schema
        )
        raw = self._run(sys_json, prompt)
        return _loads_validated(raw, output_format)


def _cli_result_text(stdout: str) -> str:
    """從 `claude -p --output-format json` 的輸出取出 result 字串。"""
    s = (stdout or "").strip()
    if not s:
        raise LLMError("claude CLI 沒有輸出")
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return s  # 不是 JSON 就當成純文字
    if isinstance(data, dict):
        if data.get("is_error"):
            raise LLMError(f"claude CLI 回報錯誤：{str(data.get('result'))[:500]}")
        for key in ("result", "text", "content"):
            if isinstance(data.get(key), str):
                return data[key]
    return s


# --------------------------------------------------------------------------
# 不呼叫模型
# --------------------------------------------------------------------------

class NoLLMBackend:
    """只產出提示詞包，不呼叫任何模型。"""

    name = "none"

    class Skipped(RuntimeError):
        def __init__(self, system: str, prompt: str, schema: dict | None = None):
            super().__init__("已跳過模型呼叫")
            self.system = system
            self.prompt = prompt
            self.schema = schema

    def complete_text(self, *, system: str, prompt: str, max_tokens: int, effort: str = "medium") -> str:
        raise self.Skipped(system, prompt)

    def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        output_format: type[T],
        max_tokens: int,
        effort: str = "high",
        cache: bool = False,
    ) -> T:
        raise self.Skipped(system, prompt, output_format.model_json_schema())


# --------------------------------------------------------------------------
# 共用
# --------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(raw: str) -> str:
    s = (raw or "").strip()
    if m := _FENCE.search(s):
        s = m.group(1).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end <= start:
        raise LLMError(f"模型回覆裡找不到 JSON：{s[:300]}")
    return s[start : end + 1]


def _loads_validated(raw: str, output_format: type[T]) -> T:
    payload = _extract_json(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LLMError(f"模型回的 JSON 無法解析：{exc}；開頭為 {payload[:200]}") from exc
    try:
        return output_format.model_validate(data)
    except ValidationError as exc:
        raise LLMError(f"模型回的 JSON 不符結構：{exc}") from exc


def build_backend(
    kind: str,
    model: str | None = None,
    *,
    use_fallbacks: bool = True,
    timeout: float = 1800.0,
) -> LLMBackend:
    kind = (kind or "api").lower()
    if kind == "api":
        return AnthropicBackend(
            model or DEFAULT_API_MODEL, use_fallbacks=use_fallbacks, timeout=timeout
        )
    if kind == "cli":
        return ClaudeCliBackend(model or DEFAULT_CLI_MODEL, timeout=timeout)
    if kind == "none":
        return NoLLMBackend()
    raise LLMError(f"未知的 --llm 值：{kind}（可用 api / cli / none）")


def write_prompt_pack(outdir: Path, skipped: "NoLLMBackend.Skipped") -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written = [outdir / "prompt_system.txt", outdir / "prompt_user.md"]
    written[0].write_text(skipped.system, encoding="utf-8")
    written[1].write_text(skipped.prompt, encoding="utf-8")
    if skipped.schema:
        p = outdir / "prompt_schema.json"
        p.write_text(json.dumps(skipped.schema, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(p)
    return written
