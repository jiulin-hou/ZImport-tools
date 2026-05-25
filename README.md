# ZImport-tools

Zimbra 内置的邮件批量导入工具。在 Zimbra Web 左下角 Zimlets 面板里出现
「数据导入」入口,点击全屏弹出 iframe,**直接复用 Zimbra 登录态,
无独立登录**。

## 它解决什么问题

用户已经登录在 Zimbra Web,不应该为了导入邮件再单独登录一次,也不应该
因此多开放一个公网入口。ZImport-tools 把导入做成 Zimbra Web 里的内置
功能,共用 Zimbra 会话、不新增公网攻击面、不二次登录。

## 功能

- 多 `.eml` 文件或大 `.tgz`(>5GB)分片上传 + 断点续传
- Message-ID 双层去重(本批内 + 邮箱内)
- 后台串行 worker,任务队列持久化,关页签也不丢
- 失败任务可重试;管理员可指定目标账户
- 服务端解包归一化,规避 Zimbra 导入器的 PaxHeader 故障
- 任务详情显示每个文件的跳过/失败原因

## 架构

- 单页前端 + Flask 后端 + 独立 worker 进程
- **无状态鉴权**:每个请求从 `ZM_AUTH_TOKEN` cookie 直接 validate
  (LRU 5 分钟缓存兜底);无 Flask session、无 secret 配置
- **CSRF 防护**:状态变更端点要求 `X-Zimport-CSRF: 1` 自定义头
  (浏览器跨站表单设不了 X-* 头)
- **管理员 SOAP 操作**(账户搜索 / 委托认证)统一走配置的服务账号
- **Zimlet** 在 Zimbra Web 左下角 Zimlets 面板出现「数据导入」,
  单击/双击注入全屏 overlay iframe(自适应任何分辨率)

## 部署形态(只此一种)

**必须** 与 Zimbra 同机部署。用户访问唯一入口是 Zimbra Web 里的
Zimlet(实际加载 `https://<zimbra-host>/zimport-tools/`,由 Zimbra
主 nginx 反代到本机 8088)。

不支持独立机器部署 —— 因为浏览器要带 Zimbra 的 `ZM_AUTH_TOKEN`
cookie,必须同源。

## 环境要求

- 目标机:**运行 Zimbra 的服务器本身**(共用其 nginx)
- 操作系统:CentOS 7(其它 Linux 亦可,部署脚本针对 CentOS 7 编写)
- Python 3.11(`setup.sh` 在系统没有时会编译安装,不动系统 python3)

## 部署

### 1. 获取代码

```bash
git clone https://github.com/jiulin-hou/ZImport-tools.git
cd ZImport-tools
```

或下载指定版本:

```bash
curl -LO https://github.com/jiulin-hou/ZImport-tools/archive/refs/tags/v1.2.0.tar.gz
tar xf v1.2.0.tar.gz && cd ZImport-tools-1.2.0
```

### 2. 一键环境准备

```bash
sudo bash deploy/setup.sh
```

做的事:建系统用户/目录 → 装编译依赖 → 编译 Python 3.11(已装就跳过)
→ 建 venv 装依赖 → 生成 `/etc/zimport-tools/config.ini` →
自动 `zmprov ca` 创建服务账号 `importsvc@<domain>`(已存在则
`zmprov sp` 同步密码)→ 自检模块导入。

### 3. 启动 systemd 服务

```bash
sudo cp /opt/zimport-tools/deploy/zimport-tools-{web,worker}.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zimport-tools-web zimport-tools-worker
```

### 4. 配 Zimbra 主 nginx 反代

```bash
sudo bash /opt/zimport-tools/deploy/setup-proxy.sh
```

在 `nginx.conf.web.https.default.template` 的主 443 server 块末尾
注入 `location ^~ /zimport-tools/` 反代到 `127.0.0.1:8088`,然后
`zmproxyctl restart`。Zimbra 升级覆盖 template 时重跑此脚本即可。

### 5. 部署 Zimlet

```bash
cd /opt/zimport-tools/zimlet && bash build.sh
sudo chown zimbra:zimbra com_msauto_zimport_tools.zip
sudo cp com_msauto_zimport_tools.zip /tmp/
sudo su - zimbra -c "zmzimletctl deploy /tmp/com_msauto_zimport_tools.zip"
# 强制在 default COS 默认启用
sudo su - zimbra -c "zmprov mc default \
    +zimbraZimletAvailableZimlets '!com_msauto_zimport_tools'"
sudo su - zimbra -c "zmprov fc -a zimlet"
```

用户**注销 Zimbra Web 重登**(zimlet 列表只在登录时拉一次),左下角
Zimlets 面板出现「数据导入」。

## 使用

1. 登录 Zimbra Web(Classic UI)
2. 左下角 Zimlets 面板点「数据导入」→ 弹出全屏 iframe
3. (管理员)填目标账户,或留空导入到自己;选目标文件夹
4. 选多个 `.eml` 或一个 `.tgz`,点「开始导入」
5. 任务在「我的任务」表里看进度,跳过/失败可点「详情」展开看原因

## 升级

开发机一条命令推送 + 重启:

```bash
bash deploy/update.sh root@<target-host>
```

要求目标机 SSH 公钥可登录、已经完成首次 `setup.sh`。脚本会 git
打包当前代码、scp 过去、停服务 → 解包 → 比较 requirements.txt 决定
是否重装依赖 → 重启服务。venv 完整保留。

## 发版

编辑 `CHANGELOG.md` 加新版本段落,然后:

```bash
bash deploy/release.sh X.Y.Z
```

## 目录结构

```
zimport_tools/   后端模块 + 前端 static/
zimlet/          Zimbra Zimlet 包(Classic 8.8.15 框架)
tests/           单元测试
deploy/          setup.sh / setup-proxy.sh / release.sh / update.sh / systemd 单元
```
