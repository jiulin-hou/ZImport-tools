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

### 一键(推荐)

在 Zimbra 同机上,以 root 身份:

```bash
# 1. 拿代码(选一个)
git clone https://github.com/jiulin-hou/ZImport-tools.git
cd ZImport-tools
#   或下载指定版本:
#   curl -LO https://github.com/jiulin-hou/ZImport-tools/releases/download/v1.4.2/zimport-tools-1.4.2.tar.gz
#   tar xf zimport-tools-1.4.2.tar.gz && cd zimport-tools

# 2. 一条命令完成所有步骤
sudo bash deploy/install.sh
```

`install.sh` 串起 4 个阶段(`setup.sh` + systemd + `setup-proxy.sh`
+ zimlet 部署),其中 zimlet 还会强制启用 + 刷 Zimbra cache。完成
后告诉用户**注销 Zimbra Web 重新登录**就能看到「数据导入」。

### 分步(排错或自定义)

`install.sh` 失败时可以逐步排查。每一步都幂等可重跑:

```bash
sudo bash deploy/setup.sh        # 阶段 1:环境 + venv + config + Zimbra 服务账号
sudo cp deploy/zimport-tools-{web,worker}.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now zimport-tools-{web,worker}
sudo bash deploy/setup-proxy.sh  # 阶段 3:Zimbra 主 nginx 加 /zimport-tools/ 反代
bash zimlet/build.sh             # 阶段 4:打 zimlet zip
sudo chown zimbra:zimbra zimlet/com_msauto_zimport_tools.zip
sudo cp zimlet/com_msauto_zimport_tools.zip /tmp/
sudo su - zimbra -c "zmzimletctl deploy /tmp/com_msauto_zimport_tools.zip"
sudo su - zimbra -c "zmprov mc default +zimbraZimletAvailableZimlets '!com_msauto_zimport_tools'"
sudo su - zimbra -c "zmprov fc -a zimlet"
```

## 使用

1. 登录 Zimbra Web(Classic UI)
2. 左下角 Zimlets 面板点「数据导入」→ 弹出全屏 iframe
3. (管理员)填目标账户,或留空导入到自己
4. 选目标文件夹;不存在的话点「+ 新建」输入路径(可嵌套如
   `Inbox/2024/Q3`,后端会逐级创建)
5. (可选)填「任务备注」,以后在任务表里能直接看到这是哪一批
6. 选多个 `.eml` 或一个 `.tgz`,点「开始导入」
7. 任务在「我的任务」表里看进度
8. 任务状态/操作:
   - **取消**(queued/running 的 eml-bundle 任务可点)
   - **重试**(failed/cancelled 任务点 → 整体重跑)
   - **只重试失败**(eml-bundle 失败任务,跳过原本就成功 / 跳过 /
     无 Message-ID / 判重出错的文件,只重跑真失败的几封)
   - **详情** 展开看每个文件:**跳过**(灰,邮箱已有重复)/
     **提醒**(黄,已入箱但有 caveat,如无 Message-ID / 判重查询出错)/
     **失败**(红,没入箱,reason 中文归类)
9. **大文件中断了不要慌**:网断 / 误关页签后回来,顶部会出现
   黄色提示条「上次有未完成的上传」,选回同样的文件即自动续传
   未完成的分片(基于 `localStorage` 记的 upload_id + 文件指纹)

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
