import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from astrbot.core.provider.agy_cli_manager import AgyCLIManager, AgyRelease


def test_managed_binary_is_preferred_and_home_is_persistent(tmp_path: Path) -> None:
    manager = AgyCLIManager(tmp_path)
    manager.ensure_runtime_directories()
    manager.managed_binary.write_bytes(b"agy")

    env = manager.build_environment(proxy="http://proxy:7890", remote_auth=True)

    assert manager.resolve_command() == str(manager.managed_binary)
    assert env["HOME"] == str(manager.home_dir)
    assert env["HTTPS_PROXY"] == "http://proxy:7890"
    assert env["SSH_CONNECTION"]
    assert env["PATH"].startswith(f"{manager.bin_dir}{os.pathsep}")


def test_astrbot_agent_config_preserves_settings_and_enforces_isolation(
    tmp_path: Path,
) -> None:
    manager = AgyCLIManager(tmp_path)
    settings_path = manager.home_dir / ".gemini" / "antigravity-cli" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "trustedWorkspaces": ["/existing"],
                "permissions": {"allow": ["read_url(example.com)"]},
            }
        ),
        encoding="utf-8",
    )

    agent_path = manager.ensure_astrbot_agent_config()
    settings = json.loads(settings_path.read_text(encoding="utf-8"))

    assert settings["trustedWorkspaces"] == ["/existing"]
    assert settings["permissions"]["allow"] == ["read_url(example.com)"]
    assert "unsandboxed(*)" in settings["permissions"]["deny"]
    assert settings["toolPermission"] == "proceed-in-sandbox"
    assert settings["artifactReviewPolicy"] == "always-proceed"
    assert settings["allowNonWorkspaceAccess"] is False
    assert settings["enableTerminalSandbox"] is True
    assert agent_path.name == "agent.md"
    agent_definition = agent_path.read_text(encoding="utf-8")
    assert "name: astrbot" in agent_definition
    assert "commandExecutionPolicy: sandbox" in agent_definition
    assert "  - run_command" in agent_definition
    assert "  - edit_file" in agent_definition
    skill_path = (
        manager.home_dir
        / ".gemini"
        / "config"
        / "skills"
        / "astrbot-host-rendering"
        / "SKILL.md"
    )
    assert "name: astrbot-host-rendering" in skill_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_install_verifies_then_atomically_replaces_managed_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = AgyCLIManager(tmp_path / "data")
    release = AgyRelease(
        version="1.2.3",
        url="https://storage.googleapis.com/example/agy.bin",
        sha512="0" * 128,
        platform="windows_amd64",
    )
    monkeypatch.setattr(manager, "fetch_release", AsyncMock(return_value=release))
    monkeypatch.setattr(manager, "get_version", AsyncMock(return_value="1.2.3"))

    async def fake_download(_release, destination, _proxy) -> None:
        destination.write_bytes(b"verified-binary")

    monkeypatch.setattr(manager, "_download_release", fake_download)

    result = await manager.install_or_update()

    assert manager.managed_binary.read_bytes() == b"verified-binary"
    assert result["version"] == "1.2.3"
    assert result["path"] == str(manager.managed_binary)
