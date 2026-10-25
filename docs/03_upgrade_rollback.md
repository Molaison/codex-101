# 升级、回滚与中断恢复

适用于 **codex-101 v0.2.0 preview 候选**，发布状态见 [README](../README.md)。v0.1.x 没有完整的版本事务管理器，先用 [README 安装命令](../README.md#安装并开始使用) 更新；公开附件以 [Releases](https://github.com/Molaison/codex-101/releases) 为准。

## 先区分两个版本

- **codex-101 版本**：安装器、Python 助手、模型目录和维护引擎的 tag，例如 `v0.2.0`。
- **官方 Codex 版本**：独立下载的 npm stable 客户端，记录在 `client.json` 和 `status` 的 `codex_version` 中。没有固定为历史的 `0.162.1`。

首次安装查询 npm `@openai/codex/latest`，校验稳定版本号与平台包 SHA512。普通重跑复用完好的已安装客户端；`upgrade` 主动选择最新 stable。旧官方副本保留于 `CODEX_HOME/provider-connect/official/`，用于符合条件的离线回滚。

本仓库 Release 选择优先 stable，无 stable 时才选 preview；若选出的 stable 比当前 preview 更旧，`upgrade` 会拒绝隐式降级，应按需使用 `rollback`。

本仓库的 preview / stable 与官方 Codex 的 stable 是两个发布渠道。`release_manifest.json` 中的 `codex_channel: stable` 不代表本安装器已是稳定版。

## 选择维护操作

| 操作 | 用途与边界 |
| --- | --- |
| `status` | 本地只读，显示已激活版本、上个版本、最近 READY 版本和记录的状态。当前不会列出未完成事务的完整阶段。 |
| `upgrade` | 查询本仓库 Release，并主动升级官方 Codex；需要联网及服务验收。 |
| `upgrade --codex-only` | 保留当前已验证 helper/catalog，只主动升级官方 Codex；仍需要 npm / CPR，不是离线命令。 |
| `recover` | 继续已记录的安装、配置补偿或回滚事务；不是强制重装，也不一定离线。 |
| `rollback` | 符合条件时恢复上一次软件配置与客户端，不访问 GitHub、npm 或 CPR。 |
| `doctor` | 输出脱敏连接诊断和已有自检结果；可能请求模型目录，不会主动发起新的真实工具回合。 |

`codex101` 是这些操作的启动入口，不是日常 Codex 客户端。已安装的启动器绑定安装目录；如果不在 PATH，可以使用下列 Python 命令。

### macOS / Linux

保持安装时相同的 `CODEX_HOME`；若当时用了自定义路径，请先设置回来。下面每行是独立操作，**不要整段顺序执行**。

```bash
root="${CODEX_HOME:-$HOME/.codex}/provider-connect"
python3 "$root/install_lifecycle.py" --root "$root" status
python3 "$root/install_lifecycle.py" --root "$root" upgrade
python3 "$root/install_lifecycle.py" --root "$root" upgrade --codex-only
python3 "$root/install_lifecycle.py" --root "$root" recover
python3 "$root/install_lifecycle.py" --root "$root" rollback
python3 "$root/install_lifecycle.py" --root "$root" doctor
```

维护启动器位于 `~/.local/share/codex-lab/bin/codex101`。

### Windows PowerShell

Windows 的安装组件在 `%LOCALAPPDATA%\codex-provider-connect`，凭据、checks 和官方客户端仍在 `CODEX_HOME\provider-connect`（未设置时为用户目录下的 `.codex\provider-connect`）。两者不要混淆。

```powershell
$root = Join-Path $env:LOCALAPPDATA 'codex-provider-connect'
$python = if ($env:CODEX_CONNECT_PYTHON) { $env:CODEX_CONNECT_PYTHON } else { Join-Path $root 'python\python.exe' }
$manager = Join-Path $root 'install_lifecycle.py'

# 选择所需的一行，不要依次执行全部维护操作
& $python -X utf8 $manager --root $root status
& $python -X utf8 $manager --root $root upgrade
& $python -X utf8 $manager --root $root upgrade --codex-only
& $python -X utf8 $manager --root $root recover
& $python -X utf8 $manager --root $root rollback
& $python -X utf8 $manager --root $root doctor
```

维护启动器位于 `%LOCALAPPDATA%\CodexLab\bin\codex101.cmd`。若安装时使用 `CODEX_CONNECT_PYTHON`，维护时也应指定同一个可用 Python。

`--root` 选择安装组件和事务目录，**不会替你更改 `CODEX_HOME`**。维护错误的根目录会读错状态，所以恢复命令必须保留正确路径。

## 安装中断后怎么做

1. 保存终端错误及 `[NEXT]` 命令。先运行该安装目录的 `status`；它反映已激活状态，不能单凭它判断是否有未完成事务。
2. 终端提示存在待恢复事务时，执行同目录的 `recover`。正在运行的自检保持原回合所有权；恢复激活或回滚时不重新发送已经完成的工具请求。
3. 若返回 `PENDING` / `PENDING_RESTART`，等待原自检或 Codex 任务结束，再恢复。不要杀进程、删除 `pending.json` 或反复加 `--repair`。
4. 若返回 `RECOVERY_REQUIRED`，停止自动恢复并保留日志。提交边界不明确、记录缺失或配置被外部修改，都可能需要人工核对。先确认原回合是否已发出，不能把“没有结果”当成“没有请求”。

`recover` 在尚未执行安装的 `PREPARED` 阶段可能继续配置并发起首次自检；它不是纯只读命令。`[NO_PENDING]` 只表示没有待恢复事务，不证明远端服务可用。

安装组件尚未准备好时，可能没有本地维护入口。按 `[NEXT]` 排除下载、Python 或参数错误，再重跑完整安装命令；不要凭空删除旧数据。

## SQLite 冲突：先保留，再选择

v0.1.1 遇到“SQLite 新旧目录均有数据”会硬中断，重复安装不能解决。v0.2.0 改为保留数据并继续配置：

- 默认源目录为配置中的 `sqlite_home`，未配置时为 `CODEX_HOME`。
- 源目录存在 `state_5.sqlite` 时，只有目标为空、未检测到 Codex 写入进程且复制前后文件大小/修改时间一致，才发布复制结果。
- 复制范围为 `state_5.sqlite`、`memories_1.sqlite`、`goals_1.sqlite`、`queue_1.sqlite` 及各自存在的 `-wal` / `-shm`；源文件保留。这是文件级复制，不是数据库内容合并，也不是事务级备份工具。
- 目标非空、检测到 Codex 进程或复制期间文件变化时，放弃迁移、继续沿用源库，打印 `[SQLITE_PRESERVED]`。两份数据均保留；发生目录冲突时还会打印选用另一目录的完整命令。

**想改用另一份库时，显式选择绝对路径。** 修改 README 安装命令：Bash 参数必须放在 `--lab` 后、末尾的 `)` 前，不能追加到括号外；PowerShell 参数可加在末尾：

- Bash：`--sqlite-home '/实际的/绝对路径'`
- PowerShell：`-SqliteHome 'C:\实际目录'`

显式路径必须是实际目录而非符号链接。优先级是命令参数、`CODEX_SQLITE_HOME`、自动迁移逻辑；显式选择会更新安装配置，**不会复制、合并、同步或删除另一目录**。如果指定新目录，它可以为空，因此不要只凭安装成功判断旧历史已迁入。

选择前确认哪份库包含需要的历史，先结束相关任务并备份。不要将不同数据库的主文件与 WAL/SHM 拼接；不要把系统临时目录视为长期备份。

## 升级与回滚会保留什么

升级在私有 `transactions/<id>/` 记录阶段、此前激活版本和配置快照，使用 `install.lock` 防止并发修改。载荷放入 `releases/<tag>/` 并按 SHA256 校验；旧平铺安装会归档为 `releases/legacy-*`。manifest 是与附件一起发布的完整性清单，**不是独立签名或发布者身份认证**。

新 tag 需要重新自检。服务端或账号失败时可以保留新的本地组件并记录 `UPSTREAM_UNAVAILABLE` / `NOT_VERIFIED`，不会把网络故障伪装成 READY，也不会自动降级。可安全补偿的本地安装失败才恢复先前配置。

离线 `rollback` 要求上一次版本、快照和官方客户端仍可验证；首次安装或缓存缺失时不保证可回滚。它会检查旧载荷 SHA256、客户端版本和已记录的二进制 SHA256：

- 有活跃 Codex 或未完成自检时，返回 `PENDING_RESTART`，不终止任务。
- 只恢复安装器改过的 `config.toml` / `client.json`。如果升级后被用户或其他进程修改，拒绝覆盖。
- 升级同时带 `--reconfigure` 更换凭据时，不自动恢复旧认证配置。
- 不复制或覆盖 Key、`connection.json`、SQLite、WAL/SHM、sessions，也不借回滚调整 Web 账号的 Memory 设置。
- 回滚前写 `rollback-pending.json`；途中中断可用同一个 `recover` 继续。

`ROLLED_BACK_LOCAL` 只证明本地恢复完成，不是一次新的服务端 `[READY]`。完整验收条件和 CI 边界见 [验收说明](01_cpr_acceptance.md)。
