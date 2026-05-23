# ZImport-tools

ZImport 的 Zimbra 内置工具版 —— 作为 Zimbra Web 应用栏里的「数据导入」页签存在,
直接信任 Zimbra 会话 cookie,**无独立登录步骤**。

姊妹项目:[ZImport](https://github.com/jiulin-hou/ZImport) 是独立 Web 工具形态。
两者并行维护、独立版本线。

## 它解决什么问题

用户已经登录在 Zimbra Web,不应该为了导入邮件再单独登录一次;独立 Web 工具会额外
新增公网暴露面。ZImport-tools 把导入做成 Zimbra Web 里的内置功能:共用 Zimbra 会话、
不新增公网攻击面、不再二次登录。

## 功能

- 多 `.eml` 文件 / 大体积 `.tgz`(>5GB,分片上传 + 断点续传)
- 自动 Message-ID 双层去重
- 后台串行 worker,任务队列持久化,关页签也不丢
- 失败任务可重试;管理员可指定目标账户
- 服务端解包归一化,从根本规避 Zimbra 导入器的 PaxHeader 故障

## 架构

- 单页前端 + Flask 后端 + 独立 worker 进程(同 ZImport)
- **前端登录**:从 Zimbra `ZM_AUTH_TOKEN` cookie 直接识别,**无登录表单**
- **CSRF 防护**:状态变更端点要求 `X-Zimport-CSRF: 1` 头 + `Origin` 校验
- **账户切换串号防护**:session 中保存 cookie token 的 hash,token 变化即重建 session
- **token 验证缓存**:LRU 1024 条,正缓存 5 分钟、负缓存 30 秒,保护 Zimbra QPS
- **管理员 SOAP 操作**(账户搜索 / 委托认证)统一走配置的服务账号
- Zimlet 在 Zimbra Web 应用栏注册「数据导入」页签,内容 = iframe `/zimport-tools/`

详细设计见 ZImport 仓库的
[`docs/superpowers/specs/2026-05-23-zimport-tools-design.md`](https://github.com/jiulin-hou/ZImport/blob/main/docs/superpowers/specs/2026-05-23-zimport-tools-design.md)。

## 环境要求

- 目标机:运行 Zimbra 的服务器(共用其 nginx)
- 操作系统:CentOS 7(其它 Linux 亦可,部署脚本针对 CentOS 7 编写)
- Python 3.8+(`setup.sh` 会并行安装 3.11,不动系统 python3)

## 在新机器上下载与部署

### 1. 获取代码

    git clone https://github.com/jiulin-hou/ZImport-tools.git
    cd ZImport-tools

或下载指定版本:

    curl -LO https://github.com/jiulin-hou/ZImport-tools/archive/refs/tags/v1.0.0.tar.gz
    tar xf v1.0.0.tar.gz && cd ZImport-tools-1.0.0

### 2. 一键环境准备

    sudo bash deploy/setup.sh

并行装 Python 3.11、建 venv、放配置模板;不动系统 python3,对 Zimbra 无影响。

### 3. 反代到 Zimbra 同域名

    sudo bash deploy/setup-proxy.sh --path /zimport-tools

`ZM_AUTH_TOKEN` cookie 才会被自动带到 ZImport-tools 后端。

### 4. 部署 Zimlet

    cd zimlet && bash build.sh
    su - zimbra -c "zmzimletctl deploy $(pwd)/com_msauto_zimport_tools.zip"

Zimbra Web 应用栏自动出现「数据导入」页签。

### 5. 完成手工步骤

`setup.sh` 跑完会打印剩下步骤(填配置、建 Zimbra 服务账号、启动 systemd 服务)。
详见 [`deploy/README.md`](deploy/README.md)。

## 使用

1. 登录 Zimbra Web
2. 点顶部应用栏的「数据导入」页签
3. (管理员)填目标账户,或留空导入到自己;选目标文件夹
4. 选多个 `.eml` 或一个 `.tgz`,点「开始导入」
5. 任务在「我的任务」表里看进度;关页签稍后再看也行

## 发版

编辑 `CHANGELOG.md` 加新版本段落,然后:

    bash deploy/release.sh X.Y.Z

## 目录结构

    zimport_tools/  后端模块 + 前端 static/
    zimlet/         Zimbra Zimlet 包(经典 8.8.15 框架)
    tests/          单元测试
    deploy/         setup.sh / setup-proxy.sh / release.sh / systemd 单元 / update.sh
