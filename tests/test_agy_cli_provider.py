import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from astrbot.core.astr_main_agent_resources import TOOL_CALL_PROMPT
from astrbot.core.config.default import CONFIG_METADATA_2
from astrbot.core.provider.agy_cli_manager import AgyCLIManager
from astrbot.core.provider.sources.agy_cli_source import (
    AGY_NATIVE_TOOL_NOTE,
    ProviderAgyCLI,
)


class _FakeStream:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def read(self, size: int) -> bytes:
        payload, self.payload = self.payload, b""
        return payload


class _FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, return_code: int = 0) -> None:
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)
        self.returncode = None
        self.return_code = return_code
        self.killed = False

    async def wait(self) -> int:
        self.returncode = self.return_code
        return self.return_code

    def kill(self) -> None:
        self.killed = True
        self.returncode = self.return_code


def _provider(tmp_path: Path, **overrides) -> ProviderAgyCLI:
    config = {
        "id": "agy-test",
        "type": "agy_cli_chat_completion",
        "model": "gemini-3.5-flash",
        "agy_command": "agy-test-command",
        "agy_working_directory": str(tmp_path),
        "timeout": 10,
    }
    config.update(overrides)
    provider = ProviderAgyCLI(config, {})
    provider.cli_manager = AgyCLIManager(tmp_path / "data")
    return provider


@pytest.mark.asyncio
async def test_formats_context_and_filters_astrbot_tooling(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    contexts = [
        {
            "role": "system",
            "content": (
                "# Persona Instructions\n\nBe concise.\n\n"
                f"{TOOL_CALL_PROMPT}\n\n"
                "## Skills\n\n- AstrBot-only skill\n\n"
                "## Response Style\n\nUse plain text."
            ),
        },
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
        {"role": "tool", "name": "lookup", "content": "result"},
    ]

    async with AsyncExitStack() as stack:
        prompt, image_paths = await provider._format_prompt(contexts, stack)

    assert AGY_NATIVE_TOOL_NOTE in prompt
    assert "Be concise." in prompt
    assert "AstrBot-only skill" not in prompt
    assert TOOL_CALL_PROMPT not in prompt
    assert "Use plain text." in prompt
    assert "User:\nHello" in prompt
    assert "Assistant:\nHi" in prompt
    assert "Tool result (lookup):\nresult" in prompt
    assert image_paths == []


@pytest.mark.parametrize(
    ("model", "thinking", "expected"),
    [
        ("gemini-3.5-flash", "adaptive", "Gemini 3.5 Flash (Medium)"),
        ("gemini-3.5-flash", "medium", "Gemini 3.5 Flash (Medium)"),
        ("gemini-3.1-pro", "high", "Gemini 3.1 Pro (High)"),
        ("gemini-3.1-pro", "minimal", "Gemini 3.1 Pro (Low)"),
        ("custom-model", "high", "custom-model"),
    ],
)
def test_maps_thinking_levels(
    tmp_path: Path, model: str, thinking: str, expected: str
) -> None:
    provider = _provider(tmp_path, agy_thinking_level=thinking)

    assert provider._resolve_model(model) == expected


@pytest.mark.asyncio
async def test_runs_agy_without_shell_and_returns_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _provider(
        tmp_path,
        agy_thinking_level="high",
        agy_dangerously_skip_permissions=True,
        proxy="http://127.0.0.1:7897",
    )
    process = _FakeProcess(b"\x1b[32mAGY_OK\x1b[0m\n", b"")
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    response = await provider.text_chat(
        contexts=[
            {"role": "system", "content": "Answer exactly."},
            {"role": "user", "content": "Say AGY_OK"},
        ]
    )

    assert response.completion_text == "AGY_OK"
    assert response.usage is not None
    assert response.usage.input > 0
    args = spawn.await_args.args
    kwargs = spawn.await_args.kwargs
    assert args[:4] == (
        "agy-test-command",
        "--dangerously-skip-permissions",
        "--model",
        "Gemini 3.5 Flash (High)",
    )
    assert "--print-timeout" in args
    assert "--print" in args
    assert kwargs["cwd"] == str(tmp_path.resolve())
    assert kwargs["env"]["HTTPS_PROXY"] == "http://127.0.0.1:7897"


@pytest.mark.asyncio
async def test_retries_with_legacy_model_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _provider(tmp_path, agy_thinking_level="high")
    spawn = AsyncMock(
        side_effect=[
            _FakeProcess(b"", b'Error: invalid --model "Gemini 3.5 Flash (High)"', 1),
            _FakeProcess(b"LEGACY_OK", b""),
        ]
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    response = await provider.text_chat(prompt="hello")

    assert response.completion_text == "LEGACY_OK"
    assert spawn.await_count == 2
    assert spawn.await_args_list[1].args[:3] == (
        "agy-test-command",
        "--model",
        "gemini-3.5-flash-high",
    )


@pytest.mark.asyncio
async def test_kills_agy_when_output_limit_is_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _provider(tmp_path, agy_max_output_bytes=3)
    process = _FakeProcess(b"four", b"")
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )

    with pytest.raises(RuntimeError, match="output exceeded"):
        await provider.text_chat(prompt="hello")

    assert process.killed is True


@pytest.mark.asyncio
async def test_permission_denial_is_reported_as_provider_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _provider(tmp_path)
    process = _FakeProcess(
        b'jetski: no output produced - tool required the "write_file" permission',
        b"",
    )
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )

    with pytest.raises(RuntimeError, match="no output produced"):
        await provider.text_chat(prompt="write a file")


def test_default_provider_template_exposes_agy_cli() -> None:
    providers = CONFIG_METADATA_2["provider_group"]["metadata"]["provider"][
        "config_template"
    ]

    assert providers["Agy CLI"] == {
        "id": "agy",
        "provider": "agy",
        "type": "agy_cli_chat_completion",
        "provider_type": "chat_completion",
        "enable": True,
        "model": "gemini-3.5-flash",
        "modalities": ["text", "image"],
        "agy_command": "agy",
        "agy_working_directory": "",
        "agy_print_timeout": "10m",
        "agy_thinking_level": "adaptive",
        "agy_system_prompt_mode": "filtered",
        "agy_dangerously_skip_permissions": False,
        "agy_max_output_bytes": 10485760,
        "agy_env": {},
        "timeout": 600,
        "proxy": "",
    }
