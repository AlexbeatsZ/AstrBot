from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from packaging.version import InvalidVersion, Version

from astrbot.core.utils.astrbot_path import (
    get_astrbot_data_path,
)

AGY_RELEASE_BASE_URL = (
    "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app"
)
AGY_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class AgyRelease:
    """Metadata for one official Antigravity CLI release."""

    version: str
    url: str
    sha512: str
    platform: str


class AgyCLIManager:
    """Manage a Docker-persistent Antigravity CLI installation for AstrBot."""

    def __init__(self, data_path: str | Path | None = None) -> None:
        self.data_path = Path(data_path or get_astrbot_data_path()).resolve()
        self.root = self.data_path / "agy"
        self.bin_dir = self.root / "bin"
        self.home_dir = self.root / "home"
        self._install_lock = asyncio.Lock()

    @property
    def managed_binary(self) -> Path:
        """Return the executable path stored inside AstrBot's data volume."""
        suffix = ".exe" if os.name == "nt" else ""
        return self.bin_dir / f"agy{suffix}"

    def ensure_runtime_directories(self) -> None:
        """Create persistent directories without touching global system state."""
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.home_dir.mkdir(parents=True, exist_ok=True)

    def resolve_command(self, configured_command: str | None = None) -> str:
        """Prefer AstrBot's managed binary for the default ``agy`` command.

        Args:
            configured_command: Custom command name or executable path.

        Returns:
            The managed path, resolved external command, or original command.
        """
        command = str(configured_command or "agy").strip() or "agy"
        if command == "agy" and self.managed_binary.is_file():
            return str(self.managed_binary)
        return shutil.which(command) or command

    def build_environment(
        self,
        *,
        proxy: str = "",
        extra: dict[str, str] | None = None,
        remote_auth: bool = False,
    ) -> dict[str, str]:
        """Build an environment whose login state survives Docker restarts.

        Args:
            proxy: Optional HTTP and HTTPS proxy URL.
            extra: Explicit variables added last for advanced overrides.
            remote_auth: Whether to force agy's remote OAuth flow.

        Returns:
            A subprocess environment rooted in AstrBot's persistent data volume.
        """
        self.ensure_runtime_directories()
        env = os.environ.copy()
        env["HOME"] = str(self.home_dir)
        env["PATH"] = os.pathsep.join([str(self.bin_dir), env.get("PATH", "")]).rstrip(
            os.pathsep
        )
        if os.name == "nt":
            local_app_data = self.root / "local-app-data"
            local_app_data.mkdir(parents=True, exist_ok=True)
            env["USERPROFILE"] = str(self.home_dir)
            env["LOCALAPPDATA"] = str(local_app_data)
        if proxy:
            env.update(
                {
                    "HTTP_PROXY": proxy,
                    "HTTPS_PROXY": proxy,
                    "http_proxy": proxy,
                    "https_proxy": proxy,
                }
            )
        if remote_auth:
            env.setdefault("SSH_CONNECTION", "127.0.0.1 1 127.0.0.1 1")
            env.setdefault("TERM", "xterm-256color")
        if extra:
            env.update(extra)
        return env

    @staticmethod
    def platform_key() -> str:
        """Return the platform identifier used by Google's release manifest.

        Returns:
            A manifest key such as ``linux_amd64``.

        Raises:
            RuntimeError: If the operating system or architecture is unsupported.
        """
        system = platform.system().lower()
        machine = platform.machine().lower()
        arch_map = {
            "x86_64": "amd64",
            "amd64": "amd64",
            "aarch64": "arm64",
            "arm64": "arm64",
        }
        arch = arch_map.get(machine)
        if not arch or system not in {"linux", "darwin", "windows"}:
            raise RuntimeError(
                f"agy does not support this platform: {system}/{machine}"
            )
        return f"{system}_{arch}"

    async def fetch_release(self, proxy: str = "") -> AgyRelease:
        """Fetch and validate the official release manifest.

        Args:
            proxy: Optional proxy used only for the manifest request.

        Returns:
            Validated official release metadata for the current platform.

        Raises:
            RuntimeError: If the platform or manifest is invalid.
            aiohttp.ClientError: If the manifest request fails.
        """
        platform_key = self.platform_key()
        manifest_url = f"{AGY_RELEASE_BASE_URL}/manifests/{platform_key}.json"
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(manifest_url, proxy=proxy or None) as response:
                response.raise_for_status()
                manifest = await response.json(content_type=None)

        version = str(manifest.get("version") or "").strip()
        url = str(manifest.get("url") or "").strip()
        sha512 = str(manifest.get("sha512") or "").strip().lower()
        parsed_url = urlparse(url)
        if (
            not version
            or parsed_url.scheme != "https"
            or not parsed_url.netloc
            or len(sha512) != 128
            or any(char not in "0123456789abcdef" for char in sha512)
        ):
            raise RuntimeError("agy release manifest is invalid")
        return AgyRelease(version, url, sha512, platform_key)

    async def _download_release(
        self,
        release: AgyRelease,
        destination: Path,
        proxy: str,
    ) -> None:
        """Download a release while enforcing size and checksum limits.

        Args:
            release: Validated release metadata.
            destination: Temporary file that receives the payload.
            proxy: Optional download proxy.

        Raises:
            RuntimeError: If the payload is too large or fails checksum validation.
            aiohttp.ClientError: If the download fails.
        """
        digest = hashlib.sha512()
        size = 0
        timeout = aiohttp.ClientTimeout(total=900)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(release.url, proxy=proxy or None) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        size += len(chunk)
                        if size > AGY_MAX_DOWNLOAD_BYTES:
                            raise RuntimeError("agy release exceeds the download limit")
                        digest.update(chunk)
                        output.write(chunk)
        if digest.hexdigest() != release.sha512:
            raise RuntimeError("agy release checksum verification failed")

    @staticmethod
    def _prepare_binary(payload: Path, destination: Path) -> None:
        """Extract or copy a verified payload into an executable candidate.

        Args:
            payload: Downloaded release file.
            destination: Temporary executable path.

        Raises:
            RuntimeError: If an archive has no valid Antigravity binary.
        """
        if payload.name.endswith(".tar.gz"):
            with tarfile.open(payload, "r:gz") as archive:
                member = next(
                    (
                        item
                        for item in archive.getmembers()
                        if item.isfile() and Path(item.name).name == "antigravity"
                    ),
                    None,
                )
                if member is None or member.size > AGY_MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(
                        "agy release archive does not contain a valid binary"
                    )
                source = archive.extractfile(member)
                if source is None:
                    raise RuntimeError("agy release binary could not be extracted")
                with source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
        else:
            shutil.copyfile(payload, destination)
        destination.chmod(
            destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    async def install_or_update(self, proxy: str = "") -> dict:
        """Install the latest verified binary atomically into AstrBot data.

        Args:
            proxy: Optional proxy used for manifest and binary downloads.

        Returns:
            Installed version, path, and platform metadata.

        Raises:
            RuntimeError: If download, validation, or extraction fails.
        """
        async with self._install_lock:
            self.ensure_runtime_directories()
            release = await self.fetch_release(proxy)
            temp_root = self.data_path / "temp"
            temp_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="agy-install-", dir=temp_root
            ) as tmp:
                temp_dir = Path(tmp)
                archive_suffix = (
                    ".tar.gz" if release.url.endswith(".tar.gz") else ".bin"
                )
                payload = temp_dir / f"download{archive_suffix}"
                candidate = temp_dir / self.managed_binary.name
                await self._download_release(release, payload, proxy)
                await asyncio.to_thread(self._prepare_binary, payload, candidate)
                os.replace(candidate, self.managed_binary)
            installed_version = await self.get_version(str(self.managed_binary))
            return {
                "version": installed_version or release.version,
                "path": str(self.managed_binary),
                "platform": release.platform,
            }

    async def get_version(self, command: str | None = None) -> str | None:
        """Read the installed CLI version without invoking a shell.

        Args:
            command: Optional command name or executable path.

        Returns:
            Version text, or ``None`` when the executable is unavailable.
        """
        resolved = self.resolve_command(command)
        try:
            process = await asyncio.create_subprocess_exec(
                resolved,
                "--version",
                env=self.build_environment(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        except (FileNotFoundError, PermissionError, TimeoutError):
            return None
        if process.returncode != 0:
            return None
        return (stdout or stderr).decode("utf-8", "replace").strip() or None

    async def status(self, proxy: str = "") -> dict:
        """Return installed/latest versions and persistent runtime locations.

        Args:
            proxy: Optional proxy used for the latest-version check.

        Returns:
            CLI installation, update, platform, and data-directory status.
        """
        managed = self.managed_binary.is_file()
        command = self.resolve_command()
        version = await self.get_version(command)
        executable = command if version else None
        latest: AgyRelease | None = None
        latest_error: str | None = None
        try:
            latest = await self.fetch_release(proxy)
        except Exception as exc:
            latest_error = str(exc)

        update_available = False
        if version and latest:
            try:
                update_available = Version(version) < Version(latest.version)
            except InvalidVersion:
                update_available = version != latest.version

        profile_dir = self.home_dir / ".gemini" / "antigravity-cli"
        return {
            "installed": bool(version),
            "version": version,
            "executable": executable,
            "installation": "managed" if managed else ("external" if version else None),
            "latest_version": latest.version if latest else None,
            "update_available": update_available,
            "platform": latest.platform if latest else self.platform_key(),
            "latest_error": latest_error,
            "profile_initialized": profile_dir.is_dir(),
            "data_directory": str(self.root),
            "managed_binary": str(self.managed_binary),
        }
