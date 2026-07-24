from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest

from astrbot.dashboard.services import agy_cli_service
from astrbot.dashboard.services.agy_cli_service import AgyCLIService


class _StrictPercentLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str, *args: object) -> None:
        self.messages.append(message % args)

    def warning(self, message: str, *args: object) -> None:
        self.messages.append(message % args)


@pytest.mark.asyncio
async def test_auth_websocket_starts_terminal_with_standard_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = _StrictPercentLogger()
    monkeypatch.setattr(agy_cli_service, "logger", logger)
    monkeypatch.setattr(agy_cli_service.os, "name", "posix")

    service = AgyCLIService(manager=object())
    run_terminal = AsyncMock()
    monkeypatch.setattr(service, "_run_terminal", run_terminal)
    websocket = SimpleNamespace(
        accept=AsyncMock(),
        receive_json=AsyncMock(
            return_value={"proxy": "http://proxy.test", "cols": 100, "rows": 30}
        ),
        send_json=AsyncMock(),
        close=AsyncMock(),
    )
    secret = "test-secret-at-least-32-bytes-long"
    token = jwt.encode({"username": "tester"}, secret, algorithm="HS256")

    await service.run_auth_websocket(
        websocket,
        token=token,
        jwt_secret=secret,
    )

    run_terminal.assert_awaited_once_with(
        websocket,
        proxy="http://proxy.test",
        cols=100,
        rows=30,
    )
    assert logger.messages == ["Agy CLI web authentication started by tester"]
