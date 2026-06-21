# FlockOS 移植与 Agent 适配(第一阶段)— 设计

日期:2026-06-20
状态:待评审(brainstorming 已确认全部关键决策)

## 目标

把 Flock(生产级多智能体编排框架:声明式类型契约 + 黑板架构)移植进 Cairn 仓库,
组成新系统 **FlockOS**。FlockOS 由两个相对独立的子系统构成:

- **cairn 子系统**:基于有向无环超图做状态路径搜索(可达 / 可验 / 正确性证明),作为原子能力。
- **flock 子系统**:任务编排与拆分,把复杂任务拆成可被状态路径搜索的粒度。

最终愿景是:flock 自动编排产出"工件",作为 cairn 的 `origin / goal / hints` 等输入,
取代当前由人工分析填入的方式。

**本设计只覆盖第一阶段**:移植 + agent 适配 + 统一 Web + 一键启动,即原始需求的 1)2)3)4)。
**需求 5(flock↔cairn 通过 HTTP 传递工件、自动创建项目并回传验证结果)留第二阶段,本轮不做。**

## 已确认的关键决策(brainstorming)

| 决策点 | 选择 |
| --- | --- |
| flock agent 如何调用 cairn 本地引擎 | **进程内直接复用**:新引擎组件 import cairn 的 worker driver + 宿主机进程执行,单 Python 进程内完成 |
| 仓库布局 | **两个并列子包** `cairn/` + `flock/`,外加薄整合层 `flockos/` |
| Web 服务拓扑 | **单进程单端口**(沿用 `:8000`):flock 看板挂 `/`,cairn 界面挂 `/cairn` |
| flock 前端构建 | **预构建为静态**:`npm build` 一次产出静态,FastAPI 直接托管;运行时不依赖 npm |

## 背景与既有事实(代码核查结论)

### Flock 侧
- 核心 = 黑板架构:`store`(工件库)+ `agent`(`.consumes(类型).publishes(类型)`,用 Pydantic schema 声明契约)+ `orchestrator`(`Flock`,按订阅类型自动路由,工作流浮现)。
- **唯一可插拔执行点** = `EngineComponent.evaluate(agent, ctx, inputs, output_group) -> EvalResult`(`flock/components/agent`)。
- `OpenClawEngine`(`flock/integrations/openclaw/engine.py`,1316 行)即其子类:把"调 LLM"换成"HTTP POST 到 openclaw 网关 `/v1/responses`",再把 agent 的 JSON 输出解析回声明的 Pydantic 类型,内置 repair/retry。
- 引擎装配机制是通用的:`AgentBuilder.with_engines(*engines)` 挂引擎;`Flock.openclaw_agent(alias)` 只是 `builder.with_engines(OpenClawEngine(...))` + `labels("openclaw")` 的便捷封装(`flock/core/orchestrator.py:249`)。`Agent._run_engines` 调 `engine.evaluate(...)`,并对返回 BaseModel 自动包装为 EvalResult。
- 前端 = 独立 React 19 + Vite + xyflow 实时图看板(`flock/frontend/`,119 个 TS/CSS),websocket 驱动,自带 `themes/` 主题系统。后端 = FastAPI(`flock/api/service.py` 的 `BlackboardHTTPService`)。CLI = typer(`flock/cli.py`)。
- 编排路由本身是确定性的(按类型订阅),**不需要 LLM**;LLM/agent 只用于单个 agent 的执行。

### Cairn 侧
- 单个 FastAPI app(`cairn/server/app.py`):router 全部挂在根(`/engines /projects /skills /chat …`)+ `/static` 静态挂载。
- 前端是纯 `static/index.html`(Alpine + hash 路由 `#/engines` 等)。
- **关键约束:Cairn 前端用的是绝对路径** —— `/static/...`、`fetch('/engines')`、`fetch('/skills')`、`/projects/${id}/executions/...`。因此 cairn 的 API 路由必须保持挂在根,否则页面操作会断。
- 本地引擎能力 = worker driver:`claudecode/codex/opencode/pi` 各自 `build_execute(worker, prompt, session) -> DriverResult(argv)`(`cairn/dispatcher/workers/`);`get_driver(name)` 取驱动(`workers/registry.py`);`WorkerConfig` 承载 type/env/model(来自 dispatch.yaml)。
- `LocalManagedProcess(command, env, cwd)`(`runtime/local/process.py`)在宿主机起一次性子进程,`communicate(timeout)` 抓 stdout/stderr,内置超时与进程树 kill / cancel。

## 1. 仓库布局(需求 1)

```
FlockOS/  (现 Cairn 仓库根)
├── cairn/              # 现有 Cairn 子系统,几乎零改动
│   └── src/cairn/...
├── flock/             # Flock 整体搬入(从 /Users/nicholas/project/ai/flock)
│   └── src/flock/...  # 保持内部 import 路径不变,确保自有测试可跑
└── flockos/           # 新增薄整合层(本设计的新代码主要在这里)
    ├── src/flockos/
    │   ├── engine.py    # CairnAgentEngine + CairnConfig(Flock EngineComponent 子类)
    │   ├── app.py       # 统一 FastAPI 装配(cairn app + flock api + 两个前端)
    │   └── cli.py       # 一键启动器 flockos start/stop/status
    └── tests/
```

- flock 作为**独立 package** 搬入 `flock/`,内部代码尽量零改动(保留 2300+ 测试可跑)。
- 只搬运行所需:`src/`、`pyproject` 必要依赖、前端 `frontend/`;其文档站(`mkdocs`)、`openspec/`、海量示例可选择性精简(落地计划核对)。
- 依赖隔离:flock 的重依赖(dspy 等)在 pyproject 用 **extras** 隔离,避免污染 cairn 运行时;FlockOS 默认安装只拉起跑 `CairnAgentEngine` 所需的部分。

## 2. Agent 引擎适配(需求 3,核心)

新增 `CairnAgentEngine(EngineComponent)`(`flockos/engine.py`),镜像 `OpenClawEngine.evaluate()`:

```python
class CairnAgentEngine(EngineComponent):
    engine: str            # "claudecode" | "codex" | "opencode" | "pi"
    timeout: int = 600
    retries: int = 1
    cwd: str | None = None # agent 执行的宿主机工作目录(默认临时工作区)

    async def evaluate(self, agent, ctx, inputs, output_group) -> EvalResult:
        # 1) 把输入工件(Pydantic)+ 输出类型的 JSON Schema 拼成 prompt
        #    (复用 openclaw 的"只输出匹配该 schema 的单个 JSON"约定)
        # 2) 解析 self.engine -> 复用 cairn WorkerConfig(来自 dispatch.yaml)
        #    + get_driver(type) + driver.build_execute(worker, prompt, session) -> argv
        # 3) LocalManagedProcess(argv, env, cwd).start()/communicate(timeout)
        #    在宿主机跑一次性 agent;超时/取消复用其进程树 kill 能力
        # 4) driver.extract_response_text(stdout, stderr) -> 取 JSON
        #    -> 校验为 output_group 声明的 Pydantic 类型 -> EvalResult
        #    解析失败时复用 openclaw 的 repair:再问一次严格 JSON(受 retries 限制)
```

便捷构造器(镜像 openclaw):

```python
flock = Flock(cairn=CairnConfig(default_engine="claudecode", ...))
agent = flock.cairn_agent("claudecode").consumes(Brief).publishes(Draft)
# 等价于 flock.agent(...).with_engines(CairnAgentEngine(engine="claudecode"))
```

- `CairnConfig` 挂在 `Flock(cairn=...)`,与现有 `openclaw=` 参数并列;`cairn_agent(engine_name)` 校验引擎名后 `with_engines(CairnAgentEngine(...))`。
- 引擎名映射到 cairn 的 `claudecode/codex/opencode/pi`;引擎探测 / override 沿用 cairn `/#/engines` 既有能力(读同一份引擎配置)。
- **仅宿主机执行**:`CairnAgentEngine` 只用 `LocalManagedProcess`,完全不触碰 `ContainerManager`。符合"flock 子系统仅在宿主机执行"。
- **不引入 LLM 直连**:flock agent 一律走 `CairnAgentEngine`;flock 自带的 `DSPyEngine` 保留但默认不用。

## 3. 统一 Web(需求 2)

统一装配在 `flockos/app.py`,单进程单端口(默认沿用 `:8000`):

- **复用 cairn 现有 app 的全部 router + `/static` 挂载,完全不改** —— cairn 的绝对路径(`/engines`、`/static`、`/projects/...`)继续解析到根,操作方式零变化。
- **Flock 看板设为主页 `/`**:其 Vite 构建产物以独立 base(`base: '/flock/'`)产出,资源走 `/flock/...`,避免与 cairn 的 `/static` 冲突。
- **Flock 的 API 子应用挂前缀 `/flock/api`**(`app.mount` 子应用),避免与 cairn 根路由(`/projects` `/engines` 等)命名冲突。
- **Cairn 界面改由 `/cairn` 返回其 `index.html`**:页面 JS 里的绝对 `/engines`、`/static`、hash 路由(`#/...`)在任何 base 下都解析到根,故 cairn 体验不变。
- **入口跳转**:flock 主页顶栏新增"进入 Cairn"链接 → `/cairn`;cairn 页面可选加返回 FlockOS 主页的链接(轻改)。
- **风格一致**:用 flock 自带 `themes/` 主题系统把看板配色调成 cairn 风格(浅色 slate 系),保持视觉统一。flock 看板结构不重写。

路由冲突核对(落地计划逐一过):flock 后端原有路由(`/artifacts /agents /health …`)全部前缀化到 `/flock/api`,确保与 cairn 根路由零重叠。

## 4. 一键启动 / 停止(需求 4)

顶层 `flockos` CLI(`flockos/cli.py`,typer 或复用 cairn cli 风格):

- `flockos start`:若 flock 前端静态产物缺失则先 `npm build` 一次产出静态;随后以 uvicorn **后台**起统一 app(写 PID 文件 + 日志文件到 `~/.cairn/` 或仓库 `.run/`)。
- `flockos start --foreground`:前台运行,日志直出终端。
- `flockos stop`:读 PID 文件优雅停止(含子进程树);停止后清理 PID 文件。
- `flockos status`:报告运行状态、PID、端口、健康检查。

单进程单端口 → 一键启动只管一个 uvicorn 进程,停止只杀一个进程树,最稳。

## 5. 测试策略

- **CairnAgentEngine**(核心):用 cairn `mock` driver + 最小 Pydantic 契约(`consumes(A).publishes(B)`),断言:
  - evaluate 把 mock 输出正确解析回声明的输出类型;
  - 非法 JSON 触发 repair/retry,超出 retries 后按 flock 约定失败;
  - 超时 / 取消正确传播(`LocalManagedProcess` 行为)。
- **统一 app**:`TestClient` 断言 `/`(flock 看板)、`/cairn`(cairn index)、`/engines` 等 cairn 根路由仍可达、`/flock/api/*` 可达、`/static` 与 `/flock/` 资源互不遮蔽。
- **启动器**:对 `start/stop/status` 的 PID 生命周期做单测(mock uvicorn/npm 调用)。
- **回归**:flock 移植后其自有测试套件冒烟跑一遍,确保 import 路径与依赖完整。

## 6. 范围与风险(明确不做 + 风险)

**明确不做(本轮)**:
- 需求 5(HTTP 工件验证对接 / flock 调 cairn 自动建项目并回传)—— 第二阶段。
- 不把 flock 看板重写成 cairn 原生(Alpine)风格 —— 保留 React 看板,仅主题调色 + 加入口。
- 不引入 LLM 直连 —— agent 一律走 `CairnAgentEngine`。
- 不改 cairn 现有任何 API / 页面行为。

**风险与缓解**:
- flock 依赖较重(dspy 等):pyproject extras 隔离,默认安装不拉无关重依赖。
- flock / cairn 路由命名冲突:flock 后端全部前缀化到 `/flock/api`,计划阶段逐一核对。
- flock 前端构建产物体积 / 首次 build 耗时:预构建为静态并提交或缓存,`start` 仅在缺失时 build。
- flock 内部 import 假设(相对包根):整体搬为独立 package 保持路径不变,降低改动面。

## 第二阶段预告(不在本轮实现)

cairn 暴露"创建项目"等 HTTP 接口;flock 编排产出工件后,通过 HTTP 传 `origin / goal / hints / project_root / engine mode` 给 cairn 自动建任务(等价于界面操作),cairn 回传验证结果给 flock。届时 `flockos/` 整合层承载这层对接。
