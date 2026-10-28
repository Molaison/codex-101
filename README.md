# codex-101 · 实验室 Codex 安装与使用

> 新增 Magpie 接入候选：安装命令增加 `--magpie`（Windows 为 `-Magpie`），即可一次安装官方 Codex、配置实验室 CPR 来源和本机临时 SQLite。仍须完成真实工具自检才返回 `[READY]`。此模式先在 `feat/magpie-cpr-one-command` 分支验证，直连入口仍可使用。

**每位同学只需要自己的 CPR API Key。** 在 Windows、macOS、Linux **本机**运行安装命令；不用 SSH 到 ywl，不必手工写 `config.toml`，不用自己安装 Node.js/npm。

> **版本状态：v0.1.1 修复候选**。安装器自动选择最新稳定 Release；没有稳定版时选择最新预发布版本，所有载荷锁定在同一 tag。仅收到 `[READY]` 且安装程序成功退出，才能认定**自己这台机器**的模型、凭据和真实工具调用已通过。管理员验证范围见 [测试状态](#测试状态)。

## 一键安装

先联系 **@曾正鹏** 获取个人 Key。Key 有个人配额，请保密。**不要**把 Key 发给聊天机器人、粘贴进命令行参数或保存到 Git。

**Windows（PowerShell）** — 将整行复制到 PowerShell：

```powershell
& { $savedRef=$env:CODEX_CONNECT_REF; $env:CODEX_CONNECT_REF='feat/magpie-cpr-one-command'; try { & ([scriptblock]::Create((Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Molaison/codex-101/feat/magpie-cpr-one-command/install.ps1').Content)) -Magpie } finally { $env:CODEX_CONNECT_REF=$savedRef } }
```

**macOS（终端 Terminal）**：

```bash
(set -o pipefail; curl --http1.1 -fsSL --retry 2 https://raw.githubusercontent.com/Molaison/codex-101/feat/magpie-cpr-one-command/install.sh | CODEX_CONNECT_REF=feat/magpie-cpr-one-command bash -s -- --magpie)
```

**Linux（终端）**：

```bash
(set -o pipefail; curl --http1.1 -fsSL --retry 2 https://raw.githubusercontent.com/Molaison/codex-101/feat/magpie-cpr-one-command/install.sh | CODEX_CONNECT_REF=feat/magpie-cpr-one-command bash -s -- --magpie)
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
| 模型 | LAB（`--lab` / `--magpie`）默认 `gpt-6.1-sol`，推理深度 `high`；真实工具自检使用 `DeepSeek-V4.1-Flash`，推理深度 `none`，不改变保存的默认模型。Magpie 中使用 `cpr/<raw>` 限定名。 |
| 权限 | `danger-full-access`、`approval_policy=never`（按实验室要求，**不会询问执行批准**） |
| 稳定性 | **SSE**，HTTP 请求最多重试 **8 次**；SSE 中断最多重试 **12 次**；SSE 空闲超时 **15 分钟** |
| 存储 | 本机用户私有临时目录存 SQLite；保留原 `CODEX_HOME`、sessions；不覆盖运行中的数据库 |
| 凭据 | 独立私有 Key 文件，使用 `auth.command`；不同服务地址不交叉复用 Key |
| 模型权限 | 本人 CPR `/models` 必须同时列出 `gpt-6.1-sol` 和 `DeepSeek-V4.1-Flash`；缺一即拒绝、不返回 `[READY]`，不回落其他模型。 |

⚠️ **全权限模式能操作你有权访问的任何文件，包括共享目录。** 请从正确的项目目录启动，并先备份重要数据。Linux/macOS 终端需要 `curl` 和 Python 3.8+；Windows 脚本可在用户目录安装自带 Python，不需要管理员权限。`/tmp` / 系统临时目录里的 SQLite 不适合作为长期唯一数据存储。

## 模型选择、配置细节与故障排查

完整中文说明：[**安装教程与 GPT 模型用途对照**](docs/installation.md)。在 Codex 中输入 `/model` 选择模型；显示名 **不是** API ID。Web 条目仍可选，不再是默认模型。`Tools` 是完整的 Codex 工具模式，适合改代码、读文件（上下文偏编程）；`Chat` 更偏一般问答。`Temporary` **不会保存到 Web 账号聊天历史**，更少留痕，但日后可能找不回该 Web 会话；`Persist` 保留 Web 聊天历史，便于回查。临时模式不等于服务端零留存，且与本地 Codex sessions 是两回事。

**重复运行不会重复下载完好的官方 Codex。** 客户端缓存保存在当前用户的 `CODEX_HOME/provider-connect/official/`；同一发布 tag 的安装助手、模型目录也会复用。只有首次安装、缓存损坏、切换用户/`CODEX_HOME` 或升级版本等情况才需要重新下载。每次安装会轻量查询最新 Release tag；日常启动请直接运行 `codex`，不要运行安装命令。

出现 `Reconnecting...`：先确认上游回合是否终止；已提交的回合不能仅因前台断线而重复执行。已知 `UPSTREAM_READINESS` 失败会直接返回上次诊断，**不会自动重装或重投**。上游修好后可明确加 `--repair`（PowerShell 用 `-Repair`）重测。

历史上部分账号的 Web Temporary 工具能力在上游 personalization 预检失败。Web 条目仍可主动选择，不自动切换或改变隐私设置；HTTPS CPR 仍需使用本人 Key 完成实际工具自检才算通过。

## 测试状态

**v0.1.0 已作为 pre-release 发布，尚不是实机验证通过的稳定版。** 2026-10-21 的 [GitHub Actions 跨平台测试](https://github.com/Molaison/codex-101/actions/runs/39603874786) 已全部成功（Windows、macOS、Ubuntu）；Linux 本地 84 项离线测试通过。发行包四个文件已从公开 Release 重新下载并与源码逐一比对一致。

**当前候选的验证边界：** `gpt-6.1-sol`（`high`）已通过官方 CLI 真实工具 marker；ty3 本人同一 Key 的 canonical CPR `/models` 返回 11 条，包含 Sol 和 `DeepSeek-V4.1-Flash`，后者（`none`）也通过官方 CLI 真实工具 marker。Magpie 完整隔离链路仍在验收，不能据此认定已通过；本次仍是候选分支，并非 release 或 merge，现有安装命令待 main 最终固定 commit。

**历史记录：** 原官方 Codex 0.162.1 自检用的是维护机**内网 HTTP API**，不是安装器固定的 HTTPS 地址；配置、`auth.command` 和官方隔离环境已恢复，无需重新提供 Key。该内网的 temp 模型曾失败于上游 readiness。用户另行批准独立持久聊天后，修正原生自检覆盖 sandbox 配置的问题，`gpt-6-tools-persist` 已于 2026-10-22 在 **44.993 秒**内完成一次真实工具读取、内容匹配和成功回合，退出码 0、`[READY]`；不开 Memory、未重启或取消 full-2 活跃回合。**这不代表 temp、HTTPS 或三平台实机通过**。不同地址不能交叉复用 Key 或验收结果；未携带 Key 访问 HTTPS `/v1/models` 返回预期的 401，不等于服务故障。

维护者交接：[脱敏故障记录、只读查询命令与恢复条件](docs/01_cpr_acceptance.md)。

**离线测试不能替代本人 Key 的端到端测试。** CPR `/models` 可用只证明模型权限；仅使用最终安装的官方客户端成功读取文件并返回匹配内容，才能打印 `[READY]`。

本仓库从维护者的 [codex-provider-connect](https://github.com/Molaison/codex-provider-connect) 接入器演进而来，`codex_provider.py` 仍保留兼容入口。发行版锁定一个版本的 Shell / PowerShell 启动器、Python 助手和模型目录。
