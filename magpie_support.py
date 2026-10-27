"""Magpie integration for codex-101's isolated lab mode.

The module intentionally keeps provider secrets out of command lines.  Magpie
0.1.x exposes no provider-key stdin/file option, so a short-lived isolated
Magpie install is first asked to write a provider template with a sentinel
key.  Only the template's ``key`` field is replaced before the provider is
merged into the lab's providers.json.

At runtime the module uses the member's normal HOME, XDG and CODEX_HOME
locations.  Only the short-lived provider-template subprocess is redirected
to a private temporary HOME/XDG/CODEX_HOME with a dead gateway port.  No
request is sent to the CPR upstream, and no model request is made.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse


CPR_PROVIDER_ID = "cpr"
CPR_PROVIDER_NAME = "CPR"
CPR_BASE_URL = "https://cpr.molaisonz.dpdns.org/v1"
GATEWAY_URL = "http://127.0.0.1:3425/v1"
SENTINEL_KEY = "sk-codex101-magpie-template-SENTINEL-not-a-real-key"

_LATEST_URL = "https://usemagpie.ai/api/latest"
_INSTALL_SH_URL = "https://usemagpie.ai/install.sh"
_RUN_TIMEOUT = 90
_SETUP_TIMEOUT = 900
_WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
_WINDOWS_DETACHED_PROCESS = 0x00000008
_USER_AGENT = "codex101-magpie-support"


class MagpieSupportError(RuntimeError):
    """A safe, secret-free integration error."""


def _home() -> Path:
    return Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or Path.home()).expanduser()


def _xdg(name: str, fallback: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else fallback


def _runtime_layout() -> Dict[str, Path]:
    home = _home()
    xdg_config_home = _xdg("XDG_CONFIG_HOME", home / ".config")
    xdg_cache_home = _xdg("XDG_CACHE_HOME", home / ".cache")
    codex_home = Path(os.environ.get("CODEX_HOME", str(home / ".codex"))).expanduser()
    return {
        "home": home,
        "xdg_config_home": xdg_config_home,
        "xdg_cache_home": xdg_cache_home,
        "codex_home": codex_home,
        "bin": _default_bin_dir(),
        "log_dir": xdg_cache_home / "magpie",
    }


def _template_layout(root: Path) -> Dict[str, Path]:
    paths = {
        "root": root,
        "home": root / "home",
        "xdg_config_home": root / "xdg-config",
        "xdg_cache_home": root / "xdg-cache",
        "codex_home": root / "codex-home",
        "bin": root / "bin",
        "log_dir": root / "xdg-cache" / "magpie",
    }
    for path in paths.values():
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return paths


def _is_loopback_host(host: str) -> bool:
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    return host in ("127.0.0.1", "::1")


def _environment(
    paths: Mapping[str, Path],
    gateway_url: str,
    *,
    block_remote: bool,
) -> Dict[str, str]:
    env = os.environ.copy()
    addr = urlparse(gateway_url)
    env.update(
        {
            "HOME": str(paths["home"]),
            "USERPROFILE": str(paths["home"]),
            "XDG_CONFIG_HOME": str(paths["xdg_config_home"]),
            "XDG_CACHE_HOME": str(paths["xdg_cache_home"]),
            "CODEX_HOME": str(paths["codex_home"]),
            "MAGPIE_ADDR": "%s:%d" % (addr.hostname, addr.port or 80),
            "MAGPIE_NO_ACCOUNTS": "1",
        }
    )
    for name in (
        "MAGPIE_PROXY",
        "MAGPIE_WEB_KEY",
        "MAGPIE_TLS_CERT",
        "MAGPIE_TLS_KEY",
        "CODEX_PROVIDER_API_KEY",
    ):
        env.pop(name, None)
    if block_remote:
        # The provider template must be built without reaching CPR or a real
        # gateway.  The local MAGPIE_ADDR remains reachable.
        dead_proxy = "http://127.0.0.1:9"
        env.update(
            {
                "HTTP_PROXY": dead_proxy,
                "HTTPS_PROXY": dead_proxy,
                "ALL_PROXY": dead_proxy,
                "http_proxy": dead_proxy,
                "https_proxy": dead_proxy,
                "all_proxy": dead_proxy,
                "NO_PROXY": "127.0.0.1,localhost,::1",
                "no_proxy": "127.0.0.1,localhost,::1",
            }
        )
    return env


def _binary_name() -> str:
    return "magpie.exe" if os.name == "nt" else "magpie"


def _default_bin_dir() -> Path:
    override = os.environ.get("MAGPIE_BIN_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else _home() / "AppData" / "Local"
        return base / "magpie" / "bin"
    return _home() / ".local" / "bin"


def find_magpie() -> Optional[Path]:
    override = os.environ.get("MAGPIE_BINARY", "").strip()
    candidates: List[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    found = shutil.which("magpie")
    if found:
        candidates.append(Path(found))
    candidates.append(_default_bin_dir() / _binary_name())
    for candidate in candidates:
        try:
            if candidate.is_file() and (os.name == "nt" or os.access(str(candidate), os.X_OK)):
                return candidate.resolve()
        except OSError:
            pass
    return None


def _download(url: str, destination: Path, *, proxy: Optional[str] = None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        if proxy
        else urllib.request.build_opener()
    )
    with opener.open(request, timeout=60) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _curl_download(url: str, destination: Path) -> None:
    curl = shutil.which("curl")
    if not curl:
        raise MagpieSupportError("curl is required by the official Magpie Unix installer")
    result = subprocess.run(
        [
            curl,
            "--fail",
            "--location",
            "--retry",
            "2",
            "--max-time",
            "90",
            "--silent",
            "--show-error",
            "--user-agent",
            _USER_AGENT,
            "--output",
            str(destination),
            url,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not destination.is_file():
        raise MagpieSupportError("could not download the official Magpie installer")


def _install_unix_magpie(bin_dir: Path) -> Path:
    if os.name == "nt":
        raise MagpieSupportError("Unix Magpie installer requested on Windows")
    bin_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codex101-magpie-install-") as temporary:
        script = Path(temporary) / "install.sh"
        _curl_download(_INSTALL_SH_URL, script)
        env = os.environ.copy()
        env["MAGPIE_BIN_DIR"] = str(bin_dir)
        if sys.platform.startswith("linux"):
            env["MAGPIE_CLI"] = "1"
        completed = subprocess.run(
            ["/bin/sh", str(script)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_SETUP_TIMEOUT,
            check=False,
        )
    target = bin_dir / "magpie"
    if completed.returncode != 0 or not target.is_file():
        raise MagpieSupportError("official Magpie installer failed")
    return target.resolve()


def _windows_asset() -> Tuple[str, str, str]:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64", "x64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        raise MagpieSupportError("unsupported Windows architecture")
    name = "magpie-cli-windows-%s.exe" % arch
    request = urllib.request.Request(_LATEST_URL, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        feed = json.load(response)
    entry = (feed.get("assets") or feed.get("files") or {}).get(name)
    if not isinstance(entry, dict):
        raise MagpieSupportError("official release feed has no %s" % name)
    url, expected = entry.get("url"), entry.get("sha256")
    if not isinstance(url, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", str(expected or "")):
        raise MagpieSupportError("official release metadata for Windows CLI is incomplete")
    return url, str(expected).lower(), name


def _install_windows_magpie(bin_dir: Path) -> Path:
    if os.name != "nt":
        raise MagpieSupportError("Windows Magpie installer requested on a non-Windows host")
    url, expected, name = _windows_asset()
    bin_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = bin_dir / "magpie.exe"
    fd, temporary_name = tempfile.mkstemp(prefix=".magpie-", suffix=".tmp", dir=str(bin_dir))
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        _download(url, temporary)
        if _sha256_file(temporary) != expected:
            raise MagpieSupportError("official Windows Magpie CLI checksum mismatch")
        os.replace(str(temporary), str(target))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return target.resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def install_magpie(bin_dir: Optional[Path] = None) -> Path:
    destination = (bin_dir or _default_bin_dir()).expanduser().resolve()
    if os.name == "nt":
        return _install_windows_magpie(destination)
    return _install_unix_magpie(destination)


def ensure_magpie() -> Path:
    binary = find_magpie()
    return binary if binary is not None else install_magpie()


def _run(
    arguments: Sequence[str],
    env: Mapping[str, str],
    *,
    timeout: int = _RUN_TIMEOUT,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(arguments),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MagpieSupportError("could not run %s (%s)" % (Path(arguments[0]).name, type(error).__name__)) from None


def _version(binary: Path, env: Mapping[str, str]) -> str:
    result = _run([str(binary), "--version"], env)
    if result.returncode != 0:
        raise MagpieSupportError("magpie --version failed")
    first = (result.stdout or "").strip().splitlines()
    return first[0] if first else "unknown"


def _template_provider(binary: Path, models: Sequence[str]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex101-magpie-template-") as temporary:
        root = Path(temporary)
        paths = _template_layout(root)
        private_binary = paths["bin"] / binary.name
        try:
            shutil.copy2(str(binary), str(private_binary))
            if os.name != "nt":
                private_binary.chmod(0o700)
        except OSError as error:
            raise MagpieSupportError(
                "could not isolate the Magpie provider-template executable (%s)" % type(error).__name__
            ) from None
        gateway = "http://127.0.0.1:9/v1"
        env = _environment(paths, gateway, block_remote=True)
        models_arg = ",".join(models)
        command = [
            str(private_binary),
            "provider",
            "add",
            CPR_PROVIDER_NAME,
            "url=" + CPR_BASE_URL,
            "responses=" + CPR_BASE_URL,
            "key=" + SENTINEL_KEY,
            "models=" + models_arg,
        ]
        result = _run(command, env, timeout=_SETUP_TIMEOUT)
        if result.returncode != 0:
            raise MagpieSupportError("isolated Magpie provider template failed")
        provider_file = paths["xdg_config_home"] / "magpie" / "providers.json"
        try:
            document = json.loads(provider_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise MagpieSupportError("isolated Magpie did not produce a readable provider template") from None
        matches = [
            item
            for item in (document.get("providers") or [])
            if isinstance(item, dict) and item.get("id") == CPR_PROVIDER_ID
        ]
        if len(matches) != 1:
            raise MagpieSupportError("isolated Magpie template has no unique cpr provider")
        provider = copy.deepcopy(matches[0])
        if provider.get("key") != SENTINEL_KEY:
            raise MagpieSupportError("isolated Magpie template did not preserve the provider key field")
        if _endpoint(provider, "chat") != CPR_BASE_URL or _endpoint(provider, "responses") != CPR_BASE_URL:
            raise MagpieSupportError("isolated Magpie changed the CPR endpoint binding")
        if provider.get("models") != list(models):
            raise MagpieSupportError("isolated Magpie changed the explicit CPR model list")
        return provider


def _endpoint(provider: Mapping[str, Any], field: str) -> str:
    value = provider.get(field, "")
    return value.rstrip("/") if isinstance(value, str) else ""


def _read_json_object(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"providers": []}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise MagpieSupportError("existing providers.json cannot be read; refusing to overwrite it") from None
    if not isinstance(document, dict):
        raise MagpieSupportError("existing providers.json is not a JSON object")
    providers = document.setdefault("providers", [])
    if not isinstance(providers, list):
        raise MagpieSupportError("existing providers.json has a non-array providers field")
    return document


def _check_existing_binding(provider: Mapping[str, Any]) -> None:
    for field in ("chat", "responses", "url", "openai", "anthropic", "gemini", "decide"):
        value = provider.get(field)
        if isinstance(value, str) and value.strip() and value.rstrip("/") != CPR_BASE_URL:
            raise MagpieSupportError("existing cpr provider is bound to a different URL; refusing to replace it")


def _merge_provider(
    template: Mapping[str, Any],
    real_key: str,
    providers_path: Path,
) -> Tuple[Dict[str, Any], bool]:
    document = _read_json_object(providers_path)
    providers = document["providers"]
    indexes = [
        index
        for index, item in enumerate(providers)
        if isinstance(item, dict) and item.get("id") == CPR_PROVIDER_ID
    ]
    if len(indexes) > 1:
        raise MagpieSupportError("providers.json has more than one cpr provider")

    generated = copy.deepcopy(dict(template))
    generated["key"] = real_key
    generated["id"] = CPR_PROVIDER_ID
    generated["chat"] = CPR_BASE_URL
    generated["responses"] = CPR_BASE_URL

    if indexes:
        current = providers[indexes[0]]
        _check_existing_binding(current)
        # Keep fields this version does not generate, but take the current
        # generated endpoint/model shape and the newly supplied key.
        merged = dict(current)
        merged.update(generated)
        providers[indexes[0]] = merged
        changed = merged != current
    else:
        providers.append(generated)
        changed = True
    return document, changed


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_dir = Path(tempfile.mkdtemp(prefix=".codex101-magpie-write-", dir=str(path.parent)))
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=".pending.", dir=str(private_dir))
        temporary = Path(temporary_name)
        try:
            fchmod = getattr(os, "fchmod", None)
            if callable(fchmod):
                fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            else:
                os.chmod(str(temporary), stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "wb", closefd=True) as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(str(temporary), str(path))
            try:
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
            except OSError:
                directory_fd = -1
            if directory_fd >= 0:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    finally:
        shutil.rmtree(str(private_dir), ignore_errors=True)


def _atomic_write_json(path: Path, document: Mapping[str, Any]) -> None:
    payload = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _atomic_write_bytes(path, payload)


def _portable_runtime_marker(binary: Path) -> Optional[Path]:
    for relative in (Path(".portable"), Path("data")):
        candidate = binary.parent / relative
        if candidate.exists() or candidate.is_symlink():
            return candidate
    return None


def _ensure_not_portable_runtime(binary: Path) -> None:
    marker = _portable_runtime_marker(binary)
    if marker is not None:
        raise MagpieSupportError(
            "unsupported portable Magpie runtime; refusing to modify its real state"
        )


def _gateway_healthy(binary: Path, env: Mapping[str, str]) -> bool:
    """Use Magpie's own healthcheck, not an arbitrary HTTP status."""
    result = _run([str(binary), "healthcheck"], env, timeout=12)
    return result.returncode == 0


def ensure_daemon(binary: Path, env: Mapping[str, str], gateway_url: str) -> Dict[str, Any]:
    """Bring up a stopped/absent daemon without restarting a healthy gateway.

    A healthy gateway is returned untouched.  An installed but stopped
    service is started with the official command; an installed running but
    unhealthy service is not restarted.
    """
    if _gateway_healthy(binary, env):
        return {"gateway_healthy": True, "daemon_installed": None, "action": "none"}

    status = _run([str(binary), "daemon", "status"], env)
    if status.returncode != 0:
        raise MagpieSupportError("magpie daemon status failed")
    output = ((status.stdout or "") + "\n" + (status.stderr or "")).lower()
    action = "none"
    if "isn't installed" in output or "is not installed" in output:
        result = _run([str(binary), "daemon", "install"], env, timeout=_SETUP_TIMEOUT)
        action = "install"
        if result.returncode != 0:
            raise MagpieSupportError("magpie daemon install failed")
    elif "not running" in output:
        result = _run([str(binary), "daemon", "start"], env, timeout=_SETUP_TIMEOUT)
        action = "start"
        if result.returncode != 0:
            raise MagpieSupportError("magpie daemon start failed")

    deadline = time.monotonic() + 8
    healthy_now = False
    while time.monotonic() < deadline:
        healthy_now = _gateway_healthy(binary, env)
        if healthy_now:
            break
        time.sleep(0.2)
    return {
        "gateway_healthy": healthy_now,
        "daemon_installed": True,
        "action": action,
    }


def _cmd_quote(value: str) -> str:
    if "\x00" in value or "\r" in value or "\n" in value or '"' in value:
        raise MagpieSupportError("Windows startup path contains an unsupported character")
    return value.replace("%", "%%")


def _startup_cmd(paths: Mapping[str, Path], binary: Path, gateway_url: str) -> Path:
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        raise MagpieSupportError("APPDATA is required to install the Windows Startup entry")
    startup_dir = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = startup_dir / "codex101-magpie.cmd"
    marker = "@rem codex101-magpie-owner: codex101-magpie"
    parsed = urlparse(gateway_url)
    log_path = paths["log_dir"] / "magpie-serve.log"
    lines = [
        "@echo off",
        marker,
        'set "HOME=%s"' % _cmd_quote(str(paths["home"])),
        'set "USERPROFILE=%s"' % _cmd_quote(str(paths["home"])),
        'set "XDG_CONFIG_HOME=%s"' % _cmd_quote(str(paths["xdg_config_home"])),
        'set "XDG_CACHE_HOME=%s"' % _cmd_quote(str(paths["xdg_cache_home"])),
        'set "CODEX_HOME=%s"' % _cmd_quote(str(paths["codex_home"])),
        'set "MAGPIE_ADDR=%s:%d"' % (parsed.hostname, parsed.port or 80),
        'set "MAGPIE_NO_ACCOUNTS=1"',
        '"%s" serve 1>>"%s" 2>&1'
        % (_cmd_quote(str(binary)), _cmd_quote(str(log_path))),
        "",
    ]
    payload = "\r\n".join(lines)
    if target.exists():
        try:
            current = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raise MagpieSupportError("existing Windows Startup file cannot be read") from None
        if marker not in current:
            raise MagpieSupportError(
                "unrelated codex101-magpie.cmd exists in Windows Startup; refusing to overwrite it"
            )
        if current == payload:
            return target
    fd, temporary_name = tempfile.mkstemp(prefix=".codex101-magpie-", suffix=".cmd", dir=str(startup_dir))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(temporary), str(target))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return target


def _ensure_windows_gateway(
    binary: Path,
    paths: Mapping[str, Path],
    env: Mapping[str, str],
    gateway_url: str,
) -> Dict[str, Any]:
    if _gateway_healthy(binary, env):
        return {"gateway_healthy": True, "daemon_installed": None, "action": "none"}
    if os.name != "nt":
        raise MagpieSupportError("Windows gateway start requested on a non-Windows host")

    startup = _startup_cmd(paths, binary, gateway_url)
    log_path = paths["log_dir"] / "magpie-serve.log"
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    creationflags = _WINDOWS_DETACHED_PROCESS | _WINDOWS_CREATE_NEW_PROCESS_GROUP
    try:
        log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(log_fd, "ab", buffering=0) as log:
            subprocess.Popen(
                [str(binary), "serve"],
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                close_fds=True,
                creationflags=creationflags,
            )
    except OSError as error:
        raise MagpieSupportError("could not start the lab magpie serve process (%s)" % type(error).__name__) from None

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if _gateway_healthy(binary, env):
            return {
                "gateway_healthy": True,
                "daemon_installed": False,
                "action": "serve",
                "startup_file": str(startup),
            }
        time.sleep(0.2)
    raise MagpieSupportError("detached lab magpie serve did not become healthy")


def _ensure_gateway(
    binary: Path,
    paths: Mapping[str, Path],
    env: Mapping[str, str],
    gateway_url: str,
) -> Dict[str, Any]:
    if os.name == "nt":
        return _ensure_windows_gateway(binary, paths, env, gateway_url)
    return ensure_daemon(binary, env, gateway_url)


def _read_codex_config(path: Path) -> Dict[str, Any]:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        return _read_codex_config_fallback(path)
    with path.open("rb") as source:
        return tomllib.load(source)


def _read_codex_config_fallback(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    result: Dict[str, Any] = {}
    match = re.search(r'(?m)^\s*model_provider\s*=\s*"([^"]+)"\s*$', text)
    if match:
        result["model_provider"] = match.group(1)
    match = re.search(r'(?m)^\s*model\s*=\s*"([^"]+)"\s*$', text)
    if match:
        result["model"] = match.group(1)
    table = re.search(r'(?ms)^\s*\[model_providers\.magpie\]\s*(.*?)(?=^\s*\[|\Z)', text)
    if table:
        provider: Dict[str, str] = {}
        for key in ("name", "base_url", "wire_api", "experimental_bearer_token"):
            match = re.search(r'(?m)^\s*%s\s*=\s*"([^"]*)"\s*$' % re.escape(key), table.group(1))
            if match:
                provider[key] = match.group(1)
        result["model_providers"] = {"magpie": provider}
    return result


def _validate_codex_config(path: Path, qualified_model: str, gateway_url: str) -> None:
    try:
        document = _read_codex_config(path)
    except (OSError, ValueError):
        raise MagpieSupportError("magpie did not produce a readable Codex config") from None
    if document.get("model") != qualified_model:
        raise MagpieSupportError("magpie did not set the qualified CPR Codex model")
    provider = (document.get("model_providers") or {}).get("magpie") or {}
    base_url = str(provider.get("base_url", "")).rstrip("/")
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or not _is_loopback_host(parsed.hostname)
        or parsed.path.rstrip("/") != "/v1"
        or base_url != gateway_url.rstrip("/")
    ):
        raise MagpieSupportError("magpie Codex provider is not the lab loopback /v1 gateway")


def _snapshot_codex_config(path: Path) -> Tuple[bool, Optional[bytes]]:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise MagpieSupportError("existing Codex config is not a regular file; refusing to replace it")
    if not path.exists():
        return False, None
    try:
        return True, path.read_bytes()
    except OSError:
        raise MagpieSupportError("existing Codex config cannot be read; refusing to replace it") from None


def _current_config_snapshot(path: Path) -> Tuple[bool, Optional[bytes]]:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise MagpieSupportError("Codex config changed to an unsupported file during setup")
    if not path.exists():
        return False, None
    return True, path.read_bytes()


def _restore_codex_config_if_unchanged(
    path: Path,
    existed: bool,
    original: Optional[bytes],
    post_existed: bool,
    post: Optional[bytes],
) -> bool:
    try:
        current_existed, current = _current_config_snapshot(path)
        if current_existed != post_existed or current != post:
            return False
        if not existed:
            if current_existed:
                path.unlink()
            return True
        if original is None:
            return False
        _atomic_write_bytes(path, original)
        return True
    except (OSError, MagpieSupportError):
        return False


def _clean_models(models: Iterable[str]) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for item in models:
        model = str(item).strip()
        if not model:
            continue
        if "," in model or any(character.isspace() for character in model):
            raise MagpieSupportError("model ids passed to provider add must not contain commas or whitespace")
        if model not in seen:
            seen.add(model)
            cleaned.append(model)
    if not cleaned:
        raise MagpieSupportError("at least one explicit CPR model id is required")
    return cleaned


def prepare(key: str, models: Iterable[str], rawmodel: str) -> Dict[str, Any]:
    """Prepare the isolated Magpie gateway and Codex provider.

    ``key`` is never placed in an argv, environment variable, return value,
    log, or exception.  It only replaces the sentinel key in the generated
    provider template.
    """
    if not isinstance(key, str) or not key.strip():
        raise MagpieSupportError("a non-empty CPR API key is required")
    model_ids = _clean_models(models)
    selected = str(rawmodel).strip()
    if not selected:
        raise MagpieSupportError("a raw model id is required")
    if selected not in model_ids:
        raise MagpieSupportError("selected model is not in the explicit CPR model list")

    paths = _runtime_layout()
    gateway_url = GATEWAY_URL
    binary = ensure_magpie()
    _ensure_not_portable_runtime(binary)
    env = _environment(paths, gateway_url, block_remote=False)
    version = _version(binary, env)

    healthy = _gateway_healthy(binary, env)
    daemon: Dict[str, Any] = {
        "gateway_healthy": healthy,
        "daemon_installed": None,
        "action": "none",
    }
    if not healthy:
        daemon = _ensure_gateway(binary, paths, env, gateway_url)
    if not daemon.get("gateway_healthy"):
        raise MagpieSupportError("Magpie gateway is not healthy at %s" % gateway_url)

    template = _template_provider(binary, model_ids)
    providers_path = paths["xdg_config_home"] / "magpie" / "providers.json"
    document, changed = _merge_provider(template, key, providers_path)
    if changed:
        _atomic_write_json(providers_path, document)

    qualified_model = "%s/%s" % (CPR_PROVIDER_ID, selected)
    codex_config = paths["codex_home"] / "config.toml"
    config_existed, original_config = _snapshot_codex_config(codex_config)
    configure = _run([str(binary), "codex", qualified_model], env, timeout=_SETUP_TIMEOUT)
    try:
        post_existed, post_config = _current_config_snapshot(codex_config)
    except (OSError, MagpieSupportError):
        post_existed, post_config = False, None
    try:
        if configure.returncode != 0:
            raise MagpieSupportError("magpie codex configuration failed")
        _validate_codex_config(codex_config, qualified_model, gateway_url)
    except MagpieSupportError:
        _restore_codex_config_if_unchanged(
            codex_config,
            config_existed,
            original_config,
            post_existed,
            post_config,
        )
        raise

    return {
        "magpie": str(binary),
        "version": version,
        "provider_file": str(providers_path),
        "provider_id": CPR_PROVIDER_ID,
        "models": model_ids,
        "qualified_model": qualified_model,
        "gateway_url": gateway_url,
        "codex_home": str(paths["codex_home"]),
        "codex_config": str(codex_config),
        "gateway_healthy": bool(daemon.get("gateway_healthy", healthy)),
        "daemon_action": str(daemon.get("action", "none")),
    }


__all__ = [
    "CPR_BASE_URL",
    "CPR_PROVIDER_ID",
    "GATEWAY_URL",
    "MagpieSupportError",
    "ensure_daemon",
    "ensure_magpie",
    "find_magpie",
    "install_magpie",
    "prepare",
]
