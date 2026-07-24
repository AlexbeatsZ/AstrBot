from __future__ import annotations

import asyncio
import os
import signal
import struct
from pathlib import Path

import jwt
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from astrbot import logger
from astrbot.core.provider.agy_cli_manager import AgyCLIManager
from astrbot.core.utils.astrbot_path import get_astrbot_workspaces_path

AGY_AUTH_SESSION_TIMEOUT_SECONDS = 15 * 60
AGY_AUTH_MAX_INPUT_CHARS = 16_384


class AgyCLIService:
    """Expose safe CLI lifecycle operations and a browser-backed auth terminal."""

    def __init__(self, manager: AgyCLIManager | None = None) -> None:
        self.manager = manager or AgyCLIManager()
        self._auth_lock = asyncio.Lock()

    async def status(self, proxy: str = "") -> dict:
        return await self.manager.status(proxy)

    async def install_or_update(self, proxy: str = "") -> dict:
        return await self.manager.install_or_update(proxy)

    @staticmethod
    def _authenticate_token(token: str | None, jwt_secret: str) -> str:
        """Validate a Dashboard JWT before opening a privileged terminal.

        Args:
            token: Dashboard JWT from the WebSocket query.
            jwt_secret: Secret used to validate the token.

        Returns:
            Authenticated Dashboard username.

        Raises:
            ValueError: If the token is missing, expired, or invalid.
        """
        if not token:
            raise ValueError("Missing authentication token")
        try:
            payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError as exc:
            raise ValueError("Authentication token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise ValueError("Invalid authentication token") from exc
        username = payload.get("username")
        if not isinstance(username, str) or not username.strip():
            raise ValueError("Invalid authentication token")
        return username

    @staticmethod
    def _resize_pty(master_fd: int, cols: int, rows: int) -> None:
        """Apply bounded browser terminal dimensions to a Unix PTY.

        Args:
            master_fd: PTY master file descriptor.
            cols: Requested terminal columns.
            rows: Requested terminal rows.
        """
        import fcntl
        import termios

        cols = max(40, min(cols, 300))
        rows = max(12, min(rows, 120))
        fcntl.ioctl(
            master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0)
        )

    async def _stream_pty_output(
        self,
        master_fd: int,
        websocket: WebSocket,
    ) -> None:
        """Forward PTY output to the browser until either side closes.

        Args:
            master_fd: PTY master file descriptor.
            websocket: Authenticated Dashboard WebSocket.
        """
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        read_file = os.fdopen(os.dup(master_fd), "rb", buffering=0)
        transport = None
        try:
            transport, _ = await loop.connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader),
                read_file,
            )
            while chunk := await reader.read(65_536):
                await websocket.send_json(
                    {"type": "output", "data": chunk.decode("utf-8", "replace")}
                )
        except (OSError, WebSocketDisconnect):
            return
        finally:
            if transport:
                transport.close()
            else:
                read_file.close()

    async def _receive_terminal_input(
        self,
        master_fd: int,
        websocket: WebSocket,
    ) -> None:
        """Forward bounded browser input and resize events to the PTY.

        Args:
            master_fd: PTY master file descriptor.
            websocket: Authenticated Dashboard WebSocket.
        """
        while True:
            payload = await websocket.receive_json()
            message_type = payload.get("type")
            if message_type == "input":
                data = payload.get("data")
                if not isinstance(data, str) or len(data) > AGY_AUTH_MAX_INPUT_CHARS:
                    continue
                await asyncio.to_thread(os.write, master_fd, data.encode())
            elif message_type == "resize":
                try:
                    cols = int(payload.get("cols", 120))
                    rows = int(payload.get("rows", 32))
                except (TypeError, ValueError):
                    continue
                self._resize_pty(master_fd, cols, rows)

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        """Terminate a terminal process, escalating to kill after a grace period.

        Args:
            process: Agy CLI subprocess to stop.
        """
        if process.returncode is not None:
            return
        try:
            process.send_signal(signal.SIGTERM)
            await asyncio.wait_for(process.wait(), timeout=3)
        except (ProcessLookupError, TimeoutError):
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def _run_terminal(
        self,
        websocket: WebSocket,
        *,
        proxy: str,
        cols: int,
        rows: int,
    ) -> None:
        """Run one bounded Agy onboarding session inside a Unix PTY.

        Args:
            websocket: Authenticated Dashboard WebSocket.
            proxy: Optional proxy for Agy network requests.
            cols: Initial terminal columns.
            rows: Initial terminal rows.

        Raises:
            RuntimeError: If Agy CLI is not installed.
        """
        import pty

        command = self.manager.resolve_command()
        if await self.manager.get_version(command) is None:
            raise RuntimeError("agy CLI is not installed")

        workspace = Path(get_astrbot_workspaces_path())
        workspace.mkdir(parents=True, exist_ok=True)
        env = self.manager.build_environment(proxy=proxy, remote_auth=True)
        master_fd, slave_fd = pty.openpty()
        process: asyncio.subprocess.Process | None = None
        tasks: list[asyncio.Task] = []
        try:
            self._resize_pty(master_fd, cols, rows)
            process = await asyncio.create_subprocess_exec(
                command,
                cwd=str(workspace),
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
            )
            os.close(slave_fd)
            slave_fd = -1
            await websocket.send_json({"type": "ready"})
            tasks = [
                asyncio.create_task(self._stream_pty_output(master_fd, websocket)),
                asyncio.create_task(self._receive_terminal_input(master_fd, websocket)),
                asyncio.create_task(process.wait()),
            ]
            done, _ = await asyncio.wait(
                tasks,
                timeout=AGY_AUTH_SESSION_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                await websocket.send_json(
                    {"type": "error", "message": "agy auth session timed out"}
                )
            elif tasks[2] in done:
                await websocket.send_json({"type": "exit", "code": process.returncode})
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if process:
                await self._terminate_process(process)
            if slave_fd >= 0:
                os.close(slave_fd)
            os.close(master_fd)

    async def run_auth_websocket(
        self,
        websocket: WebSocket,
        *,
        token: str | None,
        jwt_secret: str,
    ) -> None:
        """Bridge agy's interactive OAuth/onboarding TUI to the dashboard.

        Args:
            websocket: Incoming Dashboard WebSocket.
            token: Dashboard JWT supplied by the browser.
            jwt_secret: Secret used to validate the JWT.
        """
        await websocket.accept()
        try:
            username = self._authenticate_token(token, jwt_secret)
        except ValueError as exc:
            await websocket.close(1008, str(exc))
            return
        if os.name != "posix":
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "Web authentication is supported in Linux/Docker deployments.",
                }
            )
            await websocket.close(1003)
            return
        if self._auth_lock.locked():
            await websocket.send_json(
                {"type": "error", "message": "Another agy auth session is active."}
            )
            await websocket.close(1013)
            return

        try:
            initial = await asyncio.wait_for(websocket.receive_json(), timeout=15)
            proxy = str(initial.get("proxy") or "").strip()
            cols = int(initial.get("cols", 120))
            rows = int(initial.get("rows", 32))
            async with self._auth_lock:
                logger.info("Agy CLI web authentication started by %s", username)
                await self._run_terminal(
                    websocket,
                    proxy=proxy,
                    cols=cols,
                    rows=rows,
                )
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.warning("Agy CLI web authentication failed: %s", exc)
            try:
                await websocket.send_json({"type": "error", "message": str(exc)})
            except Exception:
                pass
        finally:
            try:
                await websocket.close()
            except Exception:
                pass
