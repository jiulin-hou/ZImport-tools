# 更新日志

版本号遵循语义化版本(主.次.补丁)。发版流程:

1. 在本文件顶部加一条新版本记录(`## vX.Y.Z — 日期` 加改动条目)
2. 运行 `bash deploy/release.sh X.Y.Z` —— 自动跑测试、写版本号、提交、
   打 tag、推送 main 与 tag、生成 `dist/zimport-tools-X.Y.Z.tar.gz`

## v1.3.0 — 2026-05-25

围绕"邮件导入流程 / 检测提示"的体验和能力升级。后端 API 扩展 +
前端大改。测试 101 → 110。

**新功能**

- **dry_run 预览**:`POST /api/import` 接受 `dry_run=true`,worker
  跑去重 + 模拟 inject 但不实际写入。结果就是预览,前端按"先预览"
  按钮触发,完成后任务上有「预览」标记,看完点"重试"即可真实导入。
- **取消任务**:`POST /api/tasks/<id>/cancel`(owner / admin 才能),
  queued 任务直接 cancelled,running 任务进入 cancelling,worker
  在下一封 eml 前感知并停止(已 inject 的邮件不回滚)。
- **新建文件夹**:`POST /api/folders { path }` 走 Zimbra SOAP
  `CreateFolderRequest`,前端目标文件夹旁多一个"+新建"按钮,
  不再需要切到 Zimbra Web 建好再回来。
- **任务备注**:`api/import` 接 `label`,任务表多一列。
- **只重试失败**:`POST /api/tasks/<id>/retry { only_failed: true }`
  通过原任务的 failures 提取所有非 duplicate 文件名,新任务 `keep_files`
  限制 worker 只处理这些(其他文件直接跳过);避免对 1000 封里只
  3 封失败的任务整批重跑。
- **失败原因中文归类**:`InjectError` 加 `code`(network / transient /
  quota / permission / invalid / unknown),`reason` 直接是中文短句;
  前端按 code 分组展示。再也不会给用户看 Zimbra 返回的英文 HTML 错误页。
- **`timestamp=0` 给 tgz 导入**:让 Zimbra 用每封邮件 Date 头作 received
  date 而不是导入时间。
- **续传 UI**:`uploadFile` 先 query `/api/upload/status` 拿 missing
  分片集,只补传缺失部分;`upload_id + 文件指纹` 存 `localStorage`,
  浏览器刷新后提示用户"发现未完成的上传,重新选同一文件继续"。
- **上传进度阶段化 + ETA**:上传 / 排队 / 处理 三段独立显示,上传
  阶段滚动平均最近 10 片速率算 ETA。
- **失败原因展示按归类**:任务详情按 `code` 把失败分类汇总,跳过
  独立成块(原本和失败混在一起)。

**性能**

- **batch dedupe**:worker 进 eml 循环前一次性 SOAP 查询所有
  Message-ID(每页 50 条),从 N 次 SOAP 降到 N/50。1000 封邮件
  从理论 1000 次 SOAP 降到 20 次。`zimbra_inject.batch_existing_message_ids`。

**Schema**

- `tasks` 表新增 `label TEXT`、`dry_run INTEGER`、`keep_files TEXT`
  三列;status 枚举新增 `cancelling` / `cancelled`(`purge_old`
  把 cancelled 也算"可清理"状态)。所有变更走 idempotent `ALTER TABLE`,
  老 DB 升级无需手动迁移。

**前端**

- 重写 `app.js`(229 → 425 行),拆出 `runImport` / `uploadFile` /
  `actionLinks` / `bindActionLinks` / `showTaskDetail` / `toast` 等。
- 任务行操作:`详情 · 取消 · 重试 · 只重试失败` 根据任务状态动态显示。
- 取消 / 创建文件夹 / retry 走 toast 反馈,不再 `alert`。

**测试覆盖(+9)**

- import 带 label + dry_run 持久化
- cancel queued / running / done 状态机
- cancel 越权 403
- create_folder 正常 + 拒收 unsafe path
- retry only_failed 计算 keep_files 正确(排除 duplicate)
- retry only_failed 无非 duplicate 失败时返回 400

## v1.2.3 — 2026-05-25

完整一轮代码 review + 安全审计 + 测试覆盖评估发现的问题修复。
测试数量 79 → 101。

**安全(必修)**

- **修真实 XSS**:任务表 / 任务详情之前用 `innerHTML` 拼接
  `t.account`、`f.name`(用户上传文件名)、`f.reason`(可能包含
  Zimbra 服务器返回的错误文本)。管理员可上传文件名为
  `<img src=x onerror=...>` 的 eml,任务失败后任何打开"详情"的人
  即执行任意脚本。`static/app.js` 加 `esc()` 包所有 innerHTML 插值。
- **修 REST URL 注入**:`zimbra_inject.py` 之前用 `"%s/home/%s/%s"`
  直接拼 `account`/`folder` 到 Zimbra REST URL。`folder` 普通用户
  可控,若提交 `Inbox?fmt=tgz&query=in:Spam` 可改变请求语义。改用
  `urllib.parse.quote`,并在 web 层 `_safe_folder()` 拒绝 `..`、
  `?`、`#`、`%`、控制字符、超长字符串。
- **TLS 校验替代 verify_tls=false**:新增 `[zimbra] ca_bundle`
  字段;`Config.tls_verify()` 优先返回 CA bundle 路径而非布尔。
  `setup.sh` 探测同机 Zimbra 时自动填
  `ca_bundle = /opt/zimbra/conf/ca/ca.pem` + `verify_tls = true`,
  使 svc_password 等敏感请求有真正的 CA 链验证而不是裸 TLS。

**鲁棒性(P1)**

- `web.py` 参数缺失/坏值返回 400 而非 500:`upload_chunk` /
  `upload_status` / `start_import` 全部改用 `.get()` + `_bounded_int()`
  解析,无效输入(缺字段、非数字、超出 `[0, 10000)`)统一 400。
- `start_import` 增加 folder 字符白名单校验(见上 URL 注入修复)。
- `worker.process_task` 顶层 `except Exception` 补完整注释说明
  为什么需要 catch-all(避免一封 eml 的解析错把整个 worker 进程
  搞死,后续任务永远卡在 running)。
- 管理员重试他人失败任务时 **保留原任务的 `requester`**,原作者
  仍能在 `/api/tasks` 看到新任务,授权语义对齐。
- 删 `zimbra_auth._admin_token` 死代码别名(无人调用)。

**测试覆盖(P0 + P1)**

- 新增 `get_task` 越权测试(任何登录用户拿别人 task_id 必 404)。
- 新增 **CSRF 参数化测试**:`/api/upload/init` `/api/upload/chunk`
  `/api/import` `/api/tasks/<id>/retry` `/api/_test_csrf` 全部断言
  "缺 X-Zimport-CSRF → 403"。任何人新加 POST 端点忘装 `login_required`
  会立刻被抓住。
- 新增 `start_import` 413(超出 `max_task_bytes`) / 507(磁盘剩余
  不足)测试。
- 新增 7 个 folder 注入 case 的参数化拒收测试。
- 新增 admin 正向测试:`body.account` 真的能路由到指定账户,且
  retry 保留原 requester。
- 新增 `upload_chunk` 缺字段 / 超大 index 返回 400 测试。
- 新增 worker tgz inject 失败路径 + retry 残留 work 自动清理测试。
- 新增 `store.purge_old` 不删 queued/running 安全闸口测试。
- 新增 archive 大小写 `.EML` / `.Eml` 识别测试。

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
