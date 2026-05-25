#!/usr/bin/env bash
#
# 发版脚本 —— 一条命令完成 ZImport 的版本发布。
#
# 用法:  bash deploy/release.sh X.Y.Z
#   例:   bash deploy/release.sh 1.1.0
#
# 发版前你需要先做:
#   在 CHANGELOG.md 顶部加好本版段落 "## vX.Y.Z — 日期" 及改动条目。
#   (改动说明只有人能写,脚本不替你编;其余全自动。)
#
# 脚本会:
#   1. 校验:版本号格式、tag 未占用、CHANGELOG 段落已就位、
#            工作区除 CHANGELOG.md 外干净
#   2. 跑测试套件(venv 存在时)
#   3. 把版本号写入 zimport_tools/__init__.py
#   4. 提交(__init__.py + CHANGELOG.md)、打 annotated tag vX.Y.Z
#   5. 推送 main 与 tag 到 origin
#   6. 生成版本化交付包 dist/zimport-tools-X.Y.Z.tar.gz
#
set -euo pipefail

VERSION="${1:-}"

log() { printf '\033[1;32m[release]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname "$SCRIPT_DIR")
cd "$ROOT"

# --- 校验 ---------------------------------------------------------------
if [ -z "$VERSION" ]; then
    err "用法: bash deploy/release.sh X.Y.Z"
    exit 1
fi
if ! printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    err "版本号格式应为 X.Y.Z(语义化版本),收到:$VERSION"
    exit 1
fi
TAG="v$VERSION"

if git rev-parse "$TAG" >/dev/null 2>&1; then
    err "标签 $TAG 已存在 —— 发版终止。"
    exit 1
fi
if ! grep -q "^## v${VERSION} " CHANGELOG.md; then
    err "CHANGELOG.md 里没有 '## v${VERSION}' 段落。"
    err "请先在 CHANGELOG.md 顶部补好本版更新记录再发版。"
    exit 1
fi
# 允许 CHANGELOG.md 有改动(发版前编辑过),其余文件必须干净
EXTRA=$(git status --porcelain | awk '{print $NF}' | grep -v '^CHANGELOG.md$' || true)
if [ -n "$EXTRA" ]; then
    err "除 CHANGELOG.md 外有未提交改动,请先处理:"
    echo "$EXTRA" >&2
    exit 1
fi

# --- 测试 ---------------------------------------------------------------
# 优先用项目 venv,其次用任意系统 python3 + pytest,都没有才警告跳过。
PYTEST_CMD=""
# Try project venv first (assumed to have all deps).
if [ -x venv/bin/python ] && venv/bin/python -c "import pytest, flask" >/dev/null 2>&1; then
    PYTEST_CMD="venv/bin/python -m pytest"
# Fall back to system python3 only if it can import the project's deps too.
elif command -v python3 >/dev/null 2>&1 \
     && python3 -c "import pytest, flask" >/dev/null 2>&1; then
    PYTEST_CMD="python3 -m pytest"
fi
if [ -n "$PYTEST_CMD" ]; then
    log "运行测试套件($PYTEST_CMD)"
    PYTHONPATH=. $PYTEST_CMD tests/ -q
else
    err "未找到可用 pytest(venv 和 python3 都没装),跳过测试。"
fi

# --- 写版本号 -----------------------------------------------------------
log "写入版本号 $VERSION → zimport_tools/__init__.py"
printf '__version__ = "%s"\n' "$VERSION" > zimport_tools/__init__.py

# --- 提交 + 打标签 ------------------------------------------------------
log "提交并打标签 $TAG"
git add zimport_tools/__init__.py CHANGELOG.md
git commit -m "chore: release $TAG"
git tag -a "$TAG" -m "ZImport $TAG"

# --- 推送 ---------------------------------------------------------------
log "推送 main 与 $TAG 到 origin"
git push origin main
git push origin "$TAG"

# --- 交付包 -------------------------------------------------------------
mkdir -p dist
ARCHIVE="dist/zimport-tools-${VERSION}.tar.gz"
log "生成交付包 $ARCHIVE"
git archive --format=tar.gz --prefix="zimport-tools/" -o "$ARCHIVE" "$TAG"

# --- GitHub Release(可选)---------------------------------------------
# 如果装了 gh 且已 `gh auth login`,自动创建 GitHub Release 条目并把
# 交付包作为附件挂上。从 CHANGELOG.md 提取本版段落作为 release notes。
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    log "创建 GitHub Release $TAG(挂交付包)"
    NOTES=$(awk -v tag="$TAG" '
        $0 ~ "^## "tag" " { capture=1; next }
        capture && /^## v/ { exit }
        capture { print }
    ' CHANGELOG.md)
    gh release create "$TAG" "$ARCHIVE" \
        --title "$TAG" \
        --notes "${NOTES:-See CHANGELOG.md}"
else
    log "跳过 GitHub Release 自动创建(gh 未装或未认证)"
    log "  手动:gh release create $TAG $ARCHIVE --notes-file CHANGELOG.md"
fi

log "发版完成:$TAG"
echo "  交付包: $ROOT/$ARCHIVE"
echo "  GitHub: https://github.com/jiulin-hou/ZImport-tools/releases/tag/$TAG"
