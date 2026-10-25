# codex-101 · 实验室 Codex 安装与使用

**每位同学只需要自己的 CPR API Key。** 在 Windows、macOS、Linux **本机**运行安装命令；不用 SSH 到 ywl，不必手工写 `config.toml`，不用自己安装 Node.js/npm。

> **版本状态：公开的 v0.1.1 仍为旧安装器；main 中的 v0.2.0 是待发布候选**。以下 SQLite 自动保留和显式选库功能在 v0.2.0 发布后才对一键安装用户生效。安装器自动选择最新稳定 Release；没有稳定版时选择最新预发布版本，所有载荷锁定在同一 tag。仅收到 `[READY]` 且安装程序成功退出，才能认定**自己这台机器**的模型、凭据和真实工具调用已通过。管理员验证范围见 [测试状态](#测试状态)。

## 一键安装

先联系 **@曾正鹏** 获取个人 Key。Key 有个人配额，请保密。**不要**把 Key 发给聊天机器人、粘贴进命令行参数或保存到 Git。

**Windows（PowerShell）** — 将整行复制到 PowerShell：

```powershell
& ([scriptblock]::Create((Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Molaison/codex-101/main/install.ps1').Content)) -Lab
```

**macOS（终端 Terminal）**：

```bash
(set -o pipefail; curl --http1.1 -fsSL --retry 2 https://raw.githubusercontent.com/Molaison/codex-101/main/install.sh | bash -s -- --lab)
```

**Linux（终端）**：

```bash
(set -o pipefail; curl --http1.1 -fsSL --retry 2 https://raw.githubusercontent.com/Molaison/codex-101/main/install.sh | bash -s -- --lab)
```

输入个人 Key 时，终端不会显示输入内容。安装器会使用固定的 CPR 地址、在用户目录安装**未经修改的官方 Codex**、合并配置、保留旧客户端与会话，并进行**真实文件读取工具测试**。成功后打开新终端：

```bash
cd <你的项目目录>
codex
```

遇到已有定制版客户端抢占 `codex` 命令名时，使用安装器输出的实验室官方路径：Windows 为 `%LOCALAPPDATA%\CodexLab\bin\codex.cmd`，macOS/Linux 为 `~/.local/share/codex-lab/bin/codex`。已有配置/正在运行的任务不会被强行清理或覆盖。

## 默认配置

| 项目 | 本仓库默认行为 |
| --- | --- |
| 服务 | `https://cpr.molaisonz.dpdns.org/v1`（需要从当前网络访问该域名） |
| 模型 | `chatgpt-web/gpt-6-tools-temp`，**GPT-6 Web Tools Temporary** |
| 权限 | `danger-full-access`、`approval_policy=never`（按实验室要求，**不会询问执行批准**） |
| 稳定性 | **SSE**，HTTP 请求最多重试 **8 次**；SSE 中断最多重试 **12 次**；SSE 空闲超时 **15 分钟** |
| 存储 | 默认尝试使用本机私有临时 SQLite；若目标已有另一份数据或源库仍被占用，则继续沿用原目录，保留双方数据并输出选择命令；保留 `CODEX_HOME`、sessions |
| 凭据 | 独立私有 Key 文件，使用 `auth.command`；不同服务地址不交叉复用 Key |
| 模型目录 | **V1-only**，普通菜单保留 9 个经维护目录标注的模型，隐藏内部保留 2 个 |

⚠️ **全权限模式能操作你有权访问的任何文件，包括共享目录。** 请从正确的项目目录启动，并先备份重要数据。Linux/macOS 终端需要 `curl` 和 Python 3.8+；Windows 脚本可在用户目录安装自带 Python，不需要管理员权限。`/tmp` / 系统临时目录里的 SQLite 不适合作为长期唯一数据存储。

## 模型选择、配置细节与故障排查

完整中文说明：[**安装教程与 GPT 模型用途对照**](docs/installation.md)。在 Codex 中输入 `/model` 选择模型；显示名 **不是** API ID。`Tools` 是完整的 Codex 工具模式，适合改代码、读文件（上下文偏编程）；`Chat` 更偏一般问答。`Temporary` **不会保存到 Web 账号聊天历史**，更少留痕，但日后可能找不回该 Web 会话；`Persist` 保留 Web 聊天历史，便于回查。临时模式不等于服务端零留存，且与本地 Codex sessions 是两回事。上游原生 GPT 的 Sol/Astra/Luna 等系列也有说明，但当前不符合 V1-only 的不会出现在模型菜单。

**重复运行不会重复下载完好的官方 Codex。** 客户端缓存保存在当前用户的 `CODEX_HOME/provider-connect/official/`；同一发布 tag 的安装助手、模型目录也会复用。只有首次安装、缓存损坏、切换用户/`CODEX_HOME` 或升级版本等情况才需要重新下载。每次安装会轻量查询最新 Release tag；日常启动请直接运行 `codex`，不要运行安装命令。

出现 `Reconnecting...`：先确认上游回合是否终止；已提交的回合不能仅因前台断线而重复执行。已知 `UPSTREAM_READINESS` 失败会直接返回上次诊断，**不会自动重装或重投**。上游修好后可明确加 `--repair`（PowerShell 用 `-Repair`）重测。

部分账号的 Temporary Chat 工具能力在上游 personalization 预检会失败。保持默认 `temp`，不自动改隐私设置；若明确接受 **Web 账号保存聊天历史**，可在上述 Bash 安装命令末尾增加 `--model chatgpt-web/gpt-6-tools-persist`，Windows 增加 `-Model 'chatgpt-web/gpt-6-tools-persist'`。这只是可选择的恢复路径，HTTPS CPR 仍需使用本人 Key 完成实际工具自检才算通过。

## SQLite 冲突或安装中断后的恢复（v0.2.0 待发布）

旧版 v0.1.1 的 `SQLite 新旧目录均有数据` 是硬中断，**反复执行原命令不会解决**，也不能靠 `CODEX_SQLITE_HOME` 可靠覆盖已有 `sqlite_home` 配置。不要删除 `state_5.sqlite`、`-wal`、`-shm`，更不要把两套文件拼在一起。

在 v0.2.0 及之后：**默认继续使用原本配置/默认使用的数据库**，跳过危险的迁移；新旧目录均原样保留，并在终端打印 `[SQLITE_PRESERVED]` 和完整的切换命令。想显式选择另一份库时，在正常安装命令的 `--lab` 后加 `--sqlite-home '/绝对路径'`；Windows 添加 `-SqliteHome 'C:\\实际目录'`。显式选库只是切换，不会同步、合并历史或删除另一份库。选择前可比较文件修改时间和已有会话；不要在 Codex 正写入数据库时手工移动文件。

新版意外中断时先看终端的 `[NEXT]`。已有版本管理器时，下面两条都不需要联网；**先 status，看见待恢复事务才执行 recover**：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/provider-connect/install_lifecycle.py" status
python3 "${CODEX_HOME:-$HOME/.codex}/provider-connect/install_lifecycle.py" recover
```

`RECOVERY_REQUIRED` 表示无法确认是否已发送真实模型回合，须检查事务日志，不要盲目 `--repair` 或重新发起同一任务。只有实际完成工具调用并收到 `[READY]` 才算安装成功。

## 测试状态

**v0.1.0 已作为 pre-release 发布，尚不是实机验证通过的稳定版。** 2026-10-21 的 [GitHub Actions 跨平台测试](https://github.com/Molaison/codex-101/actions/runs/39603874786) 已全部成功（Windows、macOS、Ubuntu）；Linux 本地 84 项离线测试通过。发行包四个文件已从公开 Release 重新下载并与源码逐一比对一致。

**目标 HTTPS CPR 的真实验收尚未完成。** 原官方 Codex 0.162.1 自检用的是维护机**内网 HTTP API**，不是安装器固定的 HTTPS 地址；配置、`auth.command` 和官方隔离环境已恢复，无需重新提供 Key。该内网的 temp 模型仍失败于上游 readiness。用户另行批准独立持久聊天后，修正原生自检覆盖 sandbox 配置的问题，`gpt-6-tools-persist` 已于 2026-10-22 在 **44.993 秒**内完成一次真实工具读取、内容匹配和成功回合，退出码 0、`[READY]`；不开 Memory、未重启或取消 full-2 活跃回合。**这不代表 temp、HTTPS 或三平台实机通过**，默认模型及既有 Release 附件不变。不同地址不能交叉复用 Key 或验收结果；未携带 Key 访问 HTTPS `/v1/models` 返回预期的 401，不等于服务故障。

维护者交接：[脱敏故障记录、只读查询命令与恢复条件](docs/01_cpr_acceptance.md)。

**离线测试不能替代本人 Key 的端到端测试。** CPR `/models` 可用只证明模型权限；仅使用最终安装的官方客户端成功读取文件并返回匹配内容，才能打印 `[READY]`。

本仓库从维护者的 [codex-provider-connect](https://github.com/Molaison/codex-provider-connect) 接入器演进而来，`codex_provider.py` 仍保留兼容入口。发行版锁定一个版本的 Shell / PowerShell 启动器、Python 助手和 V1 模型目录。
