# 更新日志

版本号遵循语义化版本(主.次.补丁)。发版流程:

1. 在本文件顶部加一条新版本记录(`## vX.Y.Z — 日期` 加改动条目)
2. 运行 `bash deploy/release.sh X.Y.Z` —— 自动跑测试、写版本号、提交、
   打 tag、推送 main 与 tag、生成 `dist/zimport-tools-X.Y.Z.tar.gz`

## v1.4.5 — 2026-05-25

**「管理员目标账户」从文本输入 / autocomplete 改为真 `<select>` 下拉。**

之前 v1.4.0 的重构把目标账户改成了纯文本输入框,要管理员靠记忆敲账号名 ——
对几十个账户的小组织没必要这么硬。

- 新增 `zimbra_search.list_accounts(cfg, limit=500)` 和
  `GET /api/admin/accounts` 端点(管理员-only),用服务账号发
  `SearchDirectoryRequest`(`objectClass=zimbraAccount`)拉所有账户,
  按 name 排序返回
- 前端 `<input>` + `<datalist>` 换成 `<select id="targetAccount">`,
  管理员登录时一次性填充所有 `<option>`,显示格式
  `部门/姓名 — name@domain`
- **过滤掉不该出现在下拉里的账户**:
  - Zimbra 内置系统账户(`galsync.*` / `spam.*` / `ham.*` /
    `virus-quarantine.*`)
  - ZImport-tools 自己的服务账号(由 `cfg.svc_name` 配置,大小写不敏感
    匹配)—— 是工具内部代理身份,任何人导邮件进它都没意义
  - **当前登录的管理员自己**(前端过滤)—— 因为下拉第一项
    `(导入到自己)` 已经覆盖这种情况,再列一遍只让人迷惑
- `search_accounts`(给将来可能的搜索 UI 用)也加了相同的过滤,
  保持两套接口语义一致

## v1.4.4 — 2026-05-25

**真实部署中暴露的问题修复 + 任务管理 UX 改进。**

**Bug 修复**

- `setup-proxy.sh` 写死了 `python3` 命令,但 CentOS 7 上 `setup.sh` 编译装的是
  `/usr/local/bin/python3.11`(`make altinstall`,有意不创建 `python3`
  软链)。`install.sh` 阶段 3 在干净 CentOS 7 上必然挂。改为优先用
  `/usr/local/bin/python3.11`,回退到任意 `python3`

**新功能 / UX 改进**

- **任务详情行内展开** —— 原来所有任务的"详情"都灌进底下一个共享 div,
  多任务时上下来回滚、且只能看一个;改为点击行(或"详情"链接)
  **在该行下方直接插入一行展示详情**,可同时展开多个、各自独立、
  视觉上紧贴对应任务。轮询刷新(3 秒一次)会保留已展开的详情,不会
  把用户正在读的内容合上
- **失败任务现在也能看详情** —— 原来"详情"链接只在 `skipped+failed>0`
  时显示,任务整体失败(`status='failed'`,例如解包炸了、`delegate_token`
  拒了、磁盘满)`failed=0` 链接根本不出来,`t.error` 那段任务级原因
  被埋了;现在 `failed`/`interrupted`/`cancelled` 状态或有 `t.error`
  时也显示"详情",展开后 `<pre>` 里完整展示错误堆栈
- **`archive.unpack_tgz` 加 magic-byte 检测** —— 原来信任 `.tgz` 后缀直接
  `tarfile.open()`,撞上"名字叫 .tgz 但内容不是 gzip"(常见误命名:
  老 Unix `compress` `.Z`、ZIP、RAR、7z、空文件)会抛 Python 的
  `ReadError` 多方法堆栈,用户看天书。现在打开前 sniff 前 512 字节,
  识别 7 种常见格式,**给一句话能看懂的中文错误**告诉用户具体是哪种
  格式、怎么改
- **任务终态加"删除"按钮** —— 之前任务只能等保留期到了自动清,不能手动
  删。新增 `POST /api/tasks/<id>/delete`(login_required + CSRF + 鉴权:
  requester 或 admin)和前端"删除"链接,弹 confirm 后删 SQLite 行 +
  `rmtree` temp_dir。运行中/排队中的任务拒绝直接删(要先取消)
- **默认保留期 `retention_days` 从 7 天改为 3 天** —— 7 天觉得偏长,
  临时上传的大文件不该攒太久。已有部署改 `/etc/zimport-tools/config.ini`
  生效

**测试新增 7 个,总计 130 passed。**

## v1.4.3 — 2026-05-25

**真正的一键部署 + README 修正过时链接**

之前 README 说"#2 一键环境准备"但后面还有 #3 #4 #5 三个手动步骤,
总共 12 条命令。新增 `deploy/install.sh` 串起所有阶段:

1. `setup.sh`(环境 + venv + config + Zimbra 服务账号)
2. 拷贝 systemd unit → daemon-reload → enable --now
3. `setup-proxy.sh`(Zimbra 主 nginx 反代)
4. Zimlet 打包 + undeploy 旧版 + deploy 新版 + 强制启用 + flush
   cache

完成后打印"用户注销 Zimbra Web 重登就能用"。所有阶段都幂等,
失败重跑 install.sh 就行;分步排错单独跑子脚本也可以。

**其他**

- README 下载 URL 从 `v1.2.0` 改成 `v1.4.2`(过时)
- README「部署」段大幅简化:**一条命令 + 分步备用**,代替之前的
  五段教程
- `deploy/README.md` 重写:一键流程表 + 阶段表 + 分步备用
- `setup.sh` 末尾的多段"下一步"提示删除(install.sh 已经接管),
  改成一句话指引

## v1.4.2 — 2026-05-25

第 3 轮审计抓到的几项 —— 一个文档真错 + 两个工程层面的稳健性改进。

- **修 `setup.sh` 末尾的"下一步"指向已删的 29443 模式**:v1.2.0
  把 `--port` / 29443 模式删了,但 setup.sh 末尾的提示块还在让用户
  跑 `bash setup-proxy.sh --port 9443` —— 用户跟着做会失败。改成
  当前真实流程:`setup-proxy.sh`(单参)+ 部署 zimlet + Zimbra Web
  重登。
- **`web.py` 文件操作加 OSError 守卫**:
  - `start_import` 里 `os.listdir(input_path)` + `os.path.getsize`
    遇到 input_dir 被并发清理时直接抛 OSError → 500。改 try-except
    返 410 + "上传文件已不可用,请重新上传"
  - `retry_task` `os.listdir(input_dir)` 同样的 TOCTOU,加 try-except
    fallback 到空集
- **`zimbra_*` 全部 `requests.post` 改用 `with` 上下文**:之前直接
  `r = requests.post(...)` 不 close,长跑 worker 可能慢慢攒连接(连
  接池泄漏)。改成 `with requests.post(...) as r:` 自动释放。覆盖
  `zimbra_inject` 3 处、`zimbra_auth._soap`、`zimbra_folders` 2 处、
  `zimbra_search`。
- **`store.list_tasks` 加 `LIMIT 200` 默认值**:不分页是迟早问题,
  现在加防护,前端无需改动(返回行更少而已)。

**回归测试(+2)**
- `test_list_tasks_caps_at_limit`:守 limit 参数生效
- `test_import_410_when_input_dir_gone`:守 OSError → 410 而非 500

**测试 fixture 跟进**:`zimbra_*` 测试里的 `_Resp` / `_SoapResp` 假
对象都加了 `__enter__` / `__exit__` 让 `with requests.post(...)` 能
mock 出来。

## v1.4.1 — 2026-05-25

零零碎碎的"知道就好"项也都修了。

- **`store.recover_interrupted` 同时处理 `cancelling`**:worker 进
  程在标 cancelled 之前异常退出(SIGTERM/OOM/崩),任务会卡
  `cancelling` 永远不动,`purge_old` 不会清(它只看 done/failed/
  interrupted/cancelled)。改成 `WHERE status IN ('running',
  'cancelling')`,两种 in-progress 状态都被扫成 interrupted,可重
  试也会被定期清理。
- **任务行 hover 显示完整任务 ID**:`<tr title="完整任务 ID:...">`,
  排错时不用再去 sqlite 翻。
- **续传:点 file input 时清 `value`**:浏览器对"选了完全相同的
  文件"不触发 `change`,续传 UI 看起来没反应。仅在有 pending
  upload 时清空,普通用户无感。

**回归测试(+1)**
- `test_recover_interrupted_also_recovers_cancelling`:验证 a
  `running` task and a `cancelling` task both end up `interrupted`
  after recover_interrupted runs, while `done` is untouched.

## v1.4.0 — 2026-05-25

审计 v1.3.x 发现的"前后端不对齐 / 空头承诺"做集中收拾。**移除
dry_run 整个功能**(下面解释),其余几项坐实。

**移除:dry_run 预览**

承诺断裂:预览任务跑完是 `done`,前端"重试"只对 failed/interrupted/
cancelled 显示,所以预览完用户没法用 UI 触发真实导入,只能再上传
一遍同样的文件。tgz 模式上 dry_run 是干脆什么都不做就标 done(Zimbra
内部一次性 import,根本没机会"模拟")。**用户实际收益小、UI 表现
具有误导性**。把整个功能拿掉:

- `web.py` 删 `dry_run` 入参处理
- `store.create_task` 删 `dry_run` 参数(schema 列保留,向后兼容)
- `worker.py` 删 `is_dry_run` 分支 + tgz 空头承诺分支
- 前端 `index.html` 删"先预览"按钮、`app.js` 删 `runImport({dryRun})`
  路径、任务详情区不再显示"预览"徽标和"这是预览结果"说明
- 删 2 个相关测试

**修:tgz 任务运行中点取消会永远卡 cancelling**

Zimbra REST 一次性 PUT tgz,worker 没机会在中间感知 cancel。
v1.3.0 写 cancel 时假设所有任务都能感知。

修法:
- `web.py` cancel 端点对 `running + kind=zimbra-export` 直接返 400
  并解释"tgz 任务由 Zimbra 内部一次性处理"
- `worker.py` 进 inject_tgz 前最后再查一次 cancel(给 queued 阶段
  取消的用户机会;queued 仍可取消)
- 前端任务行对 running 的 tgz 任务不再显示"取消"链接

**修:续传 UI 实际无法工作**

`maybeOfferResume` 弹 confirm 让用户"重新选择同一文件",但 HTML
file input 不能 programmatically 触发,用户确认完什么都没发生。

修法:弹窗删掉,改成主卡片顶部一条**常驻黄色提示条**:
"上次有未完成的上传:foo.eml、bar.eml。在下方'选择文件'里选回
同样的文件即可自动续传剩余分片。[放弃这次未完成上传]"

主动选回相同文件 → 自动续传;选别的文件 → 后端 upload_id 不匹配
自然作废;点"放弃" → 立即清掉 localStorage。

**修:retry only_failed 没校验 keep_files 文件存在**

如果 `temp_dir/input/` 被(部分)清理,keep_files 里的文件可能
已经丢了,worker 跑出来直接 0 进度,用户无感。

修法:web 层创建新任务前 `os.listdir(input/)` 过滤掉不存在的;
全部不在就返 400 + 提示"原任务的文件可能已被清理"。

**改进:"+新建文件夹"prompt 默认值基于当前选中**

之前默认填写死的 `Inbox/新文件夹`。改成 `<当前选中>/新子文件夹`,
比如选了 `Inbox/2024/Q3` 点"+新建",默认值是
`Inbox/2024/Q3/新子文件夹`。

**改进:README 反映 v1.3.x 所有新功能**

重写「使用」段:`+新建`、任务备注、取消、重试、只重试失败、详情
区三段分类(跳过/提醒/失败)、续传体验都有说明。

**清理:5 处 lint(pyflakes)**

`web.py` 删 unused `archive` 导入;`tests/test_zimbra_auth.py` 删
unused `pytest`;`tests/test_web.py` 删 unused `_web` 局部 import;
`tests/test_worker.py` 两处 unused(`tid` 局部、`zimbra_inject`
局部 import)。

**回归测试(+2)**

- `test_cancel_running_tgz_rejected`:running 的 tgz 任务点取消必 400
- `test_retry_only_failed_filters_missing_input_files`:input/ 部分
  缺失时,keep_files 过滤掉不存在的并存到新任务

## v1.3.3 — 2026-05-25

**修"新建文件夹"对嵌套路径报 502 的 bug**

v1.3.0 引入的 `POST /api/folders` 把整个路径作为 `name` 字段传给
Zimbra `CreateFolderRequest`,但 Zimbra 拒绝含 `/` 的 name(返
`invalid name: Inbox/X`),所以任何带斜杠的路径都直接 502。前端
默认填的就是 `Inbox/新文件夹`,实际上必踩。

**修法**:`zimbra_folders.create_folder` 重写成递归 —— 拆路径,逐段
先 `GetFolderRequest path=...` 看父级在不在,在就拿 numeric id 作为
下一层的 `l` 参数,不在就 `CreateFolderRequest` 建出来。完全 idempotent
(已存在直接复用),也不需要预先建中间节点。

**新增 4 个回归测试**:
- 单段创建路径
- 嵌套多段(校验每个 `l` 参数都是上一层的真实 id)
- 完全已存在 → 不发任何 CreateFolderRequest
- 空路径 / 纯 `/` → 直接 FolderError

## v1.3.2 — 2026-05-25

让 dedupe 的两个"漏判静默"场景对用户可见。

**新增 failure code(WARNING 类,邮件仍入箱)**

- `no_message_id` — eml 没有 Message-ID 头(古早邮件、自家程序拼的、
  解析失败的 .eml 都会落到这里)。本来 worker 直接 inject 不做去重也
  不提示,用户无从感知"这封邮件每次重导都会重复入箱"。现在 inject
  照旧但 failures 数组里加一条 warning,前端任务详情区单独成块。
- `dedupe_check_failed` — Zimbra SOAP 查询失败(网络抖动、Zimbra
  Fault)。原本 `message_exists` 异常静默 → 返回 False → 邮件被当
  "不存在"再次 inject。现在 `batch_existing_message_ids` 返回
  `(existing, undecidable)` tuple,worker 把 undecidable 邮件标
  warning 入箱,提示用户"判重出错,已直接导入,建议手工确认"。

**ABI**

- `zimbra_inject.batch_existing_message_ids` 返回值由 `set` 改 `tuple`
  `(existing, undecidable)`。`message_exists` 行为保持不变(异常仍
  吞为 False);新内部 `_check_one` 区分"不存在"vs"查询失败",抛
  `DedupeCheckError`。
- worker 之外的调用方不变。

**前端**

- 任务详情区分 **跳过 / 提醒 / 失败** 三段(原本只有跳过 + 失败,
  warning 误归到失败误导用户)。
- "只重试失败"按钮现在不再把 warning 当成需重试 —— 那些邮件已经
  入箱了,重试只会造成真正的重复。

**回归测试(+3)**

- `test_batch_existing_returns_undecidable_on_soap_error`
- `test_process_task_no_message_id_inject_with_warning`
- `test_process_task_dedupe_check_failed_inject_with_warning`

## v1.3.1 — 2026-05-25

**修一个 v1.3.0 用户上报的真实 dedupe bug**

v1.3.0 引入的 `batch_existing_message_ids` 用 OR 合并的 SOAP search
减少 round-trip,但实现假设 Zimbra `SearchResponse` 的每个 hit 里带
Message-ID 字段 —— 实测 Zimbra 8.8.15 hits 只回 `cid/cm/d/e/f/fr/id/
l/rev/s/sf/su`(全是 envelope metadata),完全没有 Message-ID。结果:

- batch 函数永远返回空集
- worker 看不到任何"邮箱已存在"的命中
- **dedupe 静默失效**:之前已经在邮箱里的邮件,预览跑完显示"全部
  会导入"、真实导入也会再次写入造成重复

**修法**:`batch_existing_message_ids` 内部退回到逐封 `message_exists`
查询(每封一次 SOAP)。性能从理论 N/50 退到 N,但 Zimbra 同机 LDAP
search 单次约 10ms,1000 封 dedupe ≈ 10 秒,可接受。worker 接口不变,
未来真正解决"如何从 batch 查询拿 Message-ID"再优化回来。

**回归测试**

- `test_batch_existing_message_ids_returns_only_present`:验证函数
  契约——传入 mid 列表,返回邮箱实际存在的子集
- `test_process_task_dry_run_marks_existing_as_skipped`:端到端守
  用户上报的场景——已存在的邮件预览必须 skipped=1 而不是 done=1

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
