#!/usr/bin/env bash
#
# 环境准备脚本 —— 为 ZImport 准备运行环境(默认 CentOS 7,亦兼容 RHEL 7/8/9
# 及 CentOS 8 Stream)。脚本幂等,可重复运行。
#
# 它做:
# 1. 系统用户 zimport-tools
#   2. 目录 /opt /etc /var
#   3. yum 编译依赖(含 sqlite/readline/ncurses/libuuid/gdbm-devel 等
#      Python stdlib 可选模块需要的包)
#   4. CentOS/RHEL 7:OpenSSL 1.0.2 太旧,装 EPEL + openssl11 让 Python
#      3.11 能编出 _ssl(否则 pip 走不了 HTTPS,Flask 装不上)
#   5. 并行编译 Python 3.11(make altinstall)
#   6. 代码复制 + venv + pip install
#   7. config.ini:若本机即 Zimbra 主机,自动探测域名 + 自动创建服务账号
#      (zmprov ca + zmprov ma)
#
# 用法:bash deploy/setup.sh
set -euo pipefail

PYTHON_VERSION=3.11.9
APP_DIR=/opt/zimport-tools
ETC_DIR=/etc/zimport-tools
VAR_DIR=/var/lib/zimport-tools
RUN_USER=zimport-tools
PYBIN=/usr/local/bin/python3.11

log()  { printf '\033[1;32m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn ]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

[ "$(id -u)" -eq 0 ] || { err "请用 root 运行此脚本"; exit 1; }

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(dirname "$SCRIPT_DIR")

# --- 0. 操作系统判定 --------------------------------------------------
OS_ID=""
OS_VER=""
if [ -f /etc/os-release ]; then
    OS_ID=$(. /etc/os-release && echo "${ID:-}")
    OS_VER=$(. /etc/os-release && echo "${VERSION_ID:-}")
fi
log "OS = ${OS_ID} ${OS_VER}"

PKG=yum
command -v dnf >/dev/null 2>&1 && PKG=dnf

# --- 1. 系统用户 ------------------------------------------------------
if id "$RUN_USER" >/dev/null 2>&1; then
    log "用户 $RUN_USER 已存在,跳过"
else
    log "创建系统用户 $RUN_USER"
    useradd -r -s /sbin/nologin "$RUN_USER"
fi

# --- 2. 目录 ----------------------------------------------------------
log "创建目录 $APP_DIR $ETC_DIR $VAR_DIR"
mkdir -p "$APP_DIR" "$ETC_DIR" "$VAR_DIR"
chown -R "$RUN_USER:" "$VAR_DIR"

# --- 3. 编译依赖(含 Python stdlib 可选模块需要的) ------------------
log "安装编译依赖 ($PKG)"
$PKG groupinstall -y "Development Tools"
$PKG install -y \
    openssl-devel bzip2-devel libffi-devel zlib-devel xz-devel curl \
    sqlite-devel readline-devel ncurses-devel libuuid-devel gdbm-devel

# --- 4. RHEL/CentOS 7 OpenSSL 1.0.2 → 装 openssl11 -------------------
PKG_CONFIG_OPENSSL=""
if [ "$OS_ID" = "centos" ] || [ "$OS_ID" = "rhel" ]; then
    if [ "${OS_VER%%.*}" = "7" ]; then
        log "RHEL/CentOS 7 检测到 → 准备 openssl11(让 Python 3.11 编出 _ssl)"
        $PKG install -y epel-release || true
        $PKG install -y openssl11 openssl11-devel
        # openssl11 的 pkg-config 名为 openssl11.pc;给它做 symlink 让
        # Python configure 通过 pkg-config 找到 openssl
        mkdir -p /usr/local/zimport-pkgconfig
        for n in openssl libssl libcrypto; do
            if [ -f "/usr/lib64/pkgconfig/${n}11.pc" ]; then
                ln -sf "/usr/lib64/pkgconfig/${n}11.pc" \
                       "/usr/local/zimport-pkgconfig/${n}.pc"
            fi
        done
        PKG_CONFIG_OPENSSL="/usr/local/zimport-pkgconfig"
    fi
fi

# --- 5. Python 3.11 ---------------------------------------------------
need_build=1
if [ -x "$PYBIN" ]; then
    # 检测已有 Python 是否有 _ssl 和 _sqlite3 — 没有就重编
    if "$PYBIN" -c "import ssl, sqlite3" >/dev/null 2>&1; then
        log "已检测到 $($PYBIN --version 2>&1)(_ssl + _sqlite3 都在),跳过编译"
        need_build=0
    else
        warn "已有 $PYBIN 但缺关键模块,重新编译"
    fi
fi

if [ "$need_build" = "1" ]; then
    log "编译 Python $PYTHON_VERSION(make altinstall,不覆盖系统 python3)"
    cd /usr/src
    if [ ! -d "Python-${PYTHON_VERSION}" ]; then
        curl -fLO "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz"
        tar xf "Python-${PYTHON_VERSION}.tgz"
    fi
    cd "Python-${PYTHON_VERSION}"
    make distclean >/dev/null 2>&1 || true

    if [ -n "$PKG_CONFIG_OPENSSL" ]; then
        export PKG_CONFIG_PATH="$PKG_CONFIG_OPENSSL:/usr/lib64/pkgconfig"
        ./configure --with-openssl-rpath=auto
    else
        ./configure --with-openssl-rpath=auto
    fi
    make -j"$(nproc)"
    make altinstall

    # 自检关键模块
    "$PYBIN" -c "import ssl, sqlite3, lzma, bz2; print('OK ssl=' + ssl.OPENSSL_VERSION)"
fi

# --- 6. 代码与 venv ---------------------------------------------------
if [ "$PROJECT_DIR" = "$APP_DIR" ]; then
    log "已在 $APP_DIR 内运行,跳过代码复制"
else
    log "复制代码到 $APP_DIR"
    cp -r "$PROJECT_DIR"/. "$APP_DIR"/
fi

log "创建 venv 并安装依赖"
"$PYBIN" -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"
chown -R "$RUN_USER:" "$APP_DIR"

# --- 7. config.ini 自动化 --------------------------------------------
HAS_ZIMBRA=0
ZIMBRA_HOST=""
ZIMBRA_DOMAIN=""
if [ -x /opt/zimbra/bin/zmprov ] && [ -x /opt/zimbra/bin/zmhostname ]; then
    HAS_ZIMBRA=1
    ZIMBRA_HOST=$(su - zimbra -c "zmhostname" 2>/dev/null | head -1 | tr -d '[:space:]')
    ZIMBRA_DOMAIN=$(su - zimbra -c "zmprov gad" 2>/dev/null | head -1 | tr -d '[:space:]')
    log "检测到本机是 Zimbra:host=$ZIMBRA_HOST domain=$ZIMBRA_DOMAIN"
fi

if [ -f "$ETC_DIR/config.ini" ]; then
    log "$ETC_DIR/config.ini 已存在,保留不覆盖"
else
    log "生成 $ETC_DIR/config.ini"
    cp "$APP_DIR/config.example.ini" "$ETC_DIR/config.ini"
    chmod 600 "$ETC_DIR/config.ini"
    chown "$RUN_USER:" "$ETC_DIR/config.ini"

    SVC_NAME_DEFAULT="importsvc@${ZIMBRA_DOMAIN:-example.com}"
    SVC_PASS=$(openssl rand -base64 24 | tr -d '/=+' | cut -c1-24)

    "$APP_DIR/venv/bin/python" - <<PYEOF
import configparser
cp = configparser.ConfigParser()
cp.read("$ETC_DIR/config.ini")
if "$ZIMBRA_HOST":
    cp["zimbra"]["soap_url"] = "https://$ZIMBRA_HOST:8443/service/soap"
    cp["zimbra"]["admin_soap_url"] = "https://$ZIMBRA_HOST:7071/service/admin/soap"
    cp["zimbra"]["rest_base"] = "https://$ZIMBRA_HOST:8443"
    cp["zimbra"]["verify_tls"] = "false"  # Zimbra 自签证书的默认场景
cp["service_account"]["name"] = "$SVC_NAME_DEFAULT"
cp["service_account"]["password"] = "$SVC_PASS"
with open("$ETC_DIR/config.ini", "w") as f:
    cp.write(f)
PYEOF
    log "已写入服务账号密码($SVC_NAME_DEFAULT)"

    # 若本机即 Zimbra,自动创建服务账号(若不存在)
    if [ "$HAS_ZIMBRA" = "1" ]; then
        if su - zimbra -c "/opt/zimbra/bin/zmprov ga $SVC_NAME_DEFAULT" >/dev/null 2>&1; then
            log "服务账号 $SVC_NAME_DEFAULT 已存在 —— 用 zmprov sp 同步 config.ini 的新密码"
            su - zimbra -c "/opt/zimbra/bin/zmprov sp $SVC_NAME_DEFAULT '$SVC_PASS'" >/dev/null
            su - zimbra -c "/opt/zimbra/bin/zmprov ma $SVC_NAME_DEFAULT zimbraIsAdminAccount TRUE" >/dev/null
        else
            log "在 Zimbra 上创建服务账号 $SVC_NAME_DEFAULT(标记 admin)"
            su - zimbra -c "/opt/zimbra/bin/zmprov ca $SVC_NAME_DEFAULT '$SVC_PASS' displayName 'ZImport Service'" >/dev/null
            su - zimbra -c "/opt/zimbra/bin/zmprov ma $SVC_NAME_DEFAULT zimbraIsAdminAccount TRUE" >/dev/null
        fi
    else
        warn "未检测到本机 Zimbra(无 /opt/zimbra/bin/zmprov);"
        warn "请手工到 Zimbra 上创建账号 $SVC_NAME_DEFAULT 密码 $SVC_PASS,并标记 zimbraIsAdminAccount=TRUE"
    fi
fi

# --- 8. 自检:venv 能否导入应用 --------------------------------------
log "自检:导入应用模块"
( cd "$APP_DIR" && PYTHONPATH="$APP_DIR" "$APP_DIR/venv/bin/python" \
  -c "import zimport_tools.web, zimport_tools.worker, zimport_tools.zimbra_folders, zimport_tools.zimbra_search; print('  模块导入 OK')" )

log "环境准备完成。"
cat <<EOF

================================================================
下一步:

  1. 安装并启动服务:
       cp $APP_DIR/deploy/zimport-tools-web.service $APP_DIR/deploy/zimport-tools-worker.service \\
          /etc/systemd/system/
       systemctl daemon-reload
       systemctl enable --now zimport-tools-web zimport-tools-worker

  2. 配 nginx 反向代理(同机 Zimbra 用):
       bash $APP_DIR/deploy/setup-proxy.sh        # 默认 29443 端口
       bash $APP_DIR/deploy/setup-proxy.sh --port 9443  # 换端口

  3. 浏览器打开 https://<host>:<port>/ 用 Zimbra 账号登录测试
================================================================
EOF
