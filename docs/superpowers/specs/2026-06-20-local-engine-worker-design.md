# 本地引擎 Worker(Local Engine Worker)— 设计

日期:2026-06-20
状态:待评审(brainstorming 已确认全部关键决策)

## 目标

在硬件资源受限、不想为 Docker 付出虚拟化开销的场景下,让 Cairn 能把已安装在**宿主机**上的 agent(claude code / codex / opencode / pi)作为 worker 直接运行,完成与当前 Docker worker **完全相同的业务逻辑**(bootstrap / reason / explore)。

用户在 New Project 时通过一个 "Local Engine" 勾选框选择后端:不勾 = 默认 Docker;勾上 = 本地引擎。

本设计仅覆盖**子项目 A:本地执行后端**。引擎管理页(B)、对话/调试模式(C)各自单独立项。

## 背景与关键判断

Cairn 已把"调用 agent"抽象为两层:
- `WorkerDriver`(claude/codex/opencode/pi)→ 生成一条 CLI argv。
- `ContainerManager` → 把这条 argv 丢进 **Docker 容器**里执行。

因此"本地引擎 worker"**不是**移植 praxis 那套 per-agent SDK 引擎,而是**新增一个执行后端** `LocalRuntime`——把**同一条 argv** 用 `subprocess` 在宿主机执行(agent 本地已装)。`WorkerDriver`、`tasks/*`、契约校验、执行日志、healthcheck **全部复用**。

决策(已与用户对齐):
- 执行方式:**复用现有 CLI 驱动 + `LocalRuntime`**;不为 worker 执行封装 SDK(SDK 留给子项目 C 视情况)。
- 引擎选择:**选项 a**——勾选 Local 只切换"在哪跑",仍跑 `dispatch.yaml` 里同一批 worker(env/模型/网关配置原样复用)。
- 工作区:**选项 i**——系统托管的每项目沙箱目录,种入与镜像相同的 agent 配置。
- 跨平台:**方案①**——三平台都正确实现(含 Windows `.cmd`/`.ps1`、进程组取消、显式路径覆盖兜底);自动化测试落 macOS/Linux,Windows 逻辑用 mock 单测覆盖 + 由用户实测。
- 数据根:**单根 `~/.cairn/`**(开发期,无迁移)。

## 组件与架构

### 1. `Runtime` 协议(`dispatcher/runtime/base.py`,新增)

把任务层实际依赖的 `ContainerManager` 方法面提为协议(`typing.Protocol`):

```
ensure_running(project_id) -> str
container_name(project_id) -> str
build_exec_process(name, env, argv, *, timeout_seconds) -> ManagedProcess
write_text_file(name, path, content) -> None
create_startup_container() -> str
needs_stopped_cleanup() -> bool
needs_completed_cleanup() -> bool
cleanup_stopped(project_id) -> bool
cleanup_completed(project_id) -> bool
close() -> None
```

- `ContainerManager` 已结构化满足该协议(`typing.Protocol` 下**零代码改动**;仅在类型标注层引用)。
- 任务层(`tasks/*`)只对协议编程,**一行不改**。

### 2. `LocalRuntime`(`dispatcher/runtime/local/runtime.py`,新增)

实现 `Runtime` 协议,语义对齐容器版但落在宿主机:

- `container_name(pid)` → 返回 `pid`(作为工作区键,保持接口一致)。
- `ensure_running(pid)` → 确保 `<workspaces_root>/<pid>/` 存在;**首次创建**时种入 agent 配置(见下);返回工作区绝对路径。
- `build_exec_process(key, env, argv, *, timeout_seconds)` → 返回 `ManagedProcess`(与容器版同类型、同取消语义),经第 4 节解析器解析 argv[0],在 `cwd=工作区`、注入 `env`、`stdin=DEVNULL` 下启动宿主进程。
- `write_text_file(key, path, content)` → 写入工作区内的相对路径(图快照)。
- `create_startup_container()` → 本地版建一个临时工作区目录供启动期 healthcheck,用后即删;返回其键。
- `needs_*_cleanup()` / `cleanup_stopped|completed(pid)` → 按配置 `completed_action`(`keep|remove`)保留或 `shutil.rmtree` 工作区目录。
- `close()` → no-op。

**工作区种入**:首次创建工作区时,从仓库内与镜像相同的源复制 agent 配置:
`container/.agents/` → 工作区 `.claude/` 和 `.agents/`;`container/AGENTS.md` → 工作区 `AGENTS.md` 和 `CLAUDE.md`(与 `container/Dockerfile` 的 COPY 行为一致)。

### 3. 进程封装(`dispatcher/runtime/local/process.py`,新增)

一个与现有容器 `ManagedProcess` **同接口**的本地进程封装(`start` / `communicate(timeout)` / 取消),内部用 `subprocess.Popen`:
- 复用现有 `ProcessResult`(returncode/stdout/stderr/timed_out/cancelled)。
- 取消/超时**跨平台杀整棵进程树**:unix `start_new_session=True` + `os.killpg`;Windows `CREATE_NEW_PROCESS_GROUP` + Job Object(或 `taskkill /T /F` 兜底)。
- 与现有 `HeartbeatLease` / `TaskCancellation` 的 `attach_process` 协作(接口一致即可)。

### 4. 跨平台引擎解析器(`dispatcher/runtime/local/resolve.py`,新增)

```
resolve_engine(worker_type) -> Resolved{ path: str, launcher: Literal["direct","cmd","powershell","node"], source: str } | None
probe_engine(worker_type) -> { launchable: bool, path: str|None, version: str|None, source: str }
```

- 类型→二进制名:`claudecode→claude`、`codex→codex`、`opencode→opencode`、`pi→pi`。
- 候选/优先级按平台:Windows 依 `PATHEXT` 找 `.cmd/.exe/.bat/.ps1/<name>`(**可直接启动者优先**),macOS/Linux 找 `<name>`。
- 搜索范围 = `PATH` + 增广:`npm config get prefix`(及 `/bin`)、Homebrew(`/opt/homebrew/bin`、`/usr/local/bin`)、nvm 当前版本 bin、`~/.local/bin`。
- **显式覆盖**:`~/.cairn/engines.json`(`{ "<type>": { "path": "...", "launcher": "..." } }`)优先于自动探测,作为终极兜底。
- `launcher` 决定 spawn 方式:`direct`=`[path,*args]`;`cmd`=`["cmd","/c",path,*args]`;`powershell`=`["powershell","-ExecutionPolicy","Bypass","-File",path,*args]`;`node`=`["node",entry,*args]`。
- `probe_engine` 在解析基础上跑 `<launch> --version`(带超时)取版本,返回 `launchable`(能起来)而非仅"存在"。

### 5. 后端路由与项目字段

- **DB**(`server/db.py`):`projects` 加列 `backend TEXT NOT NULL DEFAULT 'docker'`(`docker|local`),走既有 `_ensure_project_columns` 风格的迁移守卫;已有项目默认 `docker`。
- **模型**(`server/models.py`):`CreateProjectRequest.backend: Literal["docker","local"]="docker"`;`ProjectMeta.backend` 回带。
- **创建项目**(`server/routers/projects.py`):INSERT 带上 `backend`。
- **前端**(`server/static/index.html`):New Project 的 "Local Engine" 勾选框 → 勾上传 `backend:"local"`,默认 `docker`。
- **调度器**(`dispatcher/scheduler/loop.py`):`DispatcherLoop` 同时持有 `ContainerManager` 与 `LocalRuntime`(后者懒创建);在分派某项目前按 `project.backend` 选 runtime,传入 `tasks/*`。worker 选择逻辑(dispatch.yaml 那批)不变。`ProjectDetail`/`ProjectSummary`(dispatcher 侧模型)同步带 `backend`。

### 6. 配置(`dispatcher/config.py`)

- 新增 local runtime 配置块(`dispatch.yaml` 的 `runtime.local`):`workspaces_root`(默认 `~/.cairn/workspaces`)、`completed_action`(`keep|remove`,默认与容器一致)、`engines_config`(默认 `~/.cairn/engines.json`)。
- 缺省即可用,无需用户必填。

### 7. 数据根:单根 `~/.cairn/`(`server/db.py`)

- 新增 `cairn_home() -> Path`(默认 `~/.cairn`,`CAIRN_HOME` 环境变量可覆盖)。
- `DEFAULT_DB = cairn_home() / "cairn.db"`;执行日志 `executions_root()` 仍由 `_db_path.parent` 派生 → `~/.cairn/executions/`。
- `docker-compose.yaml`:server 卷挂载目标 `/root/.local/share/cairn/` → `/root/.cairn/`(宿主侧 `./datas/cairn/` 不变;仅 server-in-container 时相关)。
- 开发期不做迁移(测试数据可重建)。

## 部署说明(写入文档)

Local 模式下 **dispatcher 必须跑在宿主机**(`uv run cairn dispatch`),而非 docker-compose 的 `cairn-dispatcher` 容器——只有这样才能在宿主起进程、用宿主已装的 agent。server 也建议宿主运行(`uv run cairn serve`)。这正是 README 的本地启动方式,也正好省掉 Docker 开销。

## 数据流(local 项目一次 explore)

1. 调度器见 `project.backend == "local"` → 选 `LocalRuntime`。
2. `ensure_running(pid)` 建/复用 `~/.cairn/workspaces/<pid>/`(首次种入 agent 配置)。
3.(可选启动期)`probe_engine(worker.type)` 守卫:不可启动 → 任务失败并报明确原因。
4. `WorkerDriver.build_execute` 生成 argv(与容器完全相同)。
5. `LocalRuntime.build_exec_process` 用解析器解析二进制 + launcher,在工作区起进程。
6. 取 stdout/stderr → 现有 `extract_session`/`extract_response_text`/契约校验 → conclude 写回 server。
7. 执行日志:复用既有 `ExecutionRecorder` 上报(脱敏/截断/落盘),与后端无关。

## 错误处理

- 引擎不可启动(Level 1 守卫失败)→ 前置失败,outcome=`failed`,日志给"`<bin>` 未安装/不在 PATH/不可启动 + 提示在 engines.json 配置路径"。
- 工作区创建失败(权限等)→ 同样前置失败、明确报错。
- 超时/取消 → 复用 `did_timeout`/`cancel_reason` + 跨平台进程组/Job 杀树。
- 解析到 `.cmd` 但 Windows 上 `cmd /c` 失败等 → 报错并提示用显式覆盖。

## 测试

- `resolve_engine` / `probe_engine`:mock 文件系统/PATH/平台,覆盖 unix shim、Windows `.cmd`/`.ps1` 优先级、增广路径、`engines.json` 显式覆盖、`launchable` 校验(找到但起不来)。
- 本地 `ManagedProcess`:用真实小命令(如 `python -c "print('pong')"`)跑通、拿 stdout/exit;超时路径;取消时杀子进程(unix 进程组实测)。
- `LocalRuntime`:`ensure_running` 建目录 + 种配置文件;`write_text_file` 落在工作区;`cleanup_*` 按 `completed_action` 删/留。
- 路由:`backend=local` 项目走 LocalRuntime、`docker` 走 ContainerManager(fake runtime 断言);`CreateProjectRequest.backend` 透传入库 + 回读。
- 端到端(mock worker):一个 `backend=local` 项目跑通 bootstrap→reason→explore 链,产出 fact/intent。
- 数据根:`cairn_home()`/`DEFAULT_DB` 默认 `~/.cairn`,`CAIRN_HOME` 覆盖生效。
- 平台:测试落 macOS/Linux;Windows 解析/启动分支用 mock-platform 纯单测覆盖。

## 局限与安全(spec 显著位置)

- **无隔离**:本地 agent 以宿主用户权限运行,工作区只是目录、非沙箱,agent 可越界访问宿主文件/网络。开发期可接受,生产慎用——文档显著标注。
- **无镜像内工具**:容器镜像预置的安全工具(nuclei/katana/...)与知识库,本地没有,纯靠宿主已装环境。
- **资源共享**:本地进程直接占用宿主资源,受 `max_workers`/`max_project_workers` 限制约束。

## 不在本feature范围

- **B 引擎管理页**:复用第 4 节 `probe_engine` 做可用性 UI + Level 2 连通测试按钮(cairn 风格)。
- **C 对话/调试模式**:与引擎流式对话验证连通性/调试,可能引入 SDK/流式传输(cairn 风格)。

## 改动范围概览

- 新增:`dispatcher/runtime/base.py`(协议)、`dispatcher/runtime/local/{runtime,process,resolve}.py`、相关测试。
- 修改:`server/db.py`(`cairn_home`/`DEFAULT_DB`)、`server/models.py`(backend 字段)、`server/routers/projects.py`(backend 入库)、`server/static/index.html`(勾选框)、`dispatcher/scheduler/loop.py`(双 runtime + 路由)、`dispatcher/config.py`(local 配置块)、`dispatcher/models.py`(ProjectDetail/Summary 带 backend)、`docker-compose.yaml`(挂载目标)。
- `ContainerManager`:**不因 `~/.cairn` 改动**;仅作为 `Runtime` 协议的结构化实现被引用(≈0 代码)。
- 文档:README/dispatcher-design 增补 local 模式与部署说明。
