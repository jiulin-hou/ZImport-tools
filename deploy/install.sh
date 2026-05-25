#!/usr/bin/env bash
#
# 一键部署脚本 —— 串起 setup.sh + systemd + setup-proxy.sh + Zimlet 部署。
#
# 必须在 Zimbra 同机上以 root 运行。要分步排错可以单独跑底下的子脚本:
#   deploy/setup.sh       —— 环境 + venv + config + Zimbra 服务账号
#   deploy/setup-proxy.sh —— Zimbra 主 nginx 加 /zimport-tools/ 反代
#
# 用法:  sudo bash deploy/install.sh
set -euo pipefail

APP_DIR=/opt/zimport-tools
RUN_USER=zimport-tools
ZIMLET_NAME=com_msauto_zimport_tools

log()  { printf '\033[1;32m[install]\033[0m %s\n' "$*"; }
step() { printf '\n\033[1;36m[install] === %s ===\033[0m\n' "$*"; }
err()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

[ "$(id -u)" -eq 0 ] || { err "请用 root 运行"; exit 1; }
[ -x /opt/zimbra/bin/zmprov ] || {
    err "未检测到 Zimbra(/opt/zimbra/bin/zmprov 不存在);"
    err "ZImport-tools 必须 Zimbra 同机部署"
    exit 1
}

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(dirname "$SCRIPT_DIR")

# ----- 1. 环境 + venv + config -----------------------------------------
step "1/4 环境准备(setup.sh)"
bash "$SCRIPT_DIR/setup.sh"

# ----- 2. systemd 服务 -------------------------------------------------
step "2/4 安装并启动 systemd 服务"
cp "$APP_DIR/deploy/zimport-tools-web.service" \
   "$APP_DIR/deploy/zimport-tools-worker.service" \
   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now zimport-tools-web zimport-tools-worker
sleep 1
for svc in zimport-tools-web zimport-tools-worker; do
    state=$(systemctl is-active "$svc" || true)
    log "  $svc -> $state"
    [ "$state" = "active" ] || {
        err "$svc 启动失败,journalctl -u $svc 看详细错误"; exit 1; }
done

# ----- 3. Zimbra 主 nginx 反代 -----------------------------------------
step "3/4 配置 Zimbra 主 nginx 反代(setup-proxy.sh)"
bash "$SCRIPT_DIR/setup-proxy.sh"

# ----- 4. Zimlet 打包 + 部署 + 强制启用 + 刷 cache ----------------------
step "4/4 部署 Zimlet"
bash "$APP_DIR/zimlet/build.sh"
ZIP="$APP_DIR/zimlet/${ZIMLET_NAME}.zip"
chown zimbra:zimbra "$ZIP"
cp "$ZIP" "/tmp/${ZIMLET_NAME}.zip"
chown zimbra:zimbra "/tmp/${ZIMLET_NAME}.zip"
log "  zmzimletctl undeploy + deploy(替换旧版本)"
su - zimbra -c "zmzimletctl undeploy $ZIMLET_NAME" 2>&1 | tail -3 || true
su - zimbra -c "zmzimletctl deploy /tmp/${ZIMLET_NAME}.zip" 2>&1 | tail -3
log "  强制在 default COS 启用(避免用户得去 Preferences 自助开)"
su - zimbra -c "zmprov mc default \
    +zimbraZimletAvailableZimlets '!${ZIMLET_NAME}' \
    -zimbraZimletAvailableZimlets '+${ZIMLET_NAME}'" 2>&1 | tail || true
log "  flush zimlet cache"
su - zimbra -c "zmprov fc -a zimlet" 2>&1 | tail -3

# ----- 完成 ------------------------------------------------------------
ZIMBRA_HOST=$(su - zimbra -c "zmhostname" 2>/dev/null | head -1 | tr -d '[:space:]')
VER=$(curl -sk --max-time 5 https://127.0.0.1/zimport-tools/api/version \
      2>/dev/null || echo "")
printf '\n\033[1;32m================================================================\n部署完成。\033[0m\n\n'
printf '  服务:    systemctl status zimport-tools-{web,worker}\n'
printf '  日志:    journalctl -u zimport-tools-{web,worker} -f\n'
printf '  版本:    %s\n' "${VER}"
printf '  入口:    Zimbra Web 左下角 Zimlets 面板「数据导入」\n'
printf '           或直接访问 https://%s/zimport-tools/\n\n' "${ZIMBRA_HOST}"
printf '\033[1;33m用户需要注销 Zimbra Web 再重新登录\033[0m,zimlet 列表只在登录时拉取一次。\n\n'
printf '升级到新版本:开发机一条命令推送\n'
printf '  bash deploy/update.sh root@%s\n' "${ZIMBRA_HOST}"
printf '\033[1;32m================================================================\033[0m\n'
