# 部署说明

本工具默认部署在 Zimbra 同机(CentOS/RHEL 7 起)。venv 完全隔离,对 Zimbra 无影响。
脚本均幂等,重跑无害。

## 首次部署 —— 一条命令

以 root 在目标机解包代码后执行:

    bash deploy/setup.sh

`setup.sh` 自动完成:

- 系统用户 `zimport-tools`、目录 `/opt/zimport-tools` `/etc/zimport-tools` `/var/lib/zimport-tools`
- yum 编译依赖(含 Python stdlib 可选模块要的 sqlite/readline/ncurses 等)
- **CentOS/RHEL 7 自动处理 OpenSSL 1.0.2 太旧**:装 EPEL + openssl11,让 Python
  3.11 能编出 `_ssl` 模块(否则 pip 走不了 HTTPS)
- 编译 Python 3.11(`make altinstall`,不替换系统 python3)
- 建 venv + 装依赖
- **生成 `/etc/zimport-tools/config.ini`**:`secret_key` 随机生成;若本机即 Zimbra
  主机,自动探测域名填好 SOAP URL,**并自动 `zmprov ca` 创建服务账号**
  `importsvc@<domain>` + 标记为 admin

## 启用 systemd 服务

`setup.sh` 跑完后执行:

    cp /opt/zimport-tools/deploy/zimport-tools-web.service \
       /opt/zimport-tools/deploy/zimport-tools-worker.service \
       /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now zimport-tools-web zimport-tools-worker

## 反向代理(Zimbra 同机)

在 Zimbra nginx 上开一个新端口反代到 `127.0.0.1:8088`:

    bash /opt/zimport-tools/deploy/setup-proxy.sh                # 默认 29443
    bash /opt/zimport-tools/deploy/setup-proxy.sh --port 9443    # 换端口

脚本会:

1. 写 `/opt/zimbra/conf/nginx/includes/nginx.conf.zimport-tools`(独立 server 块,复用
   Zimbra 自己的证书 `nginx.crt` / `nginx.key`)
2. 在 `nginx.conf.web.template` 末尾加一行 `include`(首次会备份原版到 `.bak.orig`)
3. `zmproxyctl restart` 让 nginx 加载

⚠️ Zimbra 升级可能覆盖 template,届时重跑 `setup-proxy.sh` 即可恢复。

## 升级到新版本

从开发机一条命令推送 + 重启:

    bash deploy/update.sh root@<target-host>

它会 `git ls-files | tar` 把当前代码 scp 过去,远程停服务 → 解包 → 比较
requirements.txt 决定是否 `pip install` → 重启服务。venv 完整保留。

## 不是 Zimbra 同机的情况

如果 ZImport 要装在一台 _可访问 Zimbra_ 的独立机器,而非 Zimbra 主机本身:

- `setup.sh` 不会自动探测 Zimbra/创建服务账号 —— 会提示你**手动**做:
  - 在 Zimbra 上 `zmprov ca importsvc@<domain> '<密码>'`
  - `zmprov ma importsvc@<domain> zimbraIsAdminAccount TRUE`
  - 把账号/密码写到 `/etc/zimport-tools/config.ini`
  - 把 `[zimbra]` 里的 URL 改成实际 Zimbra 主机地址
- 反代要自己解决 —— `setup-proxy.sh` 只在 Zimbra 同机用;独立机请自己装
  nginx 或 caddy

## 维护

- **服务账号密码轮换**:`/etc/zimport-tools/config.ini` 含服务账号密码,文件权限须为
  `600`;定期轮换并同步更新 config.ini(然后 `systemctl restart zimport-tools-web
  zimport-tools-worker`)。
- **看日志**:
  - `journalctl -u zimport-tools-web -f`
  - `journalctl -u zimport-tools-worker -f`
  - `/opt/zimbra/log/nginx.access.log` (反代日志)
