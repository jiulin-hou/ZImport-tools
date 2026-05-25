# 更新日志

版本号遵循语义化版本(主.次.补丁)。发版流程:

1. 在本文件顶部加一条新版本记录(`## vX.Y.Z — 日期` 加改动条目)
2. 运行 `bash deploy/release.sh X.Y.Z` —— 自动跑测试、写版本号、提交、
   打 tag、推送 main 与 tag、生成 `dist/zimport-tools-X.Y.Z.tar.gz`

## v1.1.0 — 2026-05-25

针对在 Zimbra 8.8.15 同机生产环境部署时暴露出的问题做的修复与改进。

**新功能**

- **`[server] web_origins` 白名单**:CSRF 校验的 Origin 头改用显式白名单
  (逗号分隔);旧版从 `rest_base` 推断 origin,但生产环境 `rest_base`
  指向 Zimbra 后端 URL(如 `:8443`)而浏览器访问的是反代主域,两者不
  一致导致 POST 全部 403。白名单留空时不校验 Origin —— 自定义
  `X-Zimport-CSRF` 头本身即可防御跨站表单。
- **任务表新增「跳过」列 + 详情展开**:展示按 Message-ID 跳过的邮件数;
  点 "详情" 看每个文件的跳过/失败原因(中文化),区分"本批内重复"
  和"邮箱内已存在"。
- **Zimlet 改用全屏 overlay**:Zimbra 8.8.15 DwtDialog canvas 是固定
  像素,大屏渲染太小。换成 zimlet JS 自己注入 `position:fixed; inset:0`
  的 overlay + iframe,自适应任何分辨率。

**Bug fix**

- `deploy/setup.sh` 服务账号存在性检测原本 grep `'name:'` 但 `zmprov ga`
  实际输出 `# name account@domain`(无冒号),漏判后试图重建账号会撞
  `ACCOUNT_EXISTS`。改用 `zmprov ga` 退出码判断,账号存在时用
  `zmprov sp` 把 config.ini 里的新密码同步过去。
- 前端 `index.html` / `app.js` 改用相对 URL(`api/...` / `static/...`),
  同一份前端在独立 29443 入口和 Zimbra 主域子路径
  `/zimport-tools/` 下都能正常工作。

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
