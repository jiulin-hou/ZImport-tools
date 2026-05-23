#!/usr/bin/env bash
#
# 升级脚本 —— 把当前目录(开发机)的代码推送到远程目标机,装新依赖、
# 重启服务。保留 venv 内已装的包(只在 requirements.txt 变化时 pip install)。
#
# 用法:
#   bash deploy/update.sh root@10.1.2.3
#
# 远程目标机要求:
#   - SSH 公钥已配,无密码登录
#   - 已经通过 setup.sh 完成首次部署
#   - 已经有 /opt/zimport-tools 和 systemd 服务
set -euo pipefail

REMOTE="${1:-}"
if [ -z "$REMOTE" ]; then
    echo "用法: $0 user@host" >&2
    exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(dirname "$SCRIPT_DIR")

log() { printf '\033[1;32m[update]\033[0m %s\n' "$*"; }

cd "$PROJECT_DIR"

# 1. 打包 git-tracked 文件(避免传 venv / __pycache__ / 本地 config.ini)
log "打包 git-tracked 文件"
TGZ=$(mktemp /tmp/zimport-src.XXXXXX.tgz)
trap "rm -f $TGZ" EXIT
git ls-files | tar -czf "$TGZ" -T -

# 2. 传到远程 /tmp
log "scp 到 $REMOTE"
scp -q "$TGZ" "$REMOTE:/tmp/zimport-update.tgz"

# 3. 远程执行升级
log "远程升级(停服务 / 解包 / pip / 起服务)"
ssh "$REMOTE" 'bash -se' <<'REMOTE_EOF'
set -euo pipefail
APP=/opt/zimport-tools

# 备份 requirements 以判断是否需要重装依赖
OLD_REQ=$(sha256sum "$APP/requirements.txt" 2>/dev/null | awk '{print $1}')

systemctl stop zimport-tools-web zimport-tools-worker || true

# 解包(覆盖,但保留 venv 和 var)
TMPD=$(mktemp -d /tmp/zimport-update.XXXXXX)
tar xzf /tmp/zimport-update.tgz -C "$TMPD"
# rsync 保留 venv;源目录的 . 不删除目标侧的 venv 因为 git ls-files 不含 venv
rsync -a --exclude=venv "$TMPD"/ "$APP"/
rm -rf "$TMPD" /tmp/zimport-update.tgz
chown -R zimport-tools: "$APP"

# 仅在 requirements.txt 变化时重装依赖
NEW_REQ=$(sha256sum "$APP/requirements.txt" | awk '{print $1}')
if [ "$OLD_REQ" != "$NEW_REQ" ]; then
    echo "[update] requirements 有变化,pip install"
    "$APP/venv/bin/pip" install -r "$APP/requirements.txt"
fi

# 若 service 文件改了,同步过去并 daemon-reload
for s in zimport-tools-web zimport-tools-worker; do
    if ! cmp -s "$APP/deploy/$s.service" "/etc/systemd/system/$s.service" \
        2>/dev/null; then
        cp "$APP/deploy/$s.service" "/etc/systemd/system/$s.service"
        echo "[update] $s.service 已更新"
    fi
done
systemctl daemon-reload

systemctl start zimport-tools-web zimport-tools-worker
sleep 1
echo "[update] 服务状态:"
systemctl is-active zimport-tools-web zimport-tools-worker
REMOTE_EOF

log "完成。可访问页面验证。"
