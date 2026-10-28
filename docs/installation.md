# 实验室 Codex（codex-101）：Windows / macOS / Linux 使用教程

> **安装原则**：同学只需安全输入自己的 Key。入口自动选择最新稳定发布 tag；尚无稳定版时选最新预发布，载荷不混用版本。每台电脑都必须通过安装器自己的真实工具调用 `[READY]` 才算配置成功。实机验收范围以项目 [README](../README.md#测试状态) 为准。

## 1. 同学只需做一次

1. 在**任何能访问 CPR HTTPS 服务**的 Windows、macOS 或 Linux 电脑／服务器上都能安装，**不需要 SSH 登录 ywl**；无需预装 Node.js、npm 或 Codex。
2. 向 @曾正鹏 申请 **个人 CPR Key**。每人一个，按个人限额使用，不要分享到群聊、文件或 Git。
3. 在需要使用 Codex 的那台电脑的终端粘贴对应平台的**一条命令**。首次只会让你安全输入个人 Key，不需要输入 API 地址、模型名或修改配置文件。

   **macOS / Linux：**

   ```bash
   (set -o pipefail; curl --http1.1 -fsSL --retry 2 \
     https://raw.githubusercontent.com/Molaison/codex-101/feat/magpie-cpr-one-command/install.sh \
     | CODEX_CONNECT_REF=feat/magpie-cpr-one-command bash -s -- --magpie)
   ```

   **Windows PowerShell：**

   ```powershell
   & { $savedRef=$env:CODEX_CONNECT_REF; $env:CODEX_CONNECT_REF='feat/magpie-cpr-one-command'; try { & ([scriptblock]::Create((Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Molaison/codex-101/feat/magpie-cpr-one-command/install.ps1').Content)) -Magpie } finally { $env:CODEX_CONNECT_REF=$savedRef } }
   ```

4. 看到 **`[READY]` 且安装成功退出**后，打开**新终端**，进入自己的项目目录执行 `codex`。如果机器上已有同名定制版客户端占用 PATH，按安装器输出的路径运行实验室官方版；不要删除旧客户端。安装器若显示其他状态，不表示已通过真实模型测试。

如果已有 Key 和配置，重复运行同一命令会尝试安全复用；无需再次输入。换 Key 时联系维护者按安装器的 `--reconfigure` 机制处理，**不要把 Key 粘贴进聊天或命令行参数**。

**日常使用直接执行 `codex`，不需要再运行安装器。** 首次安装的官方二进制存入当前用户 `CODEX_HOME/provider-connect/official/`，同一版本完好时直接复用；同一发布 tag 的助手与模型目录也会缓存。只有首次安装、升级版本、缓存损坏或切换用户/`CODEX_HOME` 时才可能重新下载大包；安装器每次仅查询一下最新发布 tag。

## 2. 安装器为每个人自动完成什么

| 事项 | 自动行为 |
| --- | --- |
| Codex 客户端 | 使用官方 npm 发布的 **Codex 0.162.1** 平台包，下载时校验 npm SHA512，不执行你个人曾修改的 `codex`；原二进制不覆盖。|
| 启动命令 | macOS/Linux 在 `$HOME/.local/share/codex-lab/bin/codex` 放独立官方入口；Windows 在 `%LOCALAPPDATA%\CodexLab\bin\codex.cmd` 放独立入口，添加用户 PATH；原二进制不覆盖。|
| API 服务 | 固定 `https://cpr.molaisonz.dpdns.org/v1`；不复用不同地址的 Key。|
| Key | 单独存入当前用户私有目录（目录 `0700`、文件 `0600`），通过 `auth.command` 读取。|
| SQLite | Linux 使用 `/tmp/<用户名>/codex-sqlite`；macOS/Windows 使用系统临时目录下 `<用户名>/codex-lab/codex-sqlite`；避免在共享 HOME 中频繁写入。旧库确认停写后迁移主库与 WAL/SHM，**旧文件保留**。|
| 模型 | LAB（`--lab` / `--magpie`）默认 `gpt-6.1-sol`，推理深度 `high`；自检使用 `DeepSeek-V4.1-Flash`，推理深度 `none`，不改变保存的默认 Sol。Magpie 使用 `cpr/<raw>` 限定名。|
| 权限 | 按实验室要求，默认 `sandbox_mode="danger-full-access"`、`approval_policy="never"`，不弹执行授权。|
| 连接稳定性 | CPR 禁用 Responses WebSocket，使用 SSE；**HTTP 自动重试 8 次、SSE 自动重试 12 次、SSE 空闲超时 15 分钟**；真实工具自检前台最多等待 20 分钟，未结束的回合可查询恢复。|
| 验收 | 本人 Key 的 CPR `/models` 必须同时列出 `gpt-6.1-sol` 和 `DeepSeek-V4.1-Flash`，缺一即拒绝、不报 READY、不回落其他模型；再由**最终官方客户端**用 Flash 读临时文件并核对工具结果，通过才报 READY。|

**重要：**全权限意味着 Codex 可以修改你有权限访问的任何文件，包括共享目录，请只在明确的项目目录运行。系统临时目录会在重启或清理后丢失；项目代码与实验数据不得存放其中。原来的 `CODEX_HOME`、sessions 和项目文件不会随 SQLite 迁移删除。

如果旧 Codex 进程仍在写数据库，或目标已有另一份 SQLite 数据，安装器会停止迁移并告诉你原因；**不会强杀进程、覆盖数据库或把两份数据混合**。结束旧任务后重跑同一安装命令即可。

## 3. 模型怎么选？先看这张表

**默认使用 `gpt-6.1-sol`（`high`）。** 在 Codex 内用 `/model` 切换；Web 条目仍可选。菜单的显示名称只是标签，脚本/API 中必须使用准确模型 ID；Magpie 使用 `cpr/<raw>` 限定名。

| 模型 ID | 中文解释及用途 |
| --- | --- |
| `gpt-6.1-sol` | 默认日常模型，推理深度 `high`。 |
| `DeepSeek-V4.1-Flash` | 安装时真实工具自检模型，推理深度 `none`；自检不改变保存的默认模型。 |
| `chatgpt-web/gpt-6-tools-temp` | 可选的 GPT-6 工具模式、Web 临时会话。适合改代码、阅读文件、写分析脚本。 |
| `chatgpt-web/gpt-6-tools-persist` | 同为 GPT-6 Tools，但会保存 **Web 账号聊天历史**；适合需要以后回查的工具会话。|
| `chatgpt-web/gpt-6-chat-temp` | GPT-6 的 **Chat** 模式、Web 临时会话；更偏普通问答、概念解释与讨论，不是完整的 Codex 工具工作流。|
| `chatgpt-web/gpt-6-chat-persist` | GPT-6 Chat + Web 保存历史；适合以后继续文字讨论，不是首选工具代理。|
| `chatgpt-web/gpt-5.6-tools-temp` | GPT-5.6 的工具模式，Web 临时会话；GPT-6 路由出现兼容问题时可作为明确可测的备选。|
| `chatgpt-web/gpt-5.6-tools-persist` | GPT-5.6 Tools + Web 保存历史；需要历史行为时使用。|
| `chatgpt-web/gpt-5.6-chat-temp` | GPT-5.6 Chat + Web 临时会话；文字问答。|
| `chatgpt-web/gpt-5.6-chat-persist` | GPT-5.6 Chat + Web 保存历史；文字问答。|
| `gpt-5.6-luna` | 旧版轻量/快速模型；简单解释或小任务可尝试，能力以实际验证为准。|

**`Tools` vs `Chat`：** `Tools` 是完整的 **Codex 工具模式**，能读写文件、执行命令，但上下文更偏代码、项目与自动操作；`Chat` 更偏一般性问答、文献思路、概念解释，不应指望它像 Tools 一样完成文件修改。要实际改代码就选 `Tools`；只是问问题可选 `Chat`。

**`Temporary`（`temp`）vs `Persist`：** `Temporary` 不将 Web 会话保存进 ChatGPT **账号聊天历史**，留痕更少，适合临时或偏隐私的讨论；代价是隔一段时间后可能找不回同一个 Web 会话，**不要把它当成长期可恢复的工作记录**。`Persist` 保存 Web 账号侧的会话历史，更适合需要回查、持续讨论的任务。`Temporary` **不等于服务端零留存或绝对隐私**；两种模式与 Codex **本地 sessions** 的保存是两回事。长任务请把关键进展和结果写回项目文件。

`high`、`xhigh`、`max` 是推理深度，**不是三个独立模型**；Web 条目的目录支持这些档位，默认使用 `high`。更深的档位可能更慢或占用更多额度。

## 4. 原本支持的 GPT 模型去哪了？

截至 2026-10-21，上游维护目录列出的下列 **原生 GPT 模型**（并非 `chatgpt-web/` 路由）可帮助理解命名和代际定位；旧发行包的 V1-only 状态属于历史。当前候选使用 `gpt-6.1-sol` 作为默认，其他条目能否使用仍须以本人 Key 权限和实际调用为准。

| 上游原生 GPT 模型 | 主要定位（来自维护目录） | 旧发行包状态 |
| --- | --- | --- |
| `gpt-6.1-sol` | 最新一代日常编程、通用任务主力 | V2；旧包不可选，当前候选默认 |
| `gpt-6-astra` | 复杂度高、需要更强推理的任务 | V2；不可选 |
| `gpt-6-sol` | 上一代通用任务主力 | V2；不可选 |
| `gpt-6-luna` | 上一代快速、轻量的简单任务模型 | V2；不可选 |
| `gpt-5.6-sol` | 更早一代通用任务主力 | V2；不可选 |
| `gpt-5.6-terra` | 更早一代平衡型模型 | V2；不可选 |
| `gpt-5.5` | 旧版编程模型 | 未声明 V1；不可选 |

**历史发行包**的 `model_catalog.json` 保留上游目录中 **11 个显式 V1 条目**，其中 9 个正常可见、2 个内部隐藏。**目录中看到 ≠ Key 有权限 ≠ 能成功完成工具调用**，应以当前 `/models` 权限及官方客户端真实工具调用为准。

验收结果还必须对应**同一地址、模型和凭据**：内网 HTTP API 的测试不能代表本教程固定的 HTTPS 入口。维护者应核对自检 `state.json` 的 `connection.url` 与原生配置的 `base_url`；不能仅凭模型名或错误码沿用旧结果。地址范围更正见 [真实验收记录](01_cpr_acceptance.md)。

## 5. `Reconnecting...` 或失败时怎么办？

过去有 CPR WebSocket 提前断流的现象；旧版安装器还将 `request_max_retries` 和 `stream_max_retries` 都设为 **0**。新预设改走 SSE，设置 **HTTP 8 次、SSE 12 次自动重试**，把 SSE 无事件空闲超时从默认 **5 分钟提高到 15 分钟**。这些是客户端缓解措施，不保证修复服务端故障；重试次数多也意味着严重故障时最终失败可能更晚。相关字段见 [OpenAI Codex 配置参考](https://developers.openai.com/codex/config-reference/)。

先区分是 **终端/网络断线**、**Codex 与 CPR 的响应流断线**，还是 **Key 额度、服务端模型超时或 Web 桥接问题**。不要反复安装、反复提交同一请求，尤其是写文件、外部操作和长任务；先查原任务是否结束，再决定是否重试。

| 安装/自检状态 | 该做什么 |
| --- | --- |
| `AUTH` / `ACCESS` | 分别核对个人 Key / 联系管理员检查该 Key 的模型权限。|
| `NETWORK` / `ENDPOINT` | 检查当前电脑出口、代理、DNS、证书；不要关闭 TLS 校验。|
| `CHECK_PENDING` / `SERVER_BUSY` | 等旧任务或旧自检完成；**重跑同一入口查询状态**，不要额外提交相同请求。|
| `SERVER_RESTART_REQUIRED` | 让旧任务结束，重跑安装；只允许空闲重启。|
| `UPSTREAM_COMPAT` | 让维护者修 CPR/ChatGPT Web 桥接兼容；重装官方 CLI 无法修服务端协议。|
| `UPSTREAM_READINESS` | CPR Web personalization preflight 一直超时；联系维护者检查桥接和账号 readiness。继续重复安装无效。|
| `OUTPUT_MISMATCH` / 未 READY | 联系管理员，提供**脱敏**状态和时间；不要提供 Key 或整个 `~/.codex`。|

维护者检查优先级：分别记录断线发生时的 Codex 版本、CPR 状态、HTTP/SSE 事件是否完成、代理闲置超时、429/502/503/504 情况；区分终态失败与仍在进行的请求。客户端配置只能缓解短暂断流，不能把服务端失败伪装成成功。

**如果是 `UPSTREAM_READINESS`：** `/models` Key/模型预检成功不代表真实工具能启动。已知同一模型的失败会直接显示保存的终态，不会再次下载或盲目提交；上游问题处理后再加 `--repair`（PowerShell `-Repair`）明确重测。历史 Web 持久模式成功证据仅覆盖维护者的内网，不能代表当前默认或成员设备验收；不自动切换模型或启用 Memory。

安装源代码、版本发行与反馈：[codex-101](https://github.com/Molaison/codex-101)。
