# CPR 验收标准与历史证据

**codex-101 v0.2.0 已以 preview 发布。现有记录不能证明默认 temp 模型、目标 HTTPS CPR 或 Windows / macOS / Linux 三平台用户环境均已通过真实验收。**

本文区分自动化测试、官方客户端启动检查和真实服务验收，避免用一种证据替代另一种。操作入口见 [README](../README.md)，中断处理见 [恢复手册](03_upgrade_rollback.md)。

## 什么才算 READY

一次有效验收必须记录同一组信息：

- 最终实际运行的官方 Codex 路径和版本。
- 目标服务地址、模型和对应的凭据身份；报告不能包含 Key 本身。
- 真实工具读取临时文件，工具退出码为 0，输出与预设标记完整匹配。
- 成功回合 `turn.completed`，最终回答匹配，客户端退出码为 0。

安装器只有满足自检条件才应报告 `[READY]`。该结果只适用于这次连接和用户环境，不能外推到其他模型、账号、地址或系统，也不保证未来每次调用成功。

以下结果都不足以替代验收：网页打开、`/models` 可用、模型出现在菜单、包哈希一致、`codex --version` 成功、CI fixture 输出 READY。`doctor` 的 `LOCAL_OK` 也只表示本地与目录检查通过。

## 自动化测试覆盖什么

当前工作流定义在 [test.yml](../.github/workflows/test.yml)，执行结果以对应提交的 [GitHub Actions](https://github.com/Molaison/codex-101/actions) 为准。不要把旧提交的测试数量或绿灯当成本次发布的结果。

| 层次 | 检查内容 | 不能证明什么 |
| --- | --- | --- |
| Python 测试 | 配置与模型目录、客户端缓存、SQLite 保留、事务恢复、回滚和启动器合约；其中包含替身与隔离环境。 | 不能证明真实 Key、CPR 或 Web 桥接可用。 |
| 平台安装器检查 | Unix Bash 语法；Windows PowerShell 语法与隔离 bootstrap 集成。 | 隔离 bootstrap 不等于真实账号的 Windows 安装验收。 |
| 发布完整性 | `release_manifest.json` 与源码载荷 SHA256 一致。 | 清单不是独立密码学签名，不能单独证明发布者身份。 |
| 官方客户端 smoke | 三个平台下载 npm stable 包、核对 SHA512 并执行版本检查。 | 不连接 CPR，不验证工具回合或个人凭据。 |

本地复核命令（仓库根目录）：

```bash
python 01_build_release_manifest.py --check
python -m unittest -q test_lab_install test_onboarding test_profiles test_install_lifecycle
bash -n install.sh
```

Windows CI 使用 `python -X utf8 -m unittest -q test_lab_install test_install_lifecycle`，另运行 `test_windows_bootstrap.ps1`；不能将 Unix 专属测试的跳过记成 Windows 实机验收通过。

[release.yml](../.github/workflows/release.yml) 由维护者手动触发，等待上述跨平台测试和官方客户端 smoke 成功后发布 **prerelease**。它不自动推广 stable，也没有用真实 CPR Key 验收三平台。

## 历史记录：内网 HTTP 与目标 HTTPS 不可混用

以下是仓库已记录的 **2026-10-21 至 2026-10-22 历史结果**，不是 v0.2.0 的新验收。时间为香港时间。

### 内网 HTTP

历史自检使用维护机内网 `http://<内网地址>:18082/v1`、Linux x64、经 npm SHA512 校验的官方 Codex **0.162.1**。精确地址与凭据不公开。

| 时间 / 模型 | 已记录结果 | 证据范围 |
| --- | --- | --- |
| 10 月 21 日 20:04、21:06；10 月 22 日 00:57，`chatgpt-web/gpt-6-tools-temp` | 三次均终止、退出码 1，无成功工具项和成功回合，分类 `UPSTREAM_READINESS`。 | 只说明该内网路由当时的 temp 调用失败。 |
| 10 月 22 日，`chatgpt-web/gpt-6-tools-persist` | 在明确接受 Web 历史保存后，修正自检 sandbox 配置覆盖，完成一次工具读取、内容匹配和成功回合，退出码 0、READY。 | 只覆盖该内网持久模型；未开启 Memory，未重启或取消其他活跃任务。 |

temp 失败事件包含：

```text
ChatGPT personalization preflight exceeded its readiness deadline
```

这将已记录的失败定位到上游 readiness 阶段，但不能据此断定具体账号、浏览器会话或桥接组件是哪一环有错。每次日志中的重复错误事件也不等于相同次数的独立模型请求。

原始诊断曾保存在维护机临时目录，可能随清理丢失；不能承诺原始日志永久可读。需要复核时，通过原安装环境的 `checks/current.json` 找到该回合的 `state.json` 和 `events.jsonl`，不要上传整个目录。

### 目标 HTTPS

安装器固定目标为 `https://cpr.molaisonz.dpdns.org/v1`。历史记录中：

- 公开页面可访问。
- 不带 Key 请求 `/v1/models` 返回 401。这是预期的鉴权拒绝，不是个人 Key 无效或模型服务故障的证据。
- 没有该 HTTPS 地址上、使用最终官方客户端完成真实工具自检的成功证据。

早期记录曾把内网失败归到 HTTPS；核对 `state.json` 的 `connection.url` 后已更正。**内网成功或失败都不能迁移为 HTTPS 结论，Key 也不能仅因模型名相同就跨地址发送。**

## 继续验收时的最小流程

1. 核实要测的地址及凭据绑定。优先复用同一地址的现有授权配置；不要把另一服务的 Key 转发过来试。
2. 查看旧自检是否仍在运行。有未完成回合时恢复原事务；提交状态不明时保留日志并人工核对，不重新投递。
3. 上游修复且旧回合已终止后，沿用原模型做一次必要的真实只读检查。`--repair` / `-Repair` 表示明确重测，不是服务端修复开关。
4. 逐项记录客户端版本、地址、模型、工具结果、回合终态和退出码。切换为 persist 必须主动接受 Web 账号保存历史，不能用另一模型成功冒充默认 temp 已修复。
5. 在目标 HTTPS 和各目标系统的独立用户环境分别验收，再决定是否具备稳定发布条件。

对外反馈只需脱敏状态、发生时间、版本、模型和经确认可分享的地址。不要提供 Key、私有 Key 文件、完整 `config.toml`、`connection.json` 或整个 `CODEX_HOME`。
