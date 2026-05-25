# 部署说明

ZImport-tools **只能** Zimbra 同机部署(CentOS/RHEL 7 起)。venv 完全隔离,
对 Zimbra 无影响。脚本均幂等,重跑无害。

## 首次部署 —— 三条命令

以 root 在目标机解包代码后执行:

```bash
bash deploy/setup.sh                # 环境 + venv + config.ini + 服务账号
cp /opt/zimport-tools/deploy/zimport-tools-web.service \
   /opt/zimport-tools/deploy/zimport-tools-worker.service \
   /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now zimport-tools-web zimport-tools-worker
bash /opt/zimport-tools/deploy/setup-proxy.sh   # 主 nginx 加 /zimport-tools/ 反代
```

最后部署 zimlet(详见根 [`README.md`](../README.md))。

### `setup.sh` 做什么

- 建系统用户 `zimport-tools` + 目录 `/opt/zimport-tools` / `/etc/zimport-tools`
  / `/var/lib/zimport-tools`
- yum 编译依赖(含 Python stdlib 可选模块要的 sqlite/readline/ncurses 等)
- **CentOS/RHEL 7 自动装 openssl11**,让 Python 3.11 能编出 `_ssl` 模块
- 编译 Python 3.11(`make altinstall`,不替换系统 python3;已装就跳过)
- 建 venv + 装依赖
- **生成 `/etc/zimport-tools/config.ini`**:自动探测 Zimbra 域名填 SOAP URL,
  并自动 `zmprov ca` 创建服务账号 `importsvc@<domain>` + 标记为 admin;
  账号已存在则 `zmprov sp` 同步密码

### `setup-proxy.sh` 做什么

在 Zimbra 主 443 nginx server 块末尾注入 `location ^~ /zimport-tools/`,
反代到 `127.0.0.1:8088`。最后 `zmproxyctl restart` 让 nginx 加载。

⚠️ Zimbra 升级可能覆盖 nginx template,届时重跑 `setup-proxy.sh` 即可恢复。

## 升级到新版本

从开发机一条命令推送 + 重启:

```bash
bash deploy/update.sh root@<target-host>
```

`git ls-files | tar` 把当前代码 scp 过去,远程停服务 → 解包 → 比较
requirements.txt 决定是否 `pip install` → 重启服务。venv 完整保留。

## 维护

- **服务账号密码轮换**:`/etc/zimport-tools/config.ini` 含服务账号密码,
  文件权限须为 `600`;轮换时同步更新 config.ini 然后
  `systemctl restart zimport-tools-web zimport-tools-worker`
- **看日志**:
  - `journalctl -u zimport-tools-web -f`
  - `journalctl -u zimport-tools-worker -f`
  - `/opt/zimbra/log/nginx.access.log`(反代日志)
