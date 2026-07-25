import asyncio
import copy
import json
import os
import re
import shutil
import subprocess
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Literal
from uuid import uuid4

import jsonschema

from astrbot import logger
from astrbot.api.provider import Provider
from astrbot.core.agent.message import ContentPart, Message
from astrbot.core.agent.tool import ToolSet
from astrbot.core.astr_main_agent_resources import (
    TOOL_CALL_PROMPT,
    TOOL_CALL_PROMPT_SKILLS_LIKE_MODE,
)
from astrbot.core.exceptions import EmptyModelOutputError
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import LLMResponse, TokenUsage, ToolCallsResult
from astrbot.core.utils.astrbot_path import get_astrbot_workspaces_path
from astrbot.core.utils.media_utils import MediaResolver
from astrbot.core.workspace import normalize_umo_for_workspace

from ..agy_cli_manager import AgyCLIManager
from ..register import register_provider_adapter

AGY_DEFAULT_MODEL = "gemini-3.5-flash"
AGY_GEMINI_FLASH_MODEL = "gemini-3.5-flash"
AGY_GEMINI_PRO_MODEL = "gemini-3.1-pro"
AGY_MODELS = [AGY_GEMINI_FLASH_MODEL, AGY_GEMINI_PRO_MODEL]
AGY_NATIVE_TOOL_NOTE = (
    "You are running inside agy CLI via AstrBot. Use agy's native tools when "
    "needed. Emit an AstrBot host-tool envelope only when the current prompt "
    "explicitly provides the allowlisted protocol."
)
AGY_AGENT_NAME = "astrbot"
AGY_DEFAULT_HOST_TOOL_ALLOWLIST = (
    "render_code_to_image",
    "render_file_to_image",
    "render_math",
    "send_image",
)
AGY_SYSTEM_PROMPT_MAX_CHARS = 24_000
AGY_DEFAULT_TIMEOUT_SECONDS = 600
AGY_DEFAULT_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
AGY_MODEL_DISCOVERY_TIMEOUT_SECONDS = 15
AGY_MODEL_DISCOVERY_MAX_OUTPUT_BYTES = 1024 * 1024

_AGY_MODEL_LABELS = {
    "gemini-3.5-flash-low": "Gemini 3.5 Flash (Low)",
    "gemini-3.5-flash-medium": "Gemini 3.5 Flash (Medium)",
    "gemini-3.5-flash-high": "Gemini 3.5 Flash (High)",
    "gemini-3.1-pro-low": "Gemini 3.1 Pro (Low)",
    "gemini-3.1-pro-high": "Gemini 3.1 Pro (High)",
}
_AGY_MODEL_IDS_BY_LABEL = {label: model for model, label in _AGY_MODEL_LABELS.items()}

_ANSI_ESCAPE_RE = re.compile(r"\x1B\][\s\S]*?(?:\x07|\x1B\\)|\x1B\[[0-?]*[ -/]*[@-~]")
_AGY_HOST_TOOL_CALL_RE = re.compile(
    r"\A<astrbot-tool-call>\s*(\{[\s\S]*\})\s*</astrbot-tool-call>\Z"
)


class _AgyOutputLimitError(RuntimeError):
    pass


@register_provider_adapter(
    "agy_cli_chat_completion",
    "Agy CLI Provider Adapter",
    provider_display_name="Agy CLI",
)
class ProviderAgyCLI(Provider):
    """Run AstrBot chat requests through the locally authenticated agy CLI."""

    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        self.cli_manager = AgyCLIManager()
        self.model_name = str(provider_config.get("model") or AGY_DEFAULT_MODEL)
        self.command = self.cli_manager.resolve_command(
            str(provider_config.get("agy_command") or "agy").strip()
        )
        self.print_timeout = str(
            provider_config.get("agy_print_timeout") or "10m"
        ).strip()
        self.system_prompt_mode = str(
            provider_config.get("agy_system_prompt_mode") or "filtered"
        ).strip()
        self.thinking_level = str(
            provider_config.get("agy_thinking_level") or "adaptive"
        ).strip()
        self.dangerously_skip_permissions = bool(
            provider_config.get("agy_dangerously_skip_permissions", False)
        )
        self.sandbox_enabled = bool(provider_config.get("agy_sandbox", True))
        self.isolate_workspaces = bool(
            provider_config.get("agy_isolate_workspaces", True)
        )
        configured_allowlist = provider_config.get(
            "agy_host_tool_allowlist", list(AGY_DEFAULT_HOST_TOOL_ALLOWLIST)
        )
        if not isinstance(configured_allowlist, list) or any(
            not isinstance(name, str) or not name.strip()
            for name in configured_allowlist
        ):
            raise ValueError("agy_host_tool_allowlist must be a list of tool names")
        self.host_tool_allowlist = frozenset(
            name.strip() for name in configured_allowlist
        )
        if self.system_prompt_mode not in {"filtered", "full", "none"}:
            raise ValueError(
                "agy_system_prompt_mode must be one of: filtered, full, none"
            )
        if self.thinking_level not in {
            "adaptive",
            "off",
            "minimal",
            "low",
            "medium",
            "high",
        }:
            raise ValueError(
                "agy_thinking_level must be one of: adaptive, off, minimal, "
                "low, medium, high"
            )

        try:
            self.timeout = float(
                provider_config.get("timeout", AGY_DEFAULT_TIMEOUT_SECONDS)
            )
            self.max_output_bytes = int(
                provider_config.get(
                    "agy_max_output_bytes", AGY_DEFAULT_MAX_OUTPUT_BYTES
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("agy timeout and output limit must be numeric") from exc
        if self.timeout <= 0 or self.max_output_bytes <= 0:
            raise ValueError("agy timeout and output limit must be positive")

        configured_cwd = str(provider_config.get("agy_working_directory") or "")
        self.cwd = (
            Path(configured_cwd).expanduser()
            if configured_cwd
            else Path(get_astrbot_workspaces_path())
        )
        self.cwd = self.cwd.resolve()
        if not configured_cwd:
            self.cwd.mkdir(parents=True, exist_ok=True)
        if not self.cwd.is_dir():
            raise ValueError(f"agy working directory does not exist: {self.cwd}")

        configured_env = provider_config.get("agy_env", {}) or {}
        if not isinstance(configured_env, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in configured_env.items()
        ):
            raise ValueError("agy_env must be an object containing string values")
        self.env = dict(configured_env)
        self.proxy = str(provider_config.get("proxy") or "").strip()
        self._run_lock = asyncio.Lock()

    def _resolve_working_directory(self, session_id: str | None) -> Path:
        """Resolve and create the workspace used for one Agy request.

        Args:
            session_id: AstrBot unified message origin.

        Returns:
            The provider root or a normalized per-session child directory.
        """
        if not self.isolate_workspaces:
            return self.cwd
        workspace = (
            self.cwd / normalize_umo_for_workspace(str(session_id or "unknown"))
        ).resolve()
        workspace.relative_to(self.cwd)
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def get_current_key(self) -> str:
        return "agy-cli"

    def get_keys(self) -> list[str]:
        return ["agy-cli"]

    def set_key(self, key: str) -> None:
        return None

    async def get_models(self) -> list[str]:
        async with self._run_lock:
            subprocess_kwargs: dict = {}
            if os.name == "nt":
                subprocess_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            process = await asyncio.create_subprocess_exec(
                self.command,
                "models",
                cwd=str(self.cwd),
                env=self.cli_manager.build_environment(
                    proxy=self.proxy,
                    extra=self.env,
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **subprocess_kwargs,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=min(self.timeout, AGY_MODEL_DISCOVERY_TIMEOUT_SECONDS),
                )
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise RuntimeError("agy model discovery timed out") from exc

        if len(stdout) + len(stderr) > AGY_MODEL_DISCOVERY_MAX_OUTPUT_BYTES:
            raise RuntimeError("agy model discovery output exceeded 1 MiB")
        if process.returncode != 0:
            detail = (stderr or stdout).decode("utf-8", "replace").strip()
            raise RuntimeError(
                f"agy models exited with code {process.returncode}"
                + (f": {detail[:4096]}" if detail else "")
            )

        output = _ANSI_ESCAPE_RE.sub("", stdout.decode("utf-8", "replace"))
        models = list(
            dict.fromkeys(line.strip() for line in output.splitlines() if line.strip())
        )
        if not models:
            raise RuntimeError("agy models returned no available models")
        return models

    def _resolve_model(self, model: str | None) -> str:
        """Map AstrBot model settings to names accepted by current agy releases."""
        model_id = str(model or self.get_model() or AGY_DEFAULT_MODEL).strip()
        if model_id in _AGY_MODEL_IDS_BY_LABEL:
            return model_id
        if model_id in _AGY_MODEL_LABELS:
            return _AGY_MODEL_LABELS[model_id]

        level = self.thinking_level
        if model_id == AGY_GEMINI_PRO_MODEL:
            if level in {"medium", "high"}:
                return _AGY_MODEL_LABELS[f"{model_id}-high"]
            return _AGY_MODEL_LABELS[f"{model_id}-low"]
        elif model_id == AGY_GEMINI_FLASH_MODEL:
            if level in {"off", "minimal", "low"}:
                return _AGY_MODEL_LABELS[f"{model_id}-low"]
            if level == "high":
                return _AGY_MODEL_LABELS[f"{model_id}-high"]
            return _AGY_MODEL_LABELS[f"{model_id}-medium"]
        return model_id

    def _resolve_legacy_model(self, model: str | None) -> str:
        """Map a model to identifiers accepted by older agy releases."""
        model_id = str(model or self.get_model() or AGY_DEFAULT_MODEL).strip()
        if model_id in _AGY_MODEL_IDS_BY_LABEL:
            return _AGY_MODEL_IDS_BY_LABEL[model_id]
        if model_id in _AGY_MODEL_LABELS:
            return model_id
        if model_id == AGY_GEMINI_PRO_MODEL:
            if self.thinking_level in {"medium", "high"}:
                return f"{model_id}-high"
            if self.thinking_level != "adaptive":
                return f"{model_id}-low"
        elif model_id == AGY_GEMINI_FLASH_MODEL:
            if self.thinking_level in {"off", "minimal"}:
                return f"{model_id}-minimal"
            if self.thinking_level in {"low", "medium", "high"}:
                return f"{model_id}-{self.thinking_level}"
        return model_id

    @staticmethod
    def _strip_astrbot_tooling_sections(system_prompt: str) -> str:
        """Remove host-only tool and skill instructions from a system prompt."""
        cleaned = system_prompt.replace(TOOL_CALL_PROMPT, "").replace(
            TOOL_CALL_PROMPT_SKILLS_LIKE_MODE, ""
        )
        kept: list[str] = []
        skipping = False
        for line in cleaned.splitlines():
            heading = re.match(r"^##\s+(.+?)\s*$", line)
            if heading:
                title = heading.group(1).replace("`", "").strip().lower()
                skipping = (
                    "tool" in title
                    or title == "skills"
                    or title == "model aliases"
                    or title == "messaging"
                )
            if not skipping:
                kept.append(line)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()

    def _resolve_system_prompt(self, system_prompt: str) -> str:
        """Apply the configured system-prompt transport policy."""
        if self.system_prompt_mode == "none":
            return ""
        prompt = system_prompt.strip()
        if self.system_prompt_mode == "filtered":
            prompt = self._strip_astrbot_tooling_sections(prompt)
            if len(prompt) > AGY_SYSTEM_PROMPT_MAX_CHARS:
                marker = (
                    "\n\n[AstrBot system prompt truncated for agy CLI transport]\n\n"
                )
                remaining = AGY_SYSTEM_PROMPT_MAX_CHARS - len(marker)
                head_chars = int(remaining * 0.55)
                prompt = (
                    prompt[:head_chars].rstrip()
                    + marker
                    + prompt[-(remaining - head_chars) :].lstrip()
                )
        return "\n\n".join(part for part in (AGY_NATIVE_TOOL_NOTE, prompt) if part)

    async def _format_content(
        self,
        content: object,
        image_paths: list[Path],
        exit_stack: AsyncExitStack,
        working_directory: Path,
    ) -> str:
        """Render one OpenAI-style content payload and materialize images."""
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return "" if content is None else str(content)

        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            item_type = item.get("type")
            if item_type in {"text", "input_text"}:
                parts.append(str(item.get("text") or ""))
            elif item_type in {"think", "thinking"}:
                thinking = item.get("think", item.get("thinking", ""))
                if thinking:
                    parts.append(f"[thinking]\n{thinking}")
            elif item_type == "image_url":
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    image_url = image_url.get("url")
                if not isinstance(image_url, str) or not image_url:
                    parts.append("[invalid image omitted]")
                    continue
                try:
                    resolved = await exit_stack.enter_async_context(
                        MediaResolver(
                            image_url,
                            media_type="image",
                            default_suffix=".bin",
                        ).as_path()
                    )
                except Exception as exc:
                    logger.warning("Failed to prepare image for agy CLI: %s", exc)
                    parts.append("[image unavailable]")
                    continue
                image_path = resolved.path.resolve()
                try:
                    image_path.relative_to(working_directory)
                except ValueError:
                    input_dir = working_directory / ".astrbot-inputs"
                    input_dir.mkdir(parents=True, exist_ok=True)
                    suffix = image_path.suffix or ".bin"
                    copied_path = input_dir / f"{uuid4().hex}{suffix}"
                    await asyncio.to_thread(shutil.copyfile, image_path, copied_path)
                    exit_stack.callback(copied_path.unlink, missing_ok=True)
                    image_path = copied_path
                image_paths.append(image_path)
                parts.append("[image attached]")
            elif item_type == "audio_url":
                parts.append(
                    "[audio omitted: agy CLI provider supports text and images]"
                )
            elif item_type in {"tool_call", "toolCall"}:
                name = item.get("name") or "unknown"
                arguments = item.get("arguments", item.get("args", {}))
                parts.append(
                    f"[tool call: {name}]\n"
                    f"{json.dumps(arguments, ensure_ascii=False, default=str)}"
                )
            else:
                text = item.get("text")
                if text is not None:
                    parts.append(str(text))
        return "\n\n".join(part for part in parts if part).strip()

    async def _format_prompt(
        self,
        contexts: list[dict],
        exit_stack: AsyncExitStack,
        working_directory: Path,
        host_tool_prompt: str = "",
    ) -> tuple[str, list[Path]]:
        """Convert AstrBot context into the single prompt expected by agy."""
        system_parts: list[str] = []
        conversation_parts: list[str] = []
        image_paths: list[Path] = []
        for message in contexts:
            role = str(message.get("role") or "user")
            content = await self._format_content(
                message.get("content"), image_paths, exit_stack, working_directory
            )
            if role == "system":
                if content:
                    system_parts.append(content)
                continue
            if not content and message.get("tool_calls"):
                content = json.dumps(
                    message["tool_calls"], ensure_ascii=False, default=str
                )
            if not content:
                continue
            if role == "assistant":
                label = "Assistant"
            elif role == "tool":
                tool_name = message.get("name") or message.get("tool_name")
                label = f"Tool result ({tool_name})" if tool_name else "Tool result"
            else:
                label = "User"
            conversation_parts.append(f"{label}:\n{content}")

        sections: list[str] = []
        system_prompt = self._resolve_system_prompt("\n\n".join(system_parts))
        if host_tool_prompt:
            system_prompt = "\n\n".join(
                part for part in (system_prompt, host_tool_prompt) if part
            )
        if system_prompt:
            sections.append(f"System:\n{system_prompt}")
        if conversation_parts:
            sections.append("Conversation:\n" + "\n\n".join(conversation_parts))
        prompt = "\n\n".join(sections).strip()
        if image_paths:
            prompt += "\n\n" + "\n".join(f"@{path}" for path in image_paths)
        return prompt, image_paths

    async def _read_limited_stream(
        self,
        stream: asyncio.StreamReader,
        total_bytes: list[int],
    ) -> bytes:
        """Read a subprocess stream while enforcing the combined output cap."""
        chunks: list[bytes] = []
        while chunk := await stream.read(65_536):
            total_bytes[0] += len(chunk)
            if total_bytes[0] > self.max_output_bytes:
                raise _AgyOutputLimitError(
                    f"agy output exceeded {self.max_output_bytes} bytes"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    async def _run_cli(
        self,
        prompt: str,
        model: str,
        image_paths: list[Path],
        abort_signal: asyncio.Event | None,
        working_directory: Path,
    ) -> tuple[str, str, int]:
        """Execute agy without a shell and capture bounded UTF-8 output."""
        args: list[str] = []
        if self.dangerously_skip_permissions:
            args.append("--dangerously-skip-permissions")
        if self.sandbox_enabled:
            args.append("--sandbox")
        args.extend(["--agent", AGY_AGENT_NAME])
        args.extend(["--model", model, "--print-timeout", self.print_timeout])
        extra_dirs: list[Path] = []
        for image_path in image_paths:
            try:
                image_path.relative_to(working_directory)
            except ValueError:
                if image_path.parent not in extra_dirs:
                    extra_dirs.append(image_path.parent)
        for directory in extra_dirs:
            args.extend(["--add-dir", str(directory)])
        args.extend(["--print", prompt])

        env = self.cli_manager.build_environment(proxy=self.proxy, extra=self.env)
        subprocess_kwargs: dict = {}
        if os.name == "nt":
            subprocess_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            process = await asyncio.create_subprocess_exec(
                self.command,
                *args,
                cwd=str(working_directory),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **subprocess_kwargs,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"agy command was not found: {self.command}. Install and sign in to "
                "agy CLI for the AstrBot service account."
            ) from exc

        if process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            raise RuntimeError("agy process output pipes were not created")

        total_bytes = [0]

        async def collect_output() -> tuple[bytes, bytes, int]:
            stdout, stderr = await asyncio.gather(
                self._read_limited_stream(process.stdout, total_bytes),
                self._read_limited_stream(process.stderr, total_bytes),
            )
            return stdout, stderr, await process.wait()

        run_task = asyncio.create_task(collect_output())
        abort_task = asyncio.create_task(abort_signal.wait()) if abort_signal else None
        try:
            wait_for = {run_task}
            if abort_task:
                wait_for.add(abort_task)
            done, _ = await asyncio.wait(
                wait_for,
                timeout=self.timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError(f"agy timed out after {self.timeout:g} seconds")
            if abort_task and abort_task in done and abort_signal.is_set():
                raise asyncio.CancelledError("agy request was aborted")
            stdout_bytes, stderr_bytes, return_code = await run_task
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if not run_task.done():
                run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
            raise
        finally:
            if abort_task:
                abort_task.cancel()
                await asyncio.gather(abort_task, return_exceptions=True)

        stdout = _ANSI_ESCAPE_RE.sub("", stdout_bytes.decode("utf-8", "replace"))
        stderr = _ANSI_ESCAPE_RE.sub("", stderr_bytes.decode("utf-8", "replace"))
        return stdout.strip(), stderr.strip(), return_code

    async def text_chat(
        self,
        prompt: str | None = None,
        session_id: str | None = None,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        func_tool: ToolSet | None = None,
        contexts: list[Message] | list[dict] | None = None,
        system_prompt: str | None = None,
        tool_calls_result: ToolCallsResult | list[ToolCallsResult] | None = None,
        model: str | None = None,
        extra_user_content_parts: list[ContentPart] | None = None,
        tool_choice: Literal["auto", "required"] = "auto",
        request_max_retries: int | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Run one stateless agy request using AstrBot-managed conversation context."""
        context_query = copy.deepcopy(self._ensure_message_to_dicts(contexts))
        if system_prompt:
            context_query.insert(0, {"role": "system", "content": system_prompt})
        if prompt is not None or image_urls or audio_urls or extra_user_content_parts:
            content_parts: list[dict] = []
            if prompt:
                content_parts.append({"type": "text", "text": prompt})
            for part in extra_user_content_parts or []:
                content_parts.append(part.model_dump_for_context())
            for image_url in image_urls or []:
                content_parts.append(
                    {"type": "image_url", "image_url": {"url": image_url}}
                )
            for audio_url in audio_urls or []:
                content_parts.append(
                    {"type": "audio_url", "audio_url": {"url": audio_url}}
                )
            content: str | list[dict]
            if len(content_parts) == 1 and content_parts[0].get("type") == "text":
                content = str(content_parts[0]["text"])
            else:
                content = content_parts
            context_query.append({"role": "user", "content": content})
        if tool_calls_result:
            results = (
                [tool_calls_result]
                if isinstance(tool_calls_result, ToolCallsResult)
                else tool_calls_result
            )
            for result in results:
                context_query.extend(result.to_openai_messages())

        async with self._run_lock, AsyncExitStack() as exit_stack:
            working_directory = self._resolve_working_directory(session_id)
            self.cli_manager.ensure_astrbot_agent_config()
            available_host_tools = {}
            if func_tool:
                available_host_tools = {
                    tool.name: tool
                    for tool in func_tool.tools
                    if bool(getattr(tool, "active", True))
                    and tool.name in self.host_tool_allowlist
                }
            host_tool_prompt = ""
            if available_host_tools:
                schemas = [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters
                        or {"type": "object", "properties": {}},
                    }
                    for tool in available_host_tools.values()
                ]
                host_tool_prompt = (
                    "AstrBot exposes only the host tools in the JSON list below. "
                    "These tools run outside Agy's terminal sandbox, so use them only "
                    "when needed for rendering or sending the requested result. To "
                    "request exactly one host tool, your entire response must be "
                    '<astrbot-tool-call>{"name":"TOOL_NAME","arguments":{...}}'
                    "</astrbot-tool-call> with no Markdown fence or other text. Never "
                    "request a tool not listed here. If no host tool is needed, reply "
                    "normally.\n\n"
                    + json.dumps(schemas, ensure_ascii=False, separators=(",", ":"))
                )
            agy_prompt, image_paths = await self._format_prompt(
                context_query,
                exit_stack,
                working_directory,
                host_tool_prompt,
            )
            if not agy_prompt:
                raise ValueError("agy request contains no text or image content")
            resolved_model = self._resolve_model(model)
            stdout, stderr, return_code = await self._run_cli(
                agy_prompt,
                resolved_model,
                image_paths,
                kwargs.get("abort_signal"),
                working_directory,
            )
            model_error = (stderr or stdout).lower()
            legacy_model = self._resolve_legacy_model(model)
            if (
                return_code != 0
                and legacy_model != resolved_model
                and (
                    "invalid --model" in model_error
                    or "not recognized" in model_error
                    or "unknown model" in model_error
                )
            ):
                stdout, stderr, return_code = await self._run_cli(
                    agy_prompt,
                    legacy_model,
                    image_paths,
                    kwargs.get("abort_signal"),
                    working_directory,
                )

        if return_code != 0:
            detail = (stderr or stdout)[:4096]
            raise RuntimeError(
                f"agy exited with code {return_code}"
                + (f": {detail}" if detail else "")
            )
        completion_text = stdout or stderr
        if not completion_text:
            raise EmptyModelOutputError("agy CLI returned no usable output")
        lowered_output = completion_text.lower()
        if "no output produced" in lowered_output and "permission" in lowered_output:
            raise RuntimeError(completion_text[:4096])
        input_tokens = max(1, round(len(agy_prompt) / 4))
        output_tokens = max(1, round(len(completion_text) / 4))
        host_tool_match = _AGY_HOST_TOOL_CALL_RE.fullmatch(completion_text)
        if host_tool_match:
            try:
                tool_request = json.loads(host_tool_match.group(1))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "Agy returned an invalid host-tool envelope"
                ) from exc
            if not isinstance(tool_request, dict):
                raise RuntimeError("Agy host-tool request must be a JSON object")
            tool_name = tool_request.get("name")
            tool_args = tool_request.get("arguments", {})
            if tool_name not in available_host_tools:
                raise RuntimeError(
                    f"Agy requested a host tool outside the allowlist: {tool_name}"
                )
            if not isinstance(tool_args, dict):
                raise RuntimeError("Agy host-tool arguments must be a JSON object")
            try:
                jsonschema.validate(
                    tool_args,
                    available_host_tools[tool_name].parameters
                    or {"type": "object", "properties": {}},
                )
            except jsonschema.ValidationError as exc:
                raise RuntimeError(
                    f"Agy host-tool arguments failed validation: {exc.message}"
                ) from exc
            return LLMResponse(
                role="tool",
                completion_text="",
                tools_call_args=[tool_args],
                tools_call_name=[tool_name],
                tools_call_ids=[f"agy_host_{uuid4().hex}"],
                usage=TokenUsage(input_other=input_tokens, output=output_tokens),
            )
        return LLMResponse(
            role="assistant",
            result_chain=MessageChain().message(completion_text),
            usage=TokenUsage(input_other=input_tokens, output=output_tokens),
        )

    async def text_chat_stream(
        self,
        prompt: str | None = None,
        session_id: str | None = None,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        func_tool: ToolSet | None = None,
        contexts: list[Message] | list[dict] | None = None,
        system_prompt: str | None = None,
        tool_calls_result: ToolCallsResult | list[ToolCallsResult] | None = None,
        model: str | None = None,
        extra_user_content_parts: list[ContentPart] | None = None,
        tool_choice: Literal["auto", "required"] = "auto",
        request_max_retries: int | None = None,
        **kwargs,
    ) -> AsyncGenerator[LLMResponse, None]:
        """Expose agy's print-mode result through AstrBot's streaming interface."""
        yield await self.text_chat(
            prompt=prompt,
            session_id=session_id,
            image_urls=image_urls,
            audio_urls=audio_urls,
            func_tool=func_tool,
            contexts=contexts,
            system_prompt=system_prompt,
            tool_calls_result=tool_calls_result,
            model=model,
            extra_user_content_parts=extra_user_content_parts,
            tool_choice=tool_choice,
            request_max_retries=request_max_retries,
            **kwargs,
        )
