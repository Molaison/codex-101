# codex-101 · 实验室 Codex 安装器

在自己的 Windows、macOS 或 Linux 电脑上，用个人 CPR Key 一次安装官方 Codex 和 Magpie，并连接实验室 CPR。无需 SSH 到维护机，也无需安装 Node.js/npm 或手写配置。

> [v0.2.1 preview 已发布](https://github.com/Molaison/codex-101/releases/tag/v0.2.1)，源码版本为 `ff893e7`；以下 main 入口选择附件完整的公开 Release（稳定版优先，否则取最新预发布），并打印选中的 tag 与原因。[v0.2.0 preview](https://github.com/Molaison/codex-101/releases/tag/v0.2.0) 是历史版本。

## 安装并开始使用

先向 **@曾正鹏** 申请个人 CPR Key，并确认该 Key 同时具备 `gpt-6.1-sol` 和 `DeepSeek-V4.1-Flash` 的访问权限。只在安装器的隐藏输入提示中输入，不要放进命令参数、聊天、截图或 Git。

**Windows PowerShell**：

```powershell
& ([scriptblock]::Create((Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Molaison/codex-101/main/install.ps1').Content)) -Magpie
```

**macOS / Linux 终端**（需要 `curl` 和 Python 3.8+）：

```bash
(set -o pipefail; curl --http1.1 -fsSL --retry 2 https://raw.githubusercontent.com/Molaison/codex-101/main/install.sh | bash -s -- --magpie)
```

Linux 共享 Lustre HOME：在每个使用 Codex 的节点各执行一次上述命令。各节点需要可用的 systemd 用户服务，并各自运行本地 Magpie；安装和配置种子由共享 HOME 提供。详见 [共享集群说明](docs/installation.md#共享-lustre-home)。

Windows 安装器会在用户目录准备 Python。入口优先选择含完整安装附件的稳定 Release；没有稳定版时选择预发布，载荷保持同一 tag。首次安装还需要访问 GitHub、npm 和 CPR。

只有看到 **`[READY]` 且安装成功退出**，才表示当前连接的真实工具自检通过。然后打开新终端，进入自己的项目目录，运行 `codex`。日常启动不需要重跑安装器。

已有其他 `codex` 抢占命令名时，使用独立入口，不必卸载旧版：

- macOS / Linux：`~/.local/share/codex-lab/bin/codex`
- Windows：`%LOCALAPPDATA%\CodexLab\bin\codex.cmd`

## 可选：安装 Pi

macOS / Linux 可用以下命令配置独立的 Pi 入口，直接连接实验室 CPR：

```bash
(set -o pipefail; curl --http1.1 -fsSL --retry 2 https://raw.githubusercontent.com/Molaison/codex-101/main/install-pi.sh | bash)
```

完成后运行 `~/.local/bin/pi-cpr`。脚本复用已有 Pi，否则调用 pi.dev 官方安装器；Pi 需要 Node.js 22.19+，缺失时由官方安装器处理。它核验 CPR Key 和 Sol 权限，默认 `gpt-6.1-sol` / `high`，独立于 Magpie 和原有 `~/.pi`。若 Key 可见 Flash，可在 Pi 中用 `/model` 切换；Pi 的 `off` thinking 表示不使用推理。Pi 1.4.0 的独立安装配置以及 Sol / `high`、Flash / `off` 真实文件读取均已通过。详见 [Pi 配置说明](docs/installation.md#可选pi)。

## 使用前必须知道

- **执行权限很大**：实验室预设为 `danger-full-access` 和 `approval_policy=never`。Codex 能修改当前用户有权限访问的文件，包括共享目录，不会逐项询问执行批准。先备份重要数据，并从正确的项目目录启动。
- **默认连接**：`https://cpr.molaisonz.dpdns.org/v1`，通过 Magpie 连接 CPR，默认模型为 `gpt-6.1-sol`（`high`）；自检使用 `DeepSeek-V4.1-Flash`（`none`），不改变保存的默认模型。Key 不跨服务地址复用。模型菜单会把 CPR 的 V1 维护目录与当前官方 Codex 内置目录合并，同 ID 时 CPR 条目优先，保留原生模型且不重标协议；菜单可见不代表 CPR Key 已获该模型权限，所有模型请求仍走当前 CPR endpoint。
- **隐私边界**：`temp` 表示 Web 临时会话，不保存进 Web 账号聊天历史；不等于服务端零留存。`persist` 保存 Web 账号聊天历史，必须主动选择。两者均与 Codex 本地 sessions 分开。
- **SQLite 不会自动合并**：预检失败时不迁移数据库；通过后默认尝试迁移到本机临时目录。重试时仅在空闲且副本完全一致时复用，否则保留原库；不同库不覆盖、不混合。节点本地 SQLite 历史不会跨节点自动迁移或合并，共享 HOME 中的 sessions 保留。若目标存在不同数据、旧库仍可能被写入或复制期间有变化，则保留原配置目录和原库并打印 `[SQLITE_PRESERVED]`，可继续使用原库；不会强杀进程。不要删除或混拼数据库及 `-wal` / `-shm` 文件；临时目录也不能作为唯一长期备份。

[完整安装与模型说明](docs/installation.md) · [SQLite 选择与恢复](docs/03_upgrade_rollback.md#sqlite-冲突先保留再选择)

## 查看状态、升级、恢复

v0.2.0 新增 `codex101` 维护入口。先查看状态，再按目的选择操作，不要把下面所有命令依次执行。

macOS / Linux 先查看状态（`--root` 指向本次安装目录）：

```bash
root="${CODEX_HOME:-$HOME/.codex}/provider-connect"
python3 "$root/install_lifecycle.py" --root "$root" status
```

需要主动升级安装器和官方 Codex 时：

```bash
python3 "$root/install_lifecycle.py" --root "$root" upgrade
```

仅在安装中断、终端提示恢复原事务时：

```bash
python3 "$root/install_lifecycle.py" --root "$root" recover
```

Windows 命令、仅升级 Codex、离线回滚及路径说明见 [升级与恢复手册](docs/03_upgrade_rollback.md)。v0.1.x 没有完整维护入口，需先用上方安装命令更新。

**两个版本要分开看**：`v0.2.1` 是本次安装器版本；官方 Codex 的实际版本见 `status` 输出中的 `codex_version`。首次安装选择 npm 的最新稳定 Codex，后续复用完好的已安装副本；显式 `upgrade` 才主动查询升级。安装器是 preview，不影响官方客户端使用 stable 渠道。

安装失败先看终端 `[NEXT]`。预检失败（`NETWORK` / `ENDPOINT`）时，先跑终端给出的那条 `python3 …/codex_provider.py preflight`：它只重测连接，带 `reason` 帮助区分超时、连接、证书或网关响应等问题。`RECOVERY_REQUIRED` 表示无法安全自动继续，例如不能确定真实回合是否已经提交；保留日志，不要盲目 `--repair`、删除事务记录或重复发起任务。`UPSTREAM_READINESS` 通常需要维护者处理上游，反复安装不会修好服务端。

## 验证范围

- **自动测试**：配置、缓存、SQLite 保留、事务恢复、回滚和启动器合约，包含替身与隔离测试。
- **官方客户端 smoke test**：CI 在 Windows、macOS、Ubuntu 下载经 npm SHA512 校验的 stable 包并运行版本检查；不使用 CPR Key，不调用模型。
- **真实服务验收**：必须由最终安装的客户端，用对应地址和个人 Key 完成真实文件读取、内容匹配及成功回合。`/models`、下载校验、`--version` 和 CI 通过均不能替代它。

本次运行验收中，f102 和 f100 各自通过 Sol / `high` 真实文件读取；f100 验收时 f102 测试守护进程已停止。两节点的原生安装自检均返回 `[READY]`，以 DeepSeek Flash / `none` 完成真实读取，保存的默认值仍为 Sol / `high`；f102 的 `--magpie --reconfigure` 使用官方 Codex 0.164.0 并成功退出。该安装测试复用了官方客户端，并使用临时 SQLite 路径和私下注入的 Key；网络、配置和自检均为真实操作。

Pi 1.4.0 的隔离安装配置、Sol 模型列表及 Sol / `high`、Flash / `off` 真实文件读取均已通过。[main CI](https://github.com/Molaison/codex-101/actions/runs/40630318869) 的六项检查均已通过。[Release 工作流](https://github.com/Molaison/codex-101/actions/runs/40630372806) 的七项检查均已通过，包括 preview 发布。

本项目演进自 [codex-provider-connect](https://github.com/Molaison/codex-provider-connect)；`codex_provider.py` 保留兼容入口。本文面向 `--magpie` 实验室预设。
