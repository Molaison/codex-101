# codex-101 · 实验室 Codex 安装器

在自己的 Windows、macOS 或 Linux 电脑上，用个人 CPR Key 配置未经修改的官方 Codex。无需 SSH 到维护机，也无需安装 Node.js/npm 或手写配置。

> **v0.2.0 preview 候选，尚未发布**：本文说明候选源码中的功能；一键安装只使用已公开 Release，附件以 [Releases](https://github.com/Molaison/codex-101/releases) 为准。预发布不代表稳定版；CI 通过也不代表目标 HTTPS CPR 已通过真实工具验收。

## 安装并开始使用

先向 **@曾正鹏** 申请个人 CPR Key。只在安装器的隐藏输入提示中输入，不要放进命令参数、聊天、截图或 Git。

**Windows PowerShell**：

```powershell
& ([scriptblock]::Create((Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Molaison/codex-101/main/install.ps1').Content)) -Lab
```

**macOS / Linux 终端**（需要 `curl` 和 Python 3.8+）：

```bash
(set -o pipefail; curl --http1.1 -fsSL --retry 2 https://raw.githubusercontent.com/Molaison/codex-101/main/install.sh | bash -s -- --lab)
```

Windows 安装器会在用户目录准备 Python。入口优先选择含完整安装附件的稳定 Release；没有稳定版时选择预发布，载荷保持同一 tag。首次安装还需要访问 GitHub、npm 和 CPR。

只有看到 **`[READY]` 且安装成功退出**，才表示当前连接的只读文件工具自检通过。然后打开新终端，进入自己的项目目录，运行 `codex`。日常启动不需要重跑安装器。

已有其他 `codex` 抢占命令名时，使用独立入口，不必卸载旧版：

- macOS / Linux：`~/.local/share/codex-lab/bin/codex`
- Windows：`%LOCALAPPDATA%\CodexLab\bin\codex.cmd`

## 使用前必须知道

- **执行权限很大**：实验室预设为 `danger-full-access` 和 `approval_policy=never`。Codex 能修改当前用户有权限访问的文件，包括共享目录，不会逐项询问执行批准。先备份重要数据，并从正确的项目目录启动。
- **默认连接**：`https://cpr.molaisonz.dpdns.org/v1`，模型为 `chatgpt-web/gpt-6-tools-temp`。Key 不跨服务地址复用。
- **隐私边界**：`temp` 表示 Web 临时会话，不保存进 Web 账号聊天历史；不等于服务端零留存。`persist` 保存 Web 账号聊天历史，必须主动选择。两者均与 Codex 本地 sessions 分开。
- **SQLite 不会自动合并**：默认尝试迁移到本机临时目录。若目标已有数据、旧库仍可能被写入或复制期间有变化，则保留原库并打印 `[SQLITE_PRESERVED]`。不要删除或混拼数据库及 `-wal` / `-shm` 文件；临时目录也不能作为唯一长期备份。

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

**两个版本要分开看**：`v0.2.0` 是本安装器版本；官方 Codex 的实际版本见 `status` 输出中的 `codex_version`。首次安装选择 npm 的最新稳定 Codex，后续复用完好的已安装副本；显式 `upgrade` 才主动查询升级。安装器是 preview，不影响官方客户端使用 stable 渠道。

安装失败先看终端 `[NEXT]`。`RECOVERY_REQUIRED` 表示无法安全自动继续，例如不能确定真实回合是否已经提交；保留日志，不要盲目 `--repair`、删除事务记录或重复发起任务。`UPSTREAM_READINESS` 通常需要维护者处理上游，反复安装不会修好服务端。

## 验证范围

- **自动测试**：配置、缓存、SQLite 保留、事务恢复、回滚和启动器合约，包含替身与隔离测试。
- **官方客户端 smoke test**：CI 在 Windows、macOS、Ubuntu 下载经 npm SHA512 校验的 stable 包并运行版本检查；不使用 CPR Key，不调用模型。
- **真实服务验收**：必须由最终安装的客户端，用对应地址和个人 Key 完成真实文件读取、内容匹配及成功回合。`/models`、下载校验、`--version` 和 CI 通过均不能替代它。

仓库保留的历史成功记录只覆盖维护机内网 HTTP 的持久模型，不覆盖默认 temp、目标 HTTPS 或三平台独立用户环境。详见 [验收标准与历史证据](docs/01_cpr_acceptance.md)。当前测试结果请查看 [GitHub Actions](https://github.com/Molaison/codex-101/actions)，不要沿用旧版本的通过数量。

本项目演进自 [codex-provider-connect](https://github.com/Molaison/codex-provider-connect)；`codex_provider.py` 保留兼容入口。本文面向 `--lab` 实验室预设。
