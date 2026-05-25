# 更新日志

版本号遵循语义化版本(主.次.补丁)。发版流程:

1. 在本文件顶部加一条新版本记录(`## vX.Y.Z — 日期` 加改动条目)
2. 运行 `bash deploy/release.sh X.Y.Z` —— 自动跑测试、写版本号、提交、
   打 tag、推送 main 与 tag、生成 `dist/zimport-tools-X.Y.Z.tar.gz`

## v1.2.2 — 2026-05-25

收拾 v1.2.1 审计时归到"非 bug 但可改"的几项小事。

- **`__version__` 真正被消费**:新增公开端点 `GET /api/version` 暴露
  当前部署版本号;前端页脚显示 `ZImport-tools vX.Y.Z`。运维和用户
  能直接看到部署没没成功。
- **前端局部变量 `session` → `identity`**:避免和 Flask session 概念
  混淆(虽然 v1.2.0 后端已经没 session 了)。
- **`deploy/release.sh` 测试路径**:优先用项目 `venv`,其次任何可用
  的 `python3 -m pytest`,都没有才警告跳过 —— 消除每次发版要手动
  `ln -s` venv 软链的绕路。
- **`deploy/release.sh` 自动建 GitHub Release**:检测到 `gh` 已装且
  `gh auth status` 通过时,自动 `gh release create` 挂交付包 +
  从 CHANGELOG 提取本版段落作 release notes。`gh` 缺失则跳过(打印
  手动命令),不阻断发版。需要这功能的开发机执行一次:
  `sudo apt install -y gh && gh auth login`。

## v1.2.1 — 2026-05-25

清理 v1.2.0 改完后留在仓库里的几处死代码 / 误导注释。

- **删 `cfg.chunk_size`**:配置字段没有任何代码消费 —— 前端 `app.js`
  把分片大小写死成 10MB,后端 `uploads.save_chunk` 不需要这个值。
  同步从 `config.example.ini` 和测试 fixture / `test_config.py` 删除。
- **删 `zimbra_session.token_hash`**:v1.2.0 把 Flask session 删掉后,
  这个用来防 session 串号的 SHA256 helper 没人调用了,连同 `hashlib`
  import 一并清掉。
- **清 `config.py` 误导注释**:之前注释写"保留 secret_key 字段读取
  兼容旧 config.ini",但代码根本没读 —— 旧 config.ini 里有这字段也
  不会出错(被忽略),所谓"兼容"是空话。直接删注释。

## v1.2.0 — 2026-05-25

聚焦把项目精简为「纯 Zimbra 工具组件」单一形态,去掉所有"独立机器
部署"残留 + 简化鉴权/配置/CSRF 三块。

**简化**

- **鉴权改 stateless**:删 Flask session、删 `SESSION_COOKIE_*` 设置、
  identity 通过 `flask.g` 在请求范围内传递;每个请求直接
  `zimbra_session.validate(token)`(LRU 5 分钟缓存兜底),用户切换
  Zimbra 账号瞬间识别到新身份,无 session 串号风险也不再需要 hash
  比对。配置文件的 `[server] secret_key` 字段不再被使用(保留读取
  兼容旧 config.ini)。
- **删 `[server] web_origins` 白名单**:CSRF 防御只靠 `X-Zimport-CSRF`
  自定义头(浏览器跨站表单设不了 X-* 头,够防御);少一项生产环境
  踩坑配置。
- **`setup-proxy.sh` 只做一件事**:在 Zimbra 主 443 server 块加
  `location ^~ /zimport-tools/` 反代 8088。删掉早期的"独立 29443
  端口"模式(原本是给"非 Zimbra 同机"场景留的退路)。
- **`setup.sh` 不再生成 `secret_key`**:无 Flask session 后这玩意儿
  没有用。
- **README + deploy/README 重写**:明确单一部署线 "Zimbra 同机 +
  zimlet 唯一入口",删掉所有"独立机器/独立端口/多端口选项"段落。

**向后兼容性**

- 旧 `/etc/zimport-tools/config.ini` 里如有 `secret_key` /
  `web_origins` 字段不需要手动删除(被忽略)。
- 旧 `nginx.conf.zimport-tools` 文件(29443 listener)如有遗留也不
  需要手动删,但建议清掉避免 nginx 多绑无谓端口。

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
