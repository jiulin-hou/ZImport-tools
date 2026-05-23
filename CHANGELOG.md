# 更新日志

版本号遵循语义化版本(主.次.补丁)。发版流程:

1. 在本文件顶部加一条新版本记录(`## vX.Y.Z — 日期` 加改动条目)
2. 运行 `bash deploy/release.sh X.Y.Z` —— 自动跑测试、写版本号、提交、
   打 tag、推送 main 与 tag、生成 `dist/zimport-tools-X.Y.Z.tar.gz`

## v1.0.0 — 2026-05-23

首个版本。从 ZImport `main` HEAD 派生,作为独立姊妹项目维护。

**与 ZImport 的差异:**

- **无独立登录** —— 移除 `/api/login` 与登录表单;只通过 Zimbra `ZM_AUTH_TOKEN`
  cookie 识别身份(`zimbra_session.validate` → `GetInfoRequest`)
- **CSRF 防护** —— 状态变更端点要求 `X-Zimport-CSRF: 1` 头 + `Origin` 校验
- **账户切换串号防护** —— session 中保存 cookie token 的 hash,token 变化即重建 session
- **token 验证缓存** —— LRU 1024 条,正缓存 5 分钟、负缓存 30 秒,保护 Zimbra QPS
- **Zimlet 应用页签** —— 在 Zimbra Web 顶部应用栏注册「数据导入」页签,iframe 内嵌 ZImport-tools 页面
- **部署适配** —— 系统路径 `/opt/zimport-tools` 等;反代路径默认 `/zimport-tools/`;
  systemd 单元改名 `zimport-tools-web.service` / `zimport-tools-worker.service`

**继承自 ZImport `main` HEAD 的功能(初始基线复制,之后各自演进):**

- 多 eml + 大 tgz 分片上传与断点续传
- 服务端解包归一化,规避 PaxHeader 故障
- SQLite 任务队列、后台串行 worker、进度持久化、保留期自动清理
- Message-ID 双层去重、transient 失败自动重试、单封失败可重试
- 文件夹下拉(`/api/folders`)、管理员目标账户 autocomplete(`/api/admin/accounts/search`)
- systemd 部署 + 环境准备脚本 `deploy/setup.sh`
