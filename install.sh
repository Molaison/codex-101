#!/usr/bin/env bash
# Published at releases/latest/download/install.sh; all payloads stay on one release.
# Configure the official codex command. The maintenance helper is not a daily CLI.
set -Eeuo pipefail

install_failure() {
    local status="$1"
    shift
    local root="${CODEX_HOME:-$HOME/.codex}/provider-connect"
    if (( status == 2 )); then
        printf '\n[PENDING] 安装尚未确认 READY；可能仍有原自检正在运行。\n' >&2
    else
        printf '\n[NOT_READY] 安装未完成（退出码 %s）；旧数据未被安装器清理。\n' "$status" >&2
    fi
    if [[ -f "$root/codex_provider.py" ]]; then
        printf '[NEXT] 只重测 endpoint（不改配置、不调用模型）：python3 %q preflight\n' "$root/codex_provider.py" >&2
    fi
    if [[ -f "$root/install_lifecycle.py" ]]; then
        printf '[NEXT] 查看安装状态（本地只读）：python3 %q --root %q status\n' "$root/install_lifecycle.py" "$root" >&2
        if [[ -f "$root/pending.json" ]]; then
            printf '[NEXT] 恢复同一事务（不盲目重投）：python3 %q --root %q recover\n' "$root/install_lifecycle.py" "$root" >&2
        fi
    fi
    printf '[NEXT] 排除上述错误原因后，可重新执行完整安装命令：\n  (set -o pipefail; curl --http1.1 -fsSL --retry 2 https://raw.githubusercontent.com/Molaison/codex-101/main/install.sh | bash -s --' >&2
    printf ' %q' "$@" >&2
    printf ')\n' >&2
}

main() {
    local release="${CODEX_CONNECT_REF:-}"
    local base
    local root="${CODEX_HOME:-${HOME}/.codex}/provider-connect"
    local destination="${root}/codex_provider.py"
    local stamp="${root}/payload-ref"
    local temporary damaged_helper=false cached=false wants_magpie=false
    for command in curl python3; do
        if ! command -v "$command" >/dev/null 2>&1; then
            printf '缺少 %s；请安装后重跑同一命令。\n' "$command" >&2
            return 1
        fi
    done
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
        printf '当前 python3 低于 3.8；请切换到已有的 Python 3.8+ 环境后重跑同一安装命令。未改动安装或数据库。\n' >&2
        return 1
    fi
    for argument in "$@"; do
        case "$argument" in
            --magpie) wants_magpie=true ;;
            --client|--client-sha256|--patch-catalog-limit)
                printf '%s 不属于原生接入；此安装器不修改 Codex 二进制。\n' "$argument" >&2
                return 1 ;;
        esac
    done
    if [[ -z "$release" ]]; then
        if [[ -n "${CODEX_CONNECT_BASE:-}" ]]; then
            printf '自定义 CODEX_CONNECT_BASE 时还需设置 CODEX_CONNECT_REF。\n' >&2
            return 1
        fi
        # Pin all payloads to one published tag. Prefer stable; until the first
        # stable release exists, keep using the newest explicit prerelease.
        # Say which tag was chosen, so a gap against main needs no explanation.
        if ! release=$(curl --http1.1 --fail --silent --show-error --location --retry 2 --max-time 30 \
            -H 'Accept: application/vnd.github+json' \
            'https://api.github.com/repos/Molaison/codex-101/releases?per_page=30' |
            python3 -c 'import json,re,sys
needed = ("install.sh", "install.ps1", "codex_provider.py", "model_catalog.json")
eligible, skipped = [], []
for item in json.load(sys.stdin):
    tag = item.get("tag_name") or ""
    names = {asset.get("name") for asset in item.get("assets") or []}
    if item.get("draft") or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        continue
    missing = [name for name in needed if name not in names]
    if missing:
        skipped.append((tag, "、".join(missing)))
    else:
        eligible.append(item)
for tag, missing in skipped:
    sys.stderr.write("跳过 %s：该 Release 缺少附件 %s\n" % (tag, missing))
chosen = next((item for item in eligible if not item.get("prerelease")), eligible[0] if eligible else None)
if chosen is None:
    sys.stderr.write("已发布的 Release 中没有同时含四个安装附件的版本。\n")
    print("", end="")
else:
    reason = "最新稳定 Release" if not chosen.get("prerelease") else "无稳定 Release，取最新预发布"
    others = "、".join(item["tag_name"] for item in eligible if item is not chosen) or "无"
    sys.stderr.write("已选 %s（%s）；其它已发布候选：%s\n" % (chosen["tag_name"], reason, others))
    print(chosen["tag_name"], end="")'); then
            printf '无法从 GitHub 读取发布版本；未改动本机安装。\n' >&2
            return 1
        fi
        if [[ -z "$release" ]]; then
            printf '没有找到含四个安装附件的发布版本；未改动本机安装。\n' >&2
            return 1
        fi
    fi
    # Interactive only: a newer source tree is not installable, so say so instead
    # of leaving the user to guess why an older published release was pinned.
    # Skipped for piped runs, which must stay a pure download of the release.
    if [[ -t 2 && -z "${CODEX_CONNECT_REF:-}" ]]; then
        # A newer source tree is not installable: say so, instead of leaving the
        # user to guess why an older published release was pinned.
        if source_release=$(curl --http1.1 -fsS --max-time 10 --retry 1 \
            'https://raw.githubusercontent.com/Molaison/codex-101/main/release_manifest.json' 2>/dev/null |
            RELEASE="$release" python3 -c 'import json,os,sys
def digits(tag):
    return tuple(int(part) for part in tag.lstrip("v").split(".") if part.isdigit())
try:
    source = json.load(sys.stdin).get("release") or ""
except ValueError:
    sys.exit(0)
if digits(source) > digits(os.environ.get("RELEASE", "v0.0.0")):
    print(source, end="")') && [[ -n "$source_release" ]]; then
            printf '提示：源码 main 已是 %s，但该版本还没有发布 Release；安装器只安装已发布附件。\n' "$source_release" >&2
        fi
    fi
    base="https://github.com/Molaison/codex-101/releases/download/${release}"
    # An explicit branch/commit can have no release assets; version tags use releases.
    if [[ -n "${CODEX_CONNECT_REF:-}" && ! "$release" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        base="https://raw.githubusercontent.com/Molaison/codex-101/${release}"
    fi
    base="${CODEX_CONNECT_BASE:-$base}"
    mkdir -p "$root"
    chmod 700 "$root"
    # New releases have a release-pinned hash manifest and a recoverable Python
    # lifecycle engine. Keep v0.1.x's existing installer contract for migration.
    if [[ "$release" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] &&
       (( 10#${BASH_REMATCH[1]} > 0 || 10#${BASH_REMATCH[2]} >= 2 )); then
        if [[ -f "$root/install_lifecycle.py" && -f "$root/releases/$release/release_manifest.json" ]] &&
           { ! $wants_magpie || [[ -f "$root/releases/$release/magpie_support.py" ]]; } &&
           python3 "$root/install_lifecycle.py" --root "$root" verify --release "$release" >/dev/null 2>&1; then
            printf '复用已验证的本地 %s 发布组件。\n' "$release" >&2
            python3 "$root/install_lifecycle.py" --root "$root" install \
                --release "$release" --payload-dir "$root/releases/$release" -- "$@"
            return
        fi
        temporary=$(mktemp -d "${root}/.installer.XXXXXX")
        trap 'rm -rf -- "${temporary:-}"' EXIT
        for payload in release_manifest.json install_lifecycle.py codex_provider.py model_catalog.json magpie_support.py; do
            curl --http1.1 --fail --silent --show-error --location --retry 2 --max-time 180 \
                "${base}/${payload}" -o "${temporary}/${payload}"
        done
        python3 "$temporary/install_lifecycle.py" --root "$root" install \
            --release "$release" --payload-dir "$temporary" -- "$@"
        return
    fi
    # Tagged release assets are immutable. Re-running a failed model check must
    # not download the same helper/catalog again; mutable refs always refresh.
    if [[ "$release" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ && -z "${CODEX_CONNECT_BASE:-}" \
          && -f "$stamp" && ! -L "$destination" && -f "$destination" && -f "$root/model_catalog.json" ]] \
       && { ! $wants_magpie || [[ -f "$root/magpie_support.py" && ! -L "$root/magpie_support.py" ]]; } \
       && [[ $(cat "$stamp") == "$release" ]] \
       && grep -Fqx "VERSION = \"${release#v}\"" "$destination" \
       && python3 -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$destination" \
       && python3 -c 'import json,sys; x=json.load(open(sys.argv[1])); assert isinstance(x.get("models"),list) and x["models"]' "$root/model_catalog.json"; then
        cached=true
    fi
    if $cached; then
        printf '复用本地 %s 安装助手与目录，不重新下载；沿用已保存的连接。\n' "$release" >&2
    else
        if [[ -e "$destination" ]] && ! grep -q 'codex-provider-connect' "$destination"; then
            if [[ ! -L "$destination" ]] && python3 -c 'import json,sys; assert json.load(open(sys.argv[1])).get("integration") == "native"' "$root/connection.json" 2>/dev/null; then
                damaged_helper=true
            else
                printf '拒绝覆盖无关文件：%s\n' "$destination" >&2
                return 1
            fi
        fi
        temporary=$(mktemp -d "${root}/.installer.XXXXXX")
        trap 'rm -rf -- "${temporary:-}"' EXIT
        printf '正在安装或更新 %s；沿用已保存的连接。\n' "$release" >&2
        # Some proxy paths advertise HTTP/2 but fail release-asset streams (curl 92).
        curl --http1.1 --fail --silent --show-error --location --retry 2 "${base}/codex_provider.py" -o "$temporary/codex_provider.py"
        curl --http1.1 --fail --silent --show-error --location --retry 2 "${base}/model_catalog.json" -o "$temporary/model_catalog.json"
        if [[ "$release" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
           && ! grep -Fqx "VERSION = \"${release#v}\"" "$temporary/codex_provider.py"; then
            printf '下载的安装助手与 %s 不一致；未覆盖现有文件。\n' "$release" >&2
            return 1
        fi
        python3 -c 'import json,sys; x=json.load(open(sys.argv[1])); assert isinstance(x.get("models"),list) and x["models"], "Invalid model catalog"' "$temporary/model_catalog.json"
        if $wants_magpie; then
            curl --http1.1 --fail --silent --show-error --location --retry 2 "${base}/magpie_support.py" -o "$temporary/magpie_support.py"
            python3 -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$temporary/magpie_support.py"
            chmod 600 "$temporary/magpie_support.py"
            mv -f -- "$temporary/magpie_support.py" "$root/magpie_support.py"
        fi
        chmod 600 "$temporary/codex_provider.py" "$temporary/model_catalog.json"
        if $damaged_helper; then
            local backup
            mkdir -p "$root/backups"
            chmod 700 "$root/backups"
            backup=$(mktemp "$root/backups/damaged-helper.XXXXXX")
            cp -- "$destination" "$backup"
            chmod 600 "$backup"
        fi
        printf '%s\n' "$release" > "$temporary/payload-ref"
        chmod 600 "$temporary/payload-ref"
        mv -f -- "$temporary/model_catalog.json" "$root/model_catalog.json"
        mv -f -- "$temporary/codex_provider.py" "$destination"
        mv -f -- "$temporary/payload-ref" "$stamp"
        rmdir "$temporary"
        trap - EXIT
    fi
    python3 "$destination" _native-setup "$@"
    printf '\n[READY] 原生配置、模型菜单和真实只读工具自检通过。\n'
    if [[ " $* " == *" --lab "* || " $* " == *" --lab-ywl "* || " $* " == *" --magpie "* ]]; then
        printf '启动：打开新终端，运行 codex；若现有客户端仍占用命令名，可直接运行 %s/.local/share/codex-lab/bin/codex。\n' "$HOME"
        return
    fi
    if command -v codex >/dev/null 2>&1; then
        printf '以后直接运行：codex\n'
    elif [[ -x "${HOME}/.local/bin/codex" ]]; then
        printf '启动：%s/.local/bin/codex\n' "$HOME"
        printf '新开终端后运行 codex；已有其他客户端时不强制修改 PATH。\n'
    else
        printf '继续使用 CODEX_BINARY 指定的现有客户端；未替换原入口。\n'
    fi
    printf '安装、更新或修复：重跑同一固定下载命令；更换 key 加 --reconfigure。\n'
    printf '已通过的自检会复用；需要强制重测时加 --repair。\n'
    printf '已有 server 有任务时不重启；需要更新时，空闲并确认后才调用官方重启命令。\n'
}

# Keep arguments and the trap alive after an explicit failure return from main.
original_args=("$@")
trap 'install_failure "$?" "${original_args[@]}"' ERR
main "$@"
