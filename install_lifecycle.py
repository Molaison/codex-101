#!/usr/bin/env python3
"""Versioned, recoverable codex-101 payload installation and offline rollback.

The official Codex executable is managed separately by codex_provider.py; this
module never downloads a client, rotates credentials, or edits SQLite data.
"""

import argparse
import ast
from contextlib import contextmanager
import hashlib
import runpy
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


PAYLOAD = ("codex_provider.py", "model_catalog.json", "install_lifecycle.py")
RELEASE_PATTERN = re.compile(r"v\d+\.\d+\.\d+")
DISPATCHER = '''#!/usr/bin/env python3
# codex-101 managed dispatch; do not edit in place.
import json, runpy
from pathlib import Path
root = Path(__file__).resolve().parent
record = json.loads((root / "active.json").read_text(encoding="utf-8"))
runpy.run_path(str(root / "releases" / record["active"] / "codex_provider.py"), run_name="__main__")
'''


def root_path():
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "codex-provider-connect"
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "provider-connect"


def connection_state_root():
    # The Windows bootstrap payload lives in LOCALAPPDATA, while credentials,
    # checks and official Codex binaries live under CODEX_HOME/provider-connect.
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "provider-connect"


def recovery_command(root, action):
    """Print a locally runnable command, including the right private install path."""
    manager = Path(root) / "install_lifecycle.py"
    if os.name == "nt":
        python = Path(os.environ.get("CODEX_CONNECT_PYTHON",
                                    str(Path(root) / "python" / "python.exe")))
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        return "& %s -X utf8 %s --root %s %s" % (quote(python), quote(manager), quote(root), action)
    return "python3 %s --root %s %s" % (shlex.quote(str(manager)), shlex.quote(str(root)), action)


def print_next_step(root):
    """Never prescribe a new model submission before checking the saved transaction."""
    root = Path(root)
    if not (root / "install_lifecycle.py").is_file():
        return
    print("[NEXT] 本地状态（不提交模型请求）：%s" % recovery_command(root, "status"), file=sys.stderr)
    pending = root / "pending.json"
    if not pending.is_file():
        return
    try:
        pointer = read_json(pending)
        journal = read_json(root / "transactions" / pointer["id"] / "journal.json")
    except (OSError, ValueError, KeyError):
        print("[NEXT] 安装日志无法读取，先检查 %s；不要删除 pending.json。" % pending, file=sys.stderr)
        return
    if journal.get("stage") == "RECOVERY_REQUIRED":
        print("[NEXT] 事务处于 RECOVERY_REQUIRED；请检查 %s，勿直接重复真实模型回合。" %
              (root / "transactions" / pointer["id"] / "journal.json"), file=sys.stderr)
    else:
        print("[NEXT] 继续相同事务：%s" % recovery_command(root, "recover"), file=sys.stderr)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@contextmanager
def install_lock(root):
    """Kernel lock: a killed installer cannot strand a stale owner marker."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "install.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            stream.write(b" ")
            stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError("另一个安装、升级或回滚仍在运行；未修改版本。") from None
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("另一个安装、升级或回滚仍在运行；未修改版本。") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def validate_payload(source, release):
    source = Path(source)
    manifest = read_json(source / "release_manifest.json")
    if (manifest.get("schema") != 1 or manifest.get("release") != release or
            manifest.get("codex_channel") != "stable" or not RELEASE_PATTERN.fullmatch(release)):
        raise ValueError("发布 manifest 的版本、schema 或 Codex channel 不一致。")
    hashes = manifest.get("files")
    if not isinstance(hashes, dict) or any(not isinstance(hashes.get(name), str)
                                           or not re.fullmatch(r"[0-9a-f]{64}", hashes[name]) for name in PAYLOAD):
        raise ValueError("发布 manifest 缺少受保护文件的 SHA256。")
    for name in PAYLOAD:
        path = source / name
        if not path.is_file() or path.is_symlink() or digest(path) != hashes[name]:
            raise ValueError("发布组件 %s 缺失或 SHA256 不匹配；未激活。" % name)
    return manifest


def prepare_release(root, source, release):
    validate_payload(source, release)
    releases = root / "releases"
    if releases.is_symlink():
        raise ValueError("安装版本根目录是符号链接；拒绝跨目录更新。")
    releases.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = releases / release
    if target.exists() or target.is_symlink():
        if not target.is_dir() or target.is_symlink():
            raise ValueError("版本目录不是安装器拥有的普通目录；未覆盖。")
        validate_payload(target, release)
        return target
    stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=str(releases)))
    try:
        for name in PAYLOAD + ("release_manifest.json",):
            shutil.copyfile(Path(source) / name, stage / name)
            (stage / name).chmod(0o600)
        validate_payload(stage, release)
        os.rename(stage, target)  # Same filesystem; never replace a published version.
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return target


def ensure_recovery_manager(root, release_dir):
    path = root / "install_lifecycle.py"
    if path.is_symlink():
        raise ValueError("本地恢复管理器是符号链接；拒绝覆盖。")
    if path.exists():
        if not path.is_file() or "Versioned, recoverable codex-101" not in path.read_text(encoding="utf-8"):
            raise ValueError("安装目录已有无关恢复管理器；未覆盖。")
        return
    fd, pending = tempfile.mkstemp(prefix=".recovery-manager-", dir=str(root))
    os.close(fd)
    try:
        shutil.copyfile(release_dir / "install_lifecycle.py", pending)
        os.chmod(pending, 0o700)
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def legacy_state(root):
    """Import v0.1.x flat payload without touching its key, config or DB."""
    helper = root / "codex_provider.py"
    catalog = root / "model_catalog.json"
    if helper.is_symlink() or catalog.is_symlink():
        raise ValueError("旧安装组件是符号链接；拒绝将不明来源文件纳入回滚。")
    if helper.exists() and not helper.is_file():
        raise ValueError("现有 helper 不是普通文件；未覆盖。")
    if helper.is_file() and "codex-101 managed dispatch" in helper.read_text(encoding="utf-8"):
        raise ValueError("发现受管理启动器但 active.json 缺失；保留现有安装，请恢复状态记录。")
    if helper.is_file() and "codex-101 managed dispatch" not in helper.read_text(encoding="utf-8"):
        if "codex-provider-connect" not in helper.read_text(encoding="utf-8"):
            connection = connection_state_root() / "connection.json"
            if not connection.is_file() or read_json(connection).get("integration") != "native":
                raise ValueError("发现不属于本安装器的 codex_provider.py；拒绝覆盖。")
    if not helper.is_file() or not catalog.is_file() or "codex-101 managed dispatch" in helper.read_text(encoding="utf-8"):
        return None
    try:
        ast.parse(helper.read_text(encoding="utf-8"))
        models = read_json(catalog)["models"]
        if not isinstance(models, list) or not models:
            return None
    except (SyntaxError, OSError, ValueError, KeyError):
        # A damaged legacy helper may be repaired, but is not a rollback candidate.
        return None
    name = "legacy-" + digest(helper)[:12]
    target = root / "releases" / name
    if not target.exists():
        target.mkdir(parents=True, mode=0o700)
        for item in (helper, catalog):
            shutil.copyfile(item, target / item.name)
            (target / item.name).chmod(0o600)
        private_json(target / "legacy-manifest.json", {"files": {item.name: digest(item) for item in (helper, catalog)}})
    verify_release(root, name)
    return {"active": name, "previous": None, "last_ready": None, "status": "LEGACY"}


def verify_release(root, name):
    directory = root / "releases" / name
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("离线版本目录不存在或不是安全目录：%s" % name)
    if RELEASE_PATTERN.fullmatch(name):
        validate_payload(directory, name)
    elif re.fullmatch(r"legacy-[0-9a-f]{12}", name):
        manifest = read_json(directory / "legacy-manifest.json")
        for basename, expected in manifest["files"].items():
            if basename not in ("codex_provider.py", "model_catalog.json") or digest(directory / basename) != expected:
                raise ValueError("旧安装文件被修改；拒绝回滚。")
    else:
        raise ValueError("无效的版本目录名称。")
    return directory


def snapshot(tx, name, path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("受管理配置是符号链接；拒绝覆盖。")
    result = {"path": str(path), "exists": path.is_file(), "before": None, "after": None}
    if result["exists"]:
        result["before"] = digest(path)
        shutil.copyfile(path, tx / (name + ".before"))
        (tx / (name + ".before")).chmod(0o600)
    return result


def after_snapshot(record):
    path = Path(record["path"])
    record["after"] = digest(path) if path.is_file() and not path.is_symlink() else None


def can_restore(record):
    path = Path(record["path"])
    if path.is_symlink():
        return False
    current = digest(path) if path.is_file() and not path.is_symlink() else None
    return current == record["after"]


def restore_file(tx, name, record):
    if Path(record["path"]).is_symlink():
        raise ValueError("受管理文件现为符号链接；拒绝覆盖。")
    if record["before"] == record["after"]:
        return
    if record["exists"]:
        destination = Path(record["path"])
        fd, pending = tempfile.mkstemp(prefix=".rollback-", dir=str(destination.parent))
        os.close(fd)
        try:
            shutil.copyfile(tx / (name + ".before"), pending)
            os.chmod(pending, 0o600)
            os.replace(pending, destination)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
    else:
        Path(record["path"]).unlink(missing_ok=True)


def running_check(root):
    pointer = connection_state_root() / "checks" / "current.json"
    if pointer.is_file():
        try:
            path = Path(read_json(pointer)["directory"]) / "state.json"
            # Missing state cannot establish that a previously submitted turn ended.
            return not path.is_file() or read_json(path).get("status") not in ("terminal", "worker_error")
        except (OSError, ValueError, KeyError):
            return True
    return False


def codex_busy():
    """Conservatively postpone config rollback while the user's CLI is running."""
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq codex.exe", "/FO", "CSV", "/NH"],
                                capture_output=True, text=True, check=True)
        return '"codex.exe"' in result.stdout.lower()
    result = subprocess.run(["ps", "-u", str(os.getuid()), "-o", "comm="],
                            capture_output=True, text=True, check=True)
    return any(re.search(r"(^|/)(codex|codex-cli)(-.*)?$", line.strip()) for line in result.stdout.splitlines())


def install_dispatch(root, release_dir):
    # One stable compatibility entry; model catalog is read from the selected release.
    target = root / "codex_provider.py"
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("已有 codex_provider.py 不属于本安装器；未覆盖。")
    if target.is_file():
        existing = target.read_text(encoding="utf-8")
        if "codex-101 managed dispatch" not in existing and "codex-provider-connect" not in existing:
            connection = connection_state_root() / "connection.json"
            if not connection.is_file() or read_json(connection).get("integration") != "native":
                raise ValueError("已有 codex_provider.py 不属于本安装器；未覆盖。")
    fd, temp = tempfile.mkstemp(prefix=".dispatch-", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(DISPATCHER)
        os.chmod(temp, 0o700)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    # Installed management entry works offline even when the original bootstrap URL fails.
    manager = root / "install_lifecycle.py"
    if manager.is_symlink() or (manager.exists() and "Versioned, recoverable codex-101" not in manager.read_text(encoding="utf-8")):
        raise ValueError("现有恢复管理器不是本安装器拥有的文件；未覆盖。")
    fd, temp = tempfile.mkstemp(prefix=".manager-", dir=str(root))
    os.close(fd)
    try:
        shutil.copyfile(release_dir / "install_lifecycle.py", temp)
        os.chmod(temp, 0o700)
        os.replace(temp, manager)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def ensure_maintenance_launcher(root):
    if os.name == "nt":
        destination = Path(os.environ["LOCALAPPDATA"]) / "CodexLab" / "bin" / "codex101.cmd"
        python = Path(os.environ.get("CODEX_CONNECT_PYTHON", str(root / "python" / "python.exe")))
        content = '@rem codex-101 managed maintenance launcher\n@"%s" -X utf8 "%s" %%*\n' % (str(python).replace("%", "%%"), str(root / "install_lifecycle.py").replace("%", "%%"))
    else:
        destination = Path.home() / ".local" / "share" / "codex-lab" / "bin" / "codex101"
        content = '#!/bin/sh\n# codex-101 managed maintenance launcher\nexec python3 "%s" "$@"\n' % str(root / "install_lifecycle.py")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and "codex-101 managed maintenance launcher" not in destination.read_text(encoding="utf-8"):
        print("[WARNING] codex101 命令名已被其他程序占用；请使用安装目录内 install_lifecycle.py 维护。", file=sys.stderr)
        return
    fd, pending = tempfile.mkstemp(prefix=".codex101-", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.chmod(pending, 0o700)
        os.replace(pending, destination)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def activate_transaction(root, tx, record):
    """Finish a previously recorded decision; never re-run the model turn."""
    release = verify_release(root, record["to"])
    if record["stage"] != "ACTIVATING":
        raise ValueError("没有可提交的安装事务状态。")
    # Existing dispatchers keep pointing to the old active version until commit.
    install_dispatch(root, release)
    private_json(root / "active.json", record["new_state"])
    ensure_maintenance_launcher(root)
    record["stage"] = record["new_state"]["status"]
    private_json(tx / "journal.json", record)
    (root / "pending.json").unlink(missing_ok=True)
    if record["new_state"]["status"] == "READY":
        print("[READY] 已验证 codex-101 %s；旧版仍保留，可离线回滚。" % record["to"])
    else:
        print("[NOT_READY] 已保存本地组件；服务验收状态为 %s。未自动回滚。" % record["new_state"]["status"], file=sys.stderr)
    return record["return_code"]


def completed_check(root, record, release):
    """Read an already accepted native turn without ever starting another one."""
    pointer = connection_state_root() / "checks" / "current.json"
    if not pointer.is_file() or digest(pointer) == record.get("check_before"):
        return None
    identity = read_json(pointer)
    connection_file = connection_state_root() / "connection.json"
    connection = read_json(connection_file) if connection_file.is_file() else {}
    if not identity.get("native") or identity.get("url") != connection.get("url"):
        raise ValueError("自检回合与安装事务的连接不匹配；保留现场，拒绝重新提交。")
    requested = record["args"]
    if "--model" in requested and read_json(Path(identity["directory"]) / "state.json").get("model") != requested[requested.index("--model") + 1]:
        raise ValueError("自检模型发生变化；保留现场，拒绝重新提交。")
    checker = runpy.run_path(str(release / "codex_provider.py"), run_name="codex101_read_only_receipt").get("check_result")
    if checker is None:
        raise ValueError("安装助手无法只读核验已完成的真实回合；保留现场。")
    receipt = checker(Path(identity["directory"]))
    if receipt["status"] == "running":
        return None
    if receipt.get("endpoint") != identity["url"]:
        raise ValueError("已完成的自检地址不匹配；保留现场，拒绝重新提交。")
    return (0 if receipt["status"] == "passed" else 1), receipt


def finish_failed_setup(root, tx, record):
    """Finish a journaled local compensation without rerunning native setup."""
    if record["stage"] != "COMPENSATING":
        raise ValueError("安装回退事务状态不匹配。")
    for item in record["snapshots"].values():
        path = Path(item["path"])
        current = digest(path) if path.is_file() and not path.is_symlink() else None
        if current not in (item["before"], item["after"]):
            record["stage"] = "RECOVERY_REQUIRED"
            private_json(tx / "journal.json", record)
            print("[RECOVERY_REQUIRED] 安装失败后发现外部配置修改，未覆盖。", file=sys.stderr)
            return 2
    for name, item in record["snapshots"].items():
        path = Path(item["path"])
        current = digest(path) if path.is_file() and not path.is_symlink() else None
        if current != item["before"]:
            restore_file(tx, name, item)
    record["stage"] = "ROLLED_BACK_LOCAL"
    private_json(tx / "journal.json", record)
    (root / "pending.json").unlink(missing_ok=True)
    print("[NOT_READY] 本地安装失败，已恢复旧配置；旧版本仍为 active。", file=sys.stderr)
    return record.get("return_code", 1) or 1


def setup_transaction(root, tx, record):
    journal = tx / "journal.json"
    if record["stage"] == "ACTIVATING":
        return activate_transaction(root, tx, record)
    if record["stage"] == "COMPENSATING":
        return finish_failed_setup(root, tx, record)
    release = verify_release(root, record["to"])
    if record["stage"] in ("SETUP_RUNNING", "PENDING_VERIFY") and running_check(root):
        print("[PENDING] 真实工具回合仍在运行；没有重新启动安装或重复提交。", file=sys.stderr)
        return 2
    prior_stage = record["stage"]
    terminal = (completed_check(root, record, release) if prior_stage in ("SETUP_RUNNING", "PENDING_VERIFY") else None)
    if terminal is None and prior_stage in ("SETUP_RUNNING", "PENDING_VERIFY"):
        # Once a native setup has started, loss of its receipt is ambiguous.
        # Never resubmit a potentially accepted model turn after a crash.
        record["stage"] = "RECOVERY_REQUIRED"
        private_json(journal, record)
        print("[RECOVERY_REQUIRED] 无法证明之前的工具回合未提交；保留现场，不重复发送。", file=sys.stderr)
        return 2
    record["stage"] = "SETUP_RUNNING"
    if prior_stage == "PREPARED":
        pointer = connection_state_root() / "checks" / "current.json"
        record["check_before"] = digest(pointer) if pointer.is_file() else None
    private_json(journal, record)
    options = list(record["args"])
    old = record["from"]
    if old and old["active"] != record["to"] and "--repair" not in options and "--skip-check" not in options:
        options.append("--repair")  # A new release needs a fresh native tool-turn acceptance.
    returncode = (terminal[0] if terminal is not None else
                  subprocess.call([sys.executable, str(release / "codex_provider.py"), "_native-setup"] + options))
    for item in record["snapshots"].values():
        after_snapshot(item)
    if returncode == 2 and running_check(root):
        record["stage"] = "PENDING_VERIFY"
        private_json(journal, record)
        print("[PENDING] 原真实自检仍在运行，重跑安装命令或 codex101 recover 查询同一回合。", file=sys.stderr)
        return 2
    report_file = connection_state_root() / "native-installation.json"
    report = (terminal[1] if terminal is not None else
              read_json(report_file) if report_file.is_file() and digest(report_file) != record.get("report_before")
              else {})  # Never classify a new local failure using a stale upstream error.
    code = report.get("code")
    old = record["from"]
    if returncode == 2 and code in ("SERVER_BUSY", "SERVER_RESTART_REQUIRED", "SERVER_CHANGED", "SERVER_RESTART_FAILED"):
        record["stage"] = "PENDING_RESTART"
        private_json(journal, record)
        print("[PENDING_RESTART] 新配置需要原 Codex server 空闲后刷新；旧版本仍为 active。", file=sys.stderr)
        return 2
    if returncode != 0 and code not in ("UPSTREAM_READINESS", "AUTH", "ACCESS", "BUSY", "NETWORK", "ENDPOINT", "MODEL_CHECK", "UPSTREAM_COMPAT"):
        if old and "--reconfigure" not in record["args"] and all(can_restore(item) for item in record["snapshots"].values()):
            record["stage"] = "COMPENSATING"
            record["return_code"] = returncode
            private_json(journal, record)
            return finish_failed_setup(root, tx, record)
        else:
            record["stage"] = "RECOVERY_REQUIRED"
        private_json(journal, record)
        print("[NOT_READY] 本地安装未通过；旧版本未切换，配置状态需人工核查。", file=sys.stderr)
        return returncode or 1
    status = "READY" if returncode == 0 else "UPSTREAM_UNAVAILABLE" if code == "UPSTREAM_READINESS" else "NOT_VERIFIED"
    repeated = old and old["active"] == record["to"]
    changed = any(item["before"] != item["after"] for item in record["snapshots"].values())
    codex_upgrade = "--upgrade-codex" in record["args"] and changed
    state = {"active": record["to"],
             "previous": old.get("previous") if repeated and not codex_upgrade else old.get("active") if old else None,
             "last_ready": record["to"] if returncode == 0 else old.get("last_ready") if old else None,
             "status": status, "rollback_id": old.get("rollback_id") if repeated and not codex_upgrade else record["id"],
             "setup_args": [item for item in record["args"] if item != "--upgrade-codex"]}
    client_record = connection_state_root() / "client.json"
    if client_record.is_file():
        state["codex_version"] = read_json(client_record).get("version")
    record["stage"] = "ACTIVATING"
    record["return_code"] = returncode
    record["new_state"] = state
    private_json(journal, record)
    return activate_transaction(root, tx, record)


def install(root, args):
    with install_lock(root):
        pending = root / "pending.json"
        if pending.exists():
            pointer = read_json(pending)
            record = read_json(root / "transactions" / pointer["id"] / "journal.json")
            if record.get("stage") == "RECOVERY_REQUIRED":
                raise ValueError("上次安装涉及无法安全自动恢复的配置；保留现场，请先核查事务记录。")
            if record["to"] != args.release or record["args"] != args.setup:
                raise ValueError("存在不同的未完成安装事务；先执行 codex101 recover。")
            return setup_transaction(root, root / "transactions" / record["id"], record)
        release = prepare_release(root, args.payload_dir, args.release)
        ensure_recovery_manager(root, release)
        existing = read_json(root / "active.json") if (root / "active.json").is_file() else legacy_state(root)
        txid = secrets.token_hex(8)
        tx = root / "transactions" / txid
        tx.mkdir(parents=True, mode=0o700)
        codehome = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        record = {"id": txid, "to": args.release, "from": existing, "stage": "PREPARED", "args": args.setup,
                  "snapshots": {"config": snapshot(tx, "config", codehome / "config.toml"),
                                "client": snapshot(tx, "client", connection_state_root() / "client.json")}}
        if record["snapshots"]["client"]["exists"]:
            old_client = read_json(tx / "client.before")
            old_binary = Path(old_client.get("path", ""))
            if old_client.get("source") == "official-npm" and old_binary.is_file():
                # v0.1.x records lack a binary digest; preserve one now for offline rollback.
                record["previous_codex_sha256"] = digest(old_binary)
        old_report = connection_state_root() / "native-installation.json"
        record["report_before"] = digest(old_report) if old_report.is_file() else None
        private_json(tx / "journal.json", record)
        private_json(pending, record)
        return setup_transaction(root, tx, record)


def finish_rollback(root, transaction):
    """Resume a committed rollback intent; each file may already be restored."""
    state = transaction["state"]
    previous = state["previous"]
    old_release = verify_release(root, previous)
    tx = root / "transactions" / state["rollback_id"]
    record = read_json(tx / "journal.json")
    if "--reconfigure" in record.get("args", []):
        raise ValueError("此升级同时显式变更了凭据；软件回滚不自动恢复旧 Key 或认证配置。")
    for item in record["snapshots"].values():
        path = Path(item["path"])
        current = digest(path) if path.is_file() and not path.is_symlink() else None
        if current not in (item["before"], item["after"]):
            raise ValueError("安装后配置出现外部修改；拒绝覆盖。")
    client = record["snapshots"]["client"]
    previous_binary = None
    if client["exists"]:
        saved = read_json(tx / "client.before")
        previous_binary = Path(saved.get("path", ""))
        if (not previous_binary.is_file() or
                subprocess.run([str(previous_binary), "--version"], capture_output=True, timeout=15).returncode != 0):
            raise ValueError("旧官方 Codex 不可执行；未恢复版本指针。")
        expected_digest = saved.get("sha256") or record.get("previous_codex_sha256")
        if expected_digest and digest(previous_binary) != expected_digest:
            raise ValueError("旧官方 Codex 的 SHA256 与安装记录不同；拒绝回滚到损坏二进制。")
    for name, item in record["snapshots"].items():
        path = Path(item["path"])
        current = digest(path) if path.is_file() and not path.is_symlink() else None
        if current != item["before"]:
            restore_file(tx, name, item)
    if previous_binary:
        if os.name == "nt":
            launcher = Path(os.environ["LOCALAPPDATA"]) / "CodexLab" / "bin" / "codex.cmd"
            if launcher.is_file() and launcher.read_text(encoding="utf-8").startswith("@rem codex-provider-connect: lab official launcher"):
                content = '@rem codex-provider-connect: lab official launcher\n@"%s" %%*\n' % str(previous_binary).replace("%", "%%")
                fd, temporary = tempfile.mkstemp(prefix=".rollback-", dir=str(launcher.parent))
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(content)
                os.replace(temporary, launcher)
        else:
            launcher = Path.home() / ".local/share/codex-lab/bin/codex"
            if launcher.is_symlink():
                pending = launcher.with_name(".codex-rollback-" + secrets.token_hex(4))
                pending.symlink_to(previous_binary)
                os.replace(pending, launcher)
    new_state = {"active": previous, "previous": None,
                 "last_ready": record["from"].get("last_ready"), "status": "ROLLED_BACK_LOCAL",
                 "rollback_id": None, "setup_args": record["from"].get("setup_args", [])}
    client_record = connection_state_root() / "client.json"
    if client_record.is_file():
        new_state["codex_version"] = read_json(client_record).get("version")
    private_json(root / "active.json", new_state)
    if (old_release / "install_lifecycle.py").is_file():
        install_dispatch(root, old_release)
    (root / "rollback-pending.json").unlink(missing_ok=True)
    print("[ROLLED_BACK_LOCAL] 已离线恢复 %s；未改 Key、SQLite 或会话，尚未重新调用 CPR。" % previous)
    return 0


def rollback(root):
    with install_lock(root):
        if (root / "pending.json").exists():
            raise ValueError("安装事务未终结；先执行 codex101 recover，避免覆盖正在运行的自检。")
        pointer = root / "rollback-pending.json"
        if pointer.exists():
            return finish_rollback(root, read_json(pointer))
        state = read_json(root / "active.json")
        if state.get("status") == "ROLLED_BACK_LOCAL":
            print("[ALREADY_ROLLED_BACK] 当前已是回滚后的版本，无需重复操作。")
            return 0
        previous = state.get("previous")
        if not previous:
            raise ValueError("没有已保存的上一版本，不能离线回滚。")
        verify_release(root, previous)
        tx = root / "transactions" / state["rollback_id"]
        record = read_json(tx / "journal.json")
        if record["to"] != state["active"] or not record.get("from") or record["from"]["active"] != previous:
            raise ValueError("升级事务与版本指针不匹配；没有执行回滚。")
        if running_check(root) or codex_busy():
            print("[PENDING_RESTART] 存在活跃 Codex 或自检回合；空闲后再执行 codex101 rollback。")
            return 2
        if not all(can_restore(item) for item in record["snapshots"].values()):
            raise ValueError("配置或客户端记录已在安装后改变；拒绝覆盖用户后续修改。")
        transaction = {"state": state}
        private_json(pointer, transaction)  # Write-ahead intent before the first mutation.
        return finish_rollback(root, transaction)


def recover(root):
    with install_lock(root):
        rollback_record = root / "rollback-pending.json"
        if rollback_record.exists():
            if codex_busy() or running_check(root):
                print("[PENDING_RESTART] 活跃 Codex 未结束，暂不恢复回滚事务。")
                return 2
            return finish_rollback(root, read_json(rollback_record))
        pending = root / "pending.json"
        if not pending.exists():
            print("[NO_PENDING] 没有未完成安装事务。")
            return 0
        record = read_json(pending)
        tx = root / "transactions" / record["id"]
        saved = read_json(tx / "journal.json")
        if saved["stage"] == "RECOVERY_REQUIRED":
            print("[RECOVERY_REQUIRED] 配置状态不确定，已保留事务记录；未重装或覆盖用户数据。", file=sys.stderr)
            return 2
        if saved["stage"] not in ("PREPARED", "SETUP_RUNNING", "PENDING_VERIFY", "PENDING_RESTART", "ACTIVATING", "COMPENSATING"):
            raise ValueError("事务阶段未知；保留现场，未重置。")
        # The native worker itself owns the existing turn and prevents duplicate sends.
        return setup_transaction(root, tx, saved)


def download(url, destination):
    request = urllib.request.Request(url, headers={"User-Agent": "codex-101-installer/1"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=180) as response, Path(destination).open("wb") as output:
                shutil.copyfileobj(response, output)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt == 2 or isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code != 429:
                raise
            time.sleep(attempt + 1)


def upgrade(root, codex_only=False):
    state = read_json(root / "active.json")
    setup = [value for value in state.get("setup_args", ["--lab"]) if value not in ("--repair", "--reconfigure", "--upgrade-codex")]
    setup.append("--upgrade-codex")
    current = state["active"]
    if codex_only:
        if not RELEASE_PATTERN.fullmatch(current):
            raise ValueError("旧平铺安装没有 manifest；请先更新 codex-101 安装器。")
        directory, release = root / "releases" / current, current
        args = argparse.Namespace(payload_dir=directory, release=release, setup=setup)
        return install(root, args)
    request = urllib.request.Request("https://api.github.com/repos/Molaison/codex-101/releases?per_page=30",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "codex-101-installer/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        releases = json.load(response)
    required = set(PAYLOAD) | {"release_manifest.json", "install.sh", "install.ps1"}
    eligible = [r for r in releases if not r.get("draft") and RELEASE_PATTERN.fullmatch(r.get("tag_name", ""))
                and required <= {a.get("name") for a in r.get("assets", [])}]
    chosen = next((r for r in eligible if not r.get("prerelease")), eligible[0] if eligible else None)
    if not chosen:
        raise ValueError("没有带完整 manifest 的发布版本；可使用 codex101 upgrade --codex-only。")
    version = chosen["tag_name"]
    if (RELEASE_PATTERN.fullmatch(current) and
            tuple(map(int, version[1:].split("."))) < tuple(map(int, current[1:].split(".")))):
        raise ValueError("公开 stable 版本低于当前 preview；拒绝通过 upgrade 隐式降级，请使用 rollback。")
    if version == current:
        directory = root / "releases" / current
        args = argparse.Namespace(payload_dir=directory, release=current, setup=setup)
        return install(root, args)
    with tempfile.TemporaryDirectory(prefix="codex101-upgrade-") as directory:
        source = Path(directory)
        base = "https://github.com/Molaison/codex-101/releases/download/" + version + "/"
        for name in PAYLOAD + ("release_manifest.json",):
            download(base + name, source / name)
        args = argparse.Namespace(payload_dir=source, release=version, setup=setup)
        return install(root, args)


def main():
    parser = argparse.ArgumentParser(description="codex-101 的本地安装、恢复、离线回滚维护入口")
    parser.add_argument("--root", type=Path, default=root_path())
    commands = parser.add_subparsers(dest="command", required=True)
    install_cmd = commands.add_parser("install")
    install_cmd.add_argument("--release", required=True)
    install_cmd.add_argument("--payload-dir", required=True, type=Path)
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("--release", required=True)
    commands.add_parser("rollback")
    commands.add_parser("recover")
    commands.add_parser("status")
    commands.add_parser("doctor")
    upgrade_cmd = commands.add_parser("upgrade")
    upgrade_cmd.add_argument("--codex-only", action="store_true")
    args, rest = parser.parse_known_args()
    if args.command == "install":
        args.setup = rest[1:] if rest[:1] == ["--"] else rest
        result = install(args.root, args)
        if result:
            print_next_step(args.root)
        return result
    if rest:
        parser.error("unexpected arguments: %s" % rest)
    if args.command == "rollback":
        return rollback(args.root)
    if args.command == "verify":
        verify_release(args.root, args.release)
        print("[VERIFIED] 已缓存版本 %s 完整。" % args.release)
        return 0
    if args.command == "recover":
        return recover(args.root)
    if args.command == "upgrade":
        return upgrade(args.root, codex_only=args.codex_only)
    if args.command == "doctor":
        state = read_json(args.root / "active.json")
        release = verify_release(args.root, state["active"])
        return subprocess.call([sys.executable, str(release / "codex_provider.py"), "doctor", "--json"])
    state = read_json(args.root / "active.json") if (args.root / "active.json").is_file() else {"status": "LEGACY"}
    print(json.dumps(state, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print("[INSTALL_ERROR] %s" % error, file=sys.stderr)
        arguments = sys.argv[1:]
        if "--root" in arguments and arguments.index("--root") + 1 < len(arguments):
            root = Path(arguments[arguments.index("--root") + 1])
        else:
            custom = next((value.split("=", 1)[1] for value in arguments
                           if value.startswith("--root=")), None)
            root = Path(custom) if custom else root_path()
        print_next_step(root)
        sys.exit(1)
