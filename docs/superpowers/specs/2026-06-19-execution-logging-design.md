# 执行日志系统(Execution Logging)— 设计

日期:2026-06-19
状态:待评审(brainstorming 已确认全部关键决策)

## 目标

让运维 / 专业用户能在 Web 上看到"后台到底执行了什么"——每次 worker 调用的**渲染后提示词、容器内执行的命令、agent 原始输出**,以及"某个结果(fact / intent / conclude)是怎么来的"。

具体两件事:
1. **新增独立 `Runtime` 标签页**:近实时展示后台执行记录流(命令输入 + 智能体输出)。
2. **结果框绑定**:在 `Detail` / `Log` 时间线里每个 INTENT / CONCLUDE / FACT 框上加 **copy** 与 **detail** 两个小图标——detail 弹窗显示"这个结果由谁、用什么 model、什么命令、什么提示词、产生了什么输出"。

## 背景与现状(为什么现在做不到)

- 现有 Web 的 **"Log" 标签其实是"图时间线"**:它只把 facts/intents 这些**结果**按时间排列(`index.html` 的 `timelineEvents()`),从不含"怎么得到的"。
- "怎么得到的"三要素——渲染后提示词、执行命令(argv)、agent stdout——在 `dispatcher/tasks/common.py` 的 `run_worker_process` 运行时全部存在,但**进程结束即丢弃**,仅 `LOG.info` 打到 dispatcher 控制台、失败时打个预览,**从不持久化**。
- 关键架构边界:**dispatcher 与 server 是两个独立进程、独立文件系统**(见 `docker-compose.yaml`:server 挂 `./datas/cairn/ → /root/.local/share/cairn/`,dispatcher 仅挂 docker.sock 与 dispatch.yaml)。Web 只读 server 的 sqlite(`server/db.py`)。
- 推论:要在 Web 看到执行细节,**必须由 dispatcher 把执行记录"上报"给 server,server 落库 + 落盘 + 暴露接口,前端渲染**。落盘文件也**必须由 server 拥有**(谁拥有 DB 谁拥有文件),否则删除项目时 server 删不掉 dispatcher 的文件 → 产生孤儿文件占盘。

## 数据模型(`server/db.py`)

新增 `executions` 表,外键挂 `projects(id)` 且 `ON DELETE CASCADE`(与 facts/intents 一致):

| 字段 | 说明 |
|---|---|
| `id` TEXT | 执行记录 id(如 `exec_0007`,项目内自增,复用 `scoped_counters`) |
| `project_id` TEXT | 外键 → projects.id,CASCADE |
| `phase` TEXT | `bootstrap` / `reason` / `explore` / `conclude` |
| `intent_id` TEXT NULL | 关联 intent(reason/bootstrap 可空) |
| `worker_name` TEXT | 执行的 worker 名 |
| `model` TEXT | 使用的 model |
| `command` TEXT | **脱敏后**的完整 argv(JSON 数组文本) |
| `prompt` TEXT | 渲染后的完整提示词(不截断) |
| `response_text` TEXT | driver 解析出的最终回答(单独存,封顶 64KB) |
| `stdout_inline` TEXT | stdout 截断版(首尾各 32KB,合计 ≤64KB) |
| `stderr_inline` TEXT | stderr 截断版(同上) |
| `stdout_bytes` INTEGER | 原始 stdout 字节数(用于 UI 显示"已截断") |
| `truncated` INTEGER | 内联是否被截断(布尔) |
| `exit_code` INTEGER NULL | 进程退出码 |
| `outcome` TEXT | `success` / `failed` / `timeout` / `cancelled` |
| `started_at` / `ended_at` TEXT | ISO 时间 |
| `duration_ms` INTEGER | 耗时 |
| `produced_fact_id` TEXT NULL | 本次产生的 fact(explore/bootstrap) |
| `produced_intent_ids` TEXT NULL | 本次产生的 intent id 列表(reason),JSON 数组文本 |
| `log_path` TEXT NULL | **完整 `.log` 文件的绝对路径**;为空 = 无可下载文件 |

DB 内联只保留截断版与结构化字段(供 Runtime 标签 / detail 弹窗快速渲染,**不读文件**)。完整版只在 `.log` 文件里。

**外键约束(关键,见 reopen 分析):只有 `project_id` 建外键且 `ON DELETE CASCADE`。** `intent_id` / `produced_fact_id` / `produced_intent_ids` 一律是**普通可空文本列、不建外键**。原因:reopen 会 DELETE "完成 intent",若 `intent_id` 挂了 CASCADE 外键,reopen 会连带删掉执行历史——这是不允许的。执行记录是**不可变的历史**,只随**项目**删除而删除。

`scoped_counters` 增加一个 `kind='execution'` 用于项目内 exec id 自增。**exec_id 必须复用 facts/intents 同款的原子自增模式**(单事务内 `UPDATE … value=value+1` 后读取),保证多项目 / 多 worker 并发上报时不会拿到重复 id。

## 落盘(server 拥有)

- **位置**:`<DATA_ROOT>/executions/<project_id>/<时间戳>-<phase>-<intent或no_intent>-<exec_id>.log`
  - `<DATA_ROOT>` = DB 所在目录(本地 `~/.local/share/cairn`,compose 下宿主 `./datas/cairn/`)。
  - 文件名例:`2026-06-19-01-32-26-explore-i012-exec_0007.log`(时间戳在前便于排序,带 phase/intent 便于肉眼定位,**结尾带 exec_id 保证唯一**——同项目多 worker 并发时同一秒可能撞名,exec_id 兜底唯一性)。
- **按项目分子目录**:删项目时一条 `rm -rf <DATA_ROOT>/executions/<project_id>/` 即可清空,无需逐文件查。不同项目天然落在不同目录,**跨项目零串扰**。
- **原子落盘**:server 把整文件一次写到同目录临时文件再 `rename` 到正式名(原子替换),避免下载 / 打包时读到半写状态。文件 I/O 放在 DB 写事务**之外**,不在持有 sqlite 写锁期间做磁盘 I/O,降低并发争用。
- **文件格式**(自包含,分节):
  ```
  === META ===
  exec_id / phase / worker / model / outcome / duration_ms / started_at / ended_at /
  produced_fact_id / produced_intent_ids
  === COMMAND ===
  （脱敏后的完整 argv）
  === PROMPT ===
  （渲染后的完整提示词）
  === STDOUT ===
  （完整输出）
  === STDERR ===
  （如有）
  ```
- **单文件硬上限 10MB**:整文件超 10MB 时对输出部分做**首尾保留**(头尾各保留、中间插 `…… [已截断 N 字节] ……`),保证结尾的结论 / 报错栈不丢。

## 截断规则(只截"原始输出流",防止关键信息丢失)

永不截断:`prompt`、`command`、META、`response_text`(单独 64KB 限额,极少触顶)。

截断只作用于 stdout/stderr,且采用**首尾保留**(非简单截尾,避免丢掉结尾结论 / 报错):

| 层级 | 上限 | 策略 |
|---|---|---|
| DB 内联(`stdout_inline`/`stderr_inline`) | **最大 64KB 首尾**(头 32KB + 尾 32KB) | 中间插"已截断 N 字节" |
| 落盘 `.log`(完整版) | **每条硬上限 10MB** | 同样首尾保留,尾部留报错 / 结论 |
| `response_text` | 64KB | 触顶尾部标记 |

按字节计并对齐 UTF-8 边界,不切坏多字节字符。所有上限放进配置(server settings + dispatch 默认),可调。

## 密钥脱敏(强制)

opencode 的命令把 provider 配置(**含 apiKey**)塞进了 argv;原样存会把密钥泄漏进 DB / Web / `.log`。

- 新增脱敏工具:对 `command`(argv)与 env 中匹配 `apiKey` / `api_key` / `token` / `authorization` / `secret` / `Bearer …` 等的值做掩码(如 `sk-****`)。
- **在 dispatcher 上报前脱敏**;server 入库前再兜底脱敏一次(双保险)。
- META 段写入 `.log` 前同样走脱敏。

## 上报通路(dispatcher)

- `CairnClient` 新增 `report_execution(project_id, payload)` → `POST /projects/{id}/executions`。payload 含结构化字段 + **完整 stdout/stderr**(供 server 落盘)+ 截断标记。
- 在 `dispatcher/tasks/{bootstrap,explore,reason}.py` 各自的执行(及 conclude 阶段)完成后调用,带上 `produced_fact_id`(`write_conclude_result_with_fact_id` 已返回)/ `produced_intent_ids`(reason 产出)。
- **采集成本受开关控制**(见下):关闭时 dispatcher 直接不采集、不上报。

## 日志开关(两级,可配置 / Web 实时开关)

权威源在 server 的 `settings` 表(已存在 `intent_timeout` / `reason_timeout`),新增两个布尔:

1. `execution_record_enabled`(执行记录 / DB):**默认开**。占用极小,驱动 Runtime 标签 + 结果框 detail 绑定。
2. `execution_file_logging`(完整 `.log` 落盘):**默认开**。磁盘大头主开关;关闭即不再写 `.log`、不提供下载,但 Runtime/detail 仍可看 64KB 内联版。

机制:
- dispatcher 每次上报前通过已有的 `CairnClient.get_settings()` 读取开关:`execution_record_enabled` 关 → 完全不采集 / 不上报;开但 `execution_file_logging` 关 → 上报但不带完整输出(只内联)。
- server 在 `POST /projects/{id}/executions` 再校验一次(双保险):落盘开才写 `.log` 并填 `log_path`,否则 `log_path` 为空。
- **下载联动**:`log_path` 为空 / 文件不存在 → 前端隐藏下载图标;`GET /executions/{id}/log` 与 `logs.zip` 对不存在的文件返回 404。
- `dispatch.yaml` 可选提供初始默认值;运行时一律以 server settings 为准(Web 可随时改、即时生效)。

`Settings` pydantic 模型与 `GET/PUT /settings`、`settings` 表 schema、Web 齿轮(⚙)面板同步加这两个开关。

## 接口(server `routers/executions.py`,新建)

- `GET /projects/{id}/executions` — Runtime 标签数据源。返回结构化字段 + 内联截断输出(不含完整输出),按 `started_at` 排序;支持可选分页 / limit。
- `GET /executions/{id}` — 单条详情(结构化字段 + 内联输出 + `log_path` + `has_log`)。
- `GET /executions/{id}/log` — 下载**单条** `.log`(`Content-Disposition: attachment`);文件不存在 → 404。
- `GET /projects/{id}/executions/logs.zip` — 把该项目 `executions/<project_id>/` 目录下所有 `.log` **打包为一个 zip** 下载;无文件 → 404。

## 项目删除联动(杜绝孤儿文件)

`routers/projects.py` 的 `delete_project`(L147)中,在删除项目前 / 后:
- best-effort `shutil.rmtree(<DATA_ROOT>/executions/<project_id>/, ignore_errors=True)`(失败仅 warning,不阻断删除);
- `executions` 表行经既有外键 `ON DELETE CASCADE` 自动清除。

server 同时拥有 DB 与文件,单点清理,无跨进程死角。

## 前端(`server/static/index.html`,AlpineJS 单文件)

### Runtime 标签
- 在 `Detail` / `Hints` / `Log` 旁新增 **`Runtime`** 标签(现有 `Log` 图时间线保留不动)。
- 轮询 `GET /projects/{id}/executions`(复用现有轮询刷新机制)实现近实时。
- 列表每条:`时间 / phase / worker / model / outcome 徽章 / 耗时`;点击展开看 **命令 + 提示词 + 输出**;有 `log_path` 时显示 **"下载本次日志"**;标签页顶部提供 **"下载全部日志(zip)"**。
- 顶部显示当前两个开关状态;关闭时给出"日志已关闭"提示。

### 结果框 copy / detail 图标
- 在 INTENT / CONCLUDE / FACT 框右上角加两个小图标:
  - **copy**:复制该结果文本(description / 结论)。
  - **detail**:弹窗显示对应执行记录——按 `produced_fact_id`(FACT / CONCLUDE)或 `produced_intent_ids` 含该 intent(INTENT)关联到 execution;展示 worker / model / 命令 / 提示词 / 内联输出,有文件则给下载。
- 关联在前端用已拉取的 executions 列表本地匹配;匹配不到(如日志关闭期间产生的结果)则 detail 图标置灰或提示"无执行记录"。

## 多项目并行与隔离

调度器并发运行多个项目(`max_running_projects`)、单项目多 worker(`max_project_workers`),因此会有并发的 `report_execution` 同时打到 server。隔离与防串扰约束:

- **键全程带 project_id**:上报 payload → DB 行 → 文件目录 → 查询接口,执行记录全程以 `project_id`(+ `intent_id`)为键;dispatcher 每个 task 只上报自己上下文的 project/intent,**无共享可变状态**,不存在跨项目写错归属。
- **exec_id 原子分配**:复用 facts/intents 的 `scoped_counters` 原子自增(单事务 `UPDATE…value=value+1` 再读),并发上报不会重号。
- **文件名唯一**:文件名带 `exec_id`,同项目同秒并发也不撞名;不同项目落不同目录,跨项目不可能混。
- **写并发**:沿用现有 `get_conn()`(WAL,写串行化)。保持执行记录的 DB 事务短小,**≤10MB 文件写在事务外**(临时文件 + rename),避免长时间持锁;SQLITE_BUSY 的处理沿用现有约定。
- **读 / 打包并发**:`logs.zip` 与单条下载只读已 rename 完成的完整文件;正在写入的执行尚无正式文件名,不会被读到半截。
- **Runtime 标签按当前项目过滤**:前端只拉 `GET /projects/{当前id}/executions`,不混入其它项目。

## 与 reopen / replay 的关系

### reopen(`POST /projects/{id}/reopen`)

**语义背景(reopen 到底是什么操作):** reopen **本身不执行任何 agent/worker/容器**,是一次纯服务端的**图状态编辑 + 重新激活**——既不是"从头重跑",也不是"断点续跑某个暂停的执行"。

Cairn 的"完成"定义为:存在唯一一条 `to_fact_id = 'goal'` 的 intent(`get_completion_intent_or_409`),项目 status = `completed`。reopen 的步骤:

1. 校验项目为 `completed`,找到该"完成 intent"(`→ goal`)及其源事实。
2. 取人类填写的 `description`(反馈)+ `creator`。
3. **DELETE 该"完成 intent"**(撤销"已够到目标"的声明)。
4. **新建 fact** = 人类反馈文本。
5. **新建一条已 concluded 的 `external_feedback` intent**:`from` = 原源事实,`to` = 该反馈 fact(把反馈作为带来源的新事实接进图)。
6. 清 reason 租约,status 改回 `active`。

请求返回时没跑过一行 agent 代码。**"继续干活"由 dispatcher 常规循环触发**:它发现项目重新 `active` 后,对**当前完整的图**(全部旧 fact/intent 原封不动 + 新反馈 fact)重新跑 reason,reason 基于含反馈的全部信息再决定下一步(提新 intent 去 explore,或再次宣告 `→ goal`)。即:**从"当前图状态"继续**,历史零清除。类比"给已关闭工单加评论后重开":历史全在,多一条说明,状态回到进行中。

**约束(执行日志相关):**

- **执行历史不可被 reopen 抹除**:`executions.intent_id` 等**不建 CASCADE 外键**(见数据模型),因此 reopen 删除"完成 intent"时,**不会**连带删掉那条曾经 conclude 项目的执行记录——它作为历史保留。
- **悬空绑定优雅降级**:被删 intent 的 detail 绑定会失效(intent 没了),执行行仍在;Runtime 标签照常显示该次执行。
- **人工产物无执行记录**:reopen 新建的 fact / intent 由人工产生、无 worker 执行,其 detail 图标显示"无执行记录"(与"日志关闭期间产生的结果"同样处理)。
- **reopen 后继续累积**:项目重新 active 后的新 explore/reason 产生新 exec_id、新 `.log`,追加到同项目目录与时间线;**跨多轮 reopen 的执行历史完整累积**。

### replay(前端 `startProjectReplay`)
纯前端动画,按时间步进重放已有图时间线,**不调后端、不重跑、不产生执行记录**。约束:

- replay **不触发**任何 `report_execution`,对执行日志数据零影响。
- UI 行为与现有约定一致:`replay.active` 时,结果框上的 **copy / detail 图标隐藏**(与其它在重放时 `x-show="!replay.active"` 的控件保持一致),避免在动画态点开实时详情造成困惑;退出重放后恢复。
- Runtime 标签独立于 replay(replay 只动画化图画布);为简洁起见,重放进行时 Runtime 标签按现有侧栏行为正常显示完整数据,不参与逐帧动画。

## 实时性说明(YAGNI)

按**阶段近实时**:dispatcher 现在是"进程结束才拿到输出",每个 phase 完成写一条,前端靠现有轮询在数秒内刷新。**不做逐 token 流式**(需重构进程读取 + SSE/WebSocket,留作后续)。

## 测试

- server:`executions` 表 CRUD;`POST/GET` 接口;删项目级联删行 + 删目录;落盘文件内容与 META 分节;开关关闭时不落盘 / `log_path` 为空;`.log`/`logs.zip` 404 行为。
- 脱敏:argv / env / META 中的 apiKey / token / Bearer 被掩码;非密钥不误伤。
- 截断:首尾保留正确、UTF-8 不切坏、64KB 内联 / 10MB 文件上限生效、`truncated` 标记与 `stdout_bytes` 正确。
- dispatcher:`report_execution` 在开关开 / 关时的上报行为;produced id 透传。
- **并发隔离**:并发上报同一项目不重 exec_id、文件名不撞;两个项目上报互不污染(各自目录 / 行);删 A 项目不影响 B 的记录与文件。
- **reopen 不抹历史**:对已完成项目 reopen 后,原 conclude 的执行记录仍存在(`intent_id` 无 CASCADE);reopen 新建的 fact/intent 在接口上标记为无执行记录。
- **原子落盘**:模拟读到正在写的文件——只会看到 rename 后的完整文件或不存在,绝不读到半写内容。
- 前端:手测(Runtime 标签渲染、下载、copy/detail 绑定、开关联动、replay 时图标隐藏、reopen 产物 detail 降级)。

## 文档

- `docs/specs/server-protocol.md`:新增 executions 接口与 settings 开关字段。
- `docs/specs/dispatcher-design.md`:新增执行上报通路、脱敏、开关说明。

## 改动范围概览

- **server**:`db.py`(表 + scoped_counter)、`models.py`(Execution* 模型 + Settings 加两开关)、`routers/executions.py`(新建)、`routers/settings.py`(两开关)、`routers/projects.py`(删除联动)、落盘 + 脱敏工具模块。
- **dispatcher**:`protocol/client.py`(`report_execution`)、`tasks/{common,bootstrap,explore,reason}.py`(执行后上报、读开关)、脱敏 / 截断工具。
- **frontend**:`index.html`(Runtime 标签 + 结果框 copy/detail + 设置面板两开关)。

## 不做(Out of scope)

- 逐 token 实时流式输出。
- 跨项目的全局日志检索 / 全文搜索。
- 日志的自动定期清理(仅随项目删除清理;单条 10MB 上限即膨胀兜底)。
