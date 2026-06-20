# 引擎对话/调试模式(Engine Chat Mode)— 设计(子项目 C)

日期:2026-06-20
状态:待评审(brainstorming 已确认全部关键决策)

## 目标

提供一个 cairn 风格的对话页:**选一个已配置的 worker,在宿主机上一问一答地与其引擎对话**,用来**验证连通性**和**调试提示词 / 命令 / 引擎行为**。不是通用助手;核心是"这个 worker 到底能不能通、它收到的命令和原始输出是什么"。

本设计是子项目 C,基于子项目 A(本地引擎 worker:`LocalRuntime` / `LocalManagedProcess` / 解析器 / `WorkerDriver` 会话原语 / `execlog` 脱敏)。

## 关键决策(已对齐)

- **D1 引擎与凭据来源**:复用 `dispatch.yaml` 里已配置的 worker —— 对话页选一个 worker,用它的 `type` + `env`(baseURL/apiKey/model)。测的就是 dispatcher 真正会用的那套配置。
- **D2 执行方式**:复用 CLI driver,**一问一答(request/response)**。不做 SDK、不做逐 token 流式(留作后续增强)。
- **D3 多轮**:用驱动已有的 session 机制续跑;**4 个引擎均支持**(claude/codex 客户端预生成 id,opencode/pi agent 生成 id,均由驱动抽象)。
- **D4 调试可见性**:每条回复可展开看**脱敏命令 + 发送的 prompt + 原始 stdout + session id**。
- **D5 后端落点**:新增 **server 端点**,加载 `DispatchConfig`,用 解析器 + driver + `LocalManagedProcess` 在**宿主机**跑一轮。即 C 在宿主机执行引擎(需要宿主机装了该 engine,与 Local 模式一致)。
- **D6 传输**:`POST /chat/turn` 同步返回完整回复(前端转圈);不流式。
- **会话持久化**:**v1 不持久化**——对话存在浏览器,session id 由前端持有、后端无状态透传。"关页面后回到历史对话续聊"留作后续增强。

## 会话(session)机制设计

驱动层已有原语:`prepare_session()`、`build_execute(worker, prompt, session)`(session 非空=续跑)、`extract_session(session, stdout, stderr)`。

每一轮(对所有引擎统一):
1. 第 1 轮:`s0 = driver.prepare_session()`(claude 预生成 id;opencode/pi 返回 None)→ `build_execute(worker, message, s0)` → 跑 → `session = extract_session(s0, stdout, stderr)`。
2. 第 N 轮:前端回传上一轮的 `session` → `build_execute(worker, message, session)` → 驱动加续跑参数(opencode `-s`、pi `--session`、claude 复用 `--session-id`)→ agent 加载该会话上下文作答。

- **对话记忆存在各 agent 自己的 session 仓**(以不透明 `session` id 为键);cairn 只搬运 id。
- 跨独立 HTTP 调用能续上:agent 的 session 目录在本机稳定(如 pi `/tmp/cairn-pi/<worker>/sessions`),同一 worker + 同一 id 第二次调用即重挂同一会话。
- "上下文调试" = 逐轮看到 cairn 发给 agent 的命令(含 `-s/--session`,可见会话 id 与是否续跑)、prompt、原始 stdout、回复。

## 组件与架构

### 1. 对话服务(`server/chat.py`,新建)

一个不依赖 docker 的服务模块:
- `load_dispatch_config() -> DispatchConfig`:从路径加载;路径取自环境变量 `CAIRN_DISPATCH_CONFIG`(默认当前工作目录的 `dispatch.yaml`)。加载失败 → 抛出明确错误。
- `list_workers() -> list[ChatWorker]`:返回 `{name, type, model}`(model 从 worker.env 的模型键读,如 OPENCODE_MODEL/PI_MODEL/...,复用 `tasks.common.model_env_key`)。**绝不返回 env / 凭据**。
- `run_turn(worker_name, message, session) -> ChatTurnResult`:
  1. 取该 worker;`probe_engine(worker.type)` 前置守卫——不可启动则返回明确错误(`<bin> 未安装/不在 PATH/可在引擎管理页配置`)。
  2. `driver = get_driver(worker.type)`;`session_in = session or driver.prepare_session()`。
  3. `result = driver.build_execute(worker, message, session_in)`(message 即 prompt,无图快照/无任务模板)。
  4. 用一个**专用的 chat `LocalRuntime`**(workspaces_root = `~/.cairn/chats`,completed_action 固定 `stop`=保留)`ensure_running(worker_name)` 取 cwd,`build_exec_process(worker_name, worker.env, result.argv)` → `start()` → `communicate(timeout=chat 超时)`。
  5. `session_out = driver.extract_session(result.session, res.stdout, res.stderr)`;`reply = driver.extract_response_text(res.stdout, res.stderr)`。
  6. 返回 `ChatTurnResult{reply, session: session_out, command: redact_command(result.argv), prompt: message, stdout: <脱敏+截断 res.stdout>, exit_code, outcome, duration_ms}`(脱敏/截断复用 `cairn.execlog`)。

### 2. 服务端模型(`server/models.py`)

```python
class ChatWorker(BaseModel):
    name: str
    type: str
    model: str | None = None

class ChatTurnRequest(BaseModel):
    worker: str
    message: str
    session: str | None = None

class ChatTurnResult(BaseModel):
    reply: str
    session: str | None = None
    command: list[str]
    prompt: str
    stdout: str
    exit_code: int | None = None
    outcome: str            # "success" | "failed" | "timeout"
    duration_ms: int = 0
```

### 3. 服务端路由(`server/routers/chat.py`,新建)

- `GET /chat/workers` → `list[ChatWorker]`(来自 `list_workers()`)。配置加载失败 → 返回明确错误(空列表 + 错误信息或 4xx)。
- `POST /chat/turn`(body `ChatTurnRequest`)→ `ChatTurnResult`。一轮同步执行,可能耗时数秒~数十秒。
- 挂载到 `server/app.py`。

### 4. 前端(`server/static/index.html`,Alpine SPA)

- 路由:`#/chat` → `view = 'chat'`;导航入口跳 `#/chat`。
- 顶部:worker 下拉(`GET /chat/workers`,显示 `name · model`);"新对话"按钮(清空 session 与消息列表)。
- 主体:消息流(用户消息 + 助手回复气泡),cairn 风格。
- 底部:输入框 + 发送(`POST /chat/turn`,带当前 `session`;成功后保存返回的 `session`,追加回复;转圈直到返回)。
- 每条助手回复可展开 **debug**:脱敏命令、prompt、原始 stdout、session id、outcome、耗时(复用执行日志详情的展示风格)。
- 失败(outcome=failed/timeout、或前置守卫不通过)→ 在该轮显著标红并展示原因(如"pi 未安装,去引擎管理页配置")。

## 数据流(一轮)

前端 `POST /chat/turn {worker, message, session?}` → server `run_turn` → 前置 `probe_engine` 守卫 → `build_execute`(带 session)→ 宿主机 `LocalManagedProcess` 跑 → 解析 session + 回复 → 返回 `{reply, session, command, prompt, stdout, outcome, duration_ms}` → 前端追加回复 + 存 session + 可展开 debug。

## 错误处理

- 引擎不可启动(守卫失败)→ outcome=`failed`,reply 为空,明确原因。
- 超时 → outcome=`timeout`(`LocalManagedProcess` 杀进程树)。
- 解析不出回复 → reply 回退为原始 stdout(便于调试),outcome 视退出码定。
- `dispatch.yaml` 加载失败 → `GET /chat/workers` 给明确错误;前端提示"未找到 dispatch 配置,设置 CAIRN_DISPATCH_CONFIG"。
- 凭据绝不出现在 `GET /chat/workers`;命令/输出经 `execlog` 脱敏后才返回。

## 测试

- `chat.run_turn`(用 mock driver + fake runtime / 或 mock LocalManagedProcess):新会话取到 session;续跑把 session 透传进 `build_execute`;reply = `extract_response_text`;command 脱敏;守卫不过时 outcome=failed。
- `list_workers`:返回 name/type/model,**断言不含 env/apiKey**;model 取值正确。
- 路由(TestClient):`GET /chat/workers`(monkeypatch 配置加载);`POST /chat/turn`(monkeypatch `run_turn`)返回结构正确。
- 配置加载:`CAIRN_DISPATCH_CONFIG` 生效;缺失/损坏给明确错误。
- 前端:手测(选 worker、发消息、多轮续聊见上下文、展开 debug、失败展示、新对话清 session)。

## 局限与安全

- **在宿主机执行引擎**:需要宿主机装了该 engine(同 Local 模式);只在 server 在宿主机时有意义。
- **同步、可能慢**:一轮阻塞到 agent 结束;无 token 流式(后续增强)。
- **v1 不持久化**:对话在浏览器,刷新即丢;会话 id 仍是 agent 的,理论可手动续,但 cairn 不存历史。
- 凭据来自 `dispatch.yaml`(server 需可读该文件);页面不回传凭据,命令/输出脱敏。
- 信任边界同 Local 模式:用 worker 的 env 在宿主机起 agent,无隔离。

## 不在 v1 范围(后续增强)

- 逐 token 流式(SDK 或 CLI stream + SSE/WS)。
- 服务端持久化对话(关页面后回到历史会话续聊)。
- 在 docker 容器内跑对话(当前只在宿主机)。

## 改动范围概览

- 新增:`server/chat.py`、`server/routers/chat.py` + 测试。
- 修改:`server/models.py`(Chat* 模型)、`server/app.py`(挂载)、`server/static/index.html`(路由+对话页+导航)。
- 复用:`get_driver`、`WorkerDriver` 会话原语、`LocalRuntime`/`LocalManagedProcess`、`resolve.probe_engine`、`execlog` 脱敏/截断、`tasks.common.model_env_key`、`DispatchConfig.load`。
