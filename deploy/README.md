# 部署说明

ZImport-tools **只能** Zimbra 同机部署(CentOS/RHEL 7+)。venv 完全隔离,
对 Zimbra 无影响。所有脚本幂等,重跑无害。

## 一键部署

以 root 在目标机解包代码后,**一条命令**:

```bash
sudo bash deploy/install.sh
```

`install.sh` 串起 4 个阶段:

| 阶段 | 子脚本 | 做什么 |
|---|---|---|
| 1/4 | `setup.sh` | 系统用户 / 目录 / 编译依赖 / Python 3.11(已装跳过)/ venv / `/etc/zimport-tools/config.ini` / Zimbra 服务账号(自动 `zmprov ca`,存在就 `zmprov sp` 同步密码) |
| 2/4 | (内联) | 拷贝 systemd unit 文件 → `daemon-reload` → `enable --now` |
| 3/4 | `setup-proxy.sh` | 在 Zimbra 主 443 server 块加 `location ^~ /zimport-tools/` 反代 `127.0.0.1:8088` → `zmproxyctl restart` |
| 4/4 | `zimlet/build.sh` + zmzimletctl | 打 zimlet zip + undeploy 旧版 + deploy 新版 + 强制在 default COS 启用 + `zmprov fc -a zimlet` |

完成后:
- 服务自动起来跑(`systemctl status zimport-tools-{web,worker}`)
- 用户**注销 Zimbra Web 重登**即可在左下角 Zimlets 面板看到「数据导入」
- 或直接访问 `https://<zimbra-host>/zimport-tools/`

## 分步执行(排错用)

`install.sh` 任何一步失败,直接重跑就行(幂等)。如果你想跳着排查,
可以单独跑各阶段的子脚本 —— 它们也都是独立可重跑的:

```bash
sudo bash deploy/setup.sh                                # 阶段 1
# 阶段 2(内联):
sudo cp deploy/zimport-tools-{web,worker}.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zimport-tools-{web,worker}

sudo bash deploy/setup-proxy.sh                          # 阶段 3
# 阶段 4(内联):
bash zimlet/build.sh
sudo chown zimbra:zimbra zimlet/com_msauto_zimport_tools.zip
sudo cp zimlet/com_msauto_zimport_tools.zip /tmp/
sudo su - zimbra -c "zmzimletctl deploy /tmp/com_msauto_zimport_tools.zip"
sudo su - zimbra -c "zmprov mc default \
    +zimbraZimletAvailableZimlets '!com_msauto_zimport_tools'"
sudo su - zimbra -c "zmprov fc -a zimlet"
```

## 升级到新版本

从开发机一条命令推送 + 重启:

```bash
bash deploy/update.sh root@<target-host>
```

`git ls-files | tar` 把当前代码 scp 过去,远程停服务 → 解包 → 比较
`requirements.txt` 决定是否 `pip install` → 重启服务。venv 完整保留。
**不重新部署 zimlet**——zimlet 改了的话单独跑阶段 4。

## 维护

- **服务账号密码轮换**:`/etc/zimport-tools/config.ini` 含服务账号密码,
  文件权限须 `600`;轮换时 `zmprov sp importsvc@<domain> <新密码>` +
  改 config.ini + `systemctl restart zimport-tools-{web,worker}`
- **看日志**:
  - `journalctl -u zimport-tools-web -f`
  - `journalctl -u zimport-tools-worker -f`
  - `/opt/zimbra/log/nginx.access.log`(反代访问)
- **Zimbra 升级覆盖 nginx template 后**:重跑 `bash deploy/setup-proxy.sh`
