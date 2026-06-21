# 项目知识注入(Project Knowledge Injection)— 设计(子项目 B)

日期:2026-06-21
状态:待评审(brainstorming 已确认全部关键决策)

## 目标

让后台执行的 worker 智能体**复用项目前期已产出的分析结果**(源码、资料、领域知识图谱、静态扫描、代码图),从而以更多上下文、更精准地完成任务,且 worker 不必重做前期那些重活。

核心做法:每个项目有一个宿主机上的**项目根目录 A**,内含固定布局的前期产物;任务运行时把 A **只读挂载/共享**进 worker 工作区(docker + local 统一),并在 prompt 中告知 worker 这些资料在哪、怎么用、优先复用。

本设计是子项目 B。它与子项目 A(Skills 管理)相邻但独立;实现分支叠在 `feat/skills-management` 之上(A 尚未并入 main),因为二者会改到同几个文件(prompt 模板、模型、New Project 表单、runtime)。

## 关键判断(为什么是"挂载"而不是"复制")

A 目录**大**,且是 docker + local 都要共享的"唯一真相源"。每个任务都把它复制进容器/工作区既慢又浪费、还产生多份副本。子项目 A 的"复制投递"(`install_skills`)适合"小而全局"的 skill;**A 这种"大而每项目"的知识目录用挂载/共享**。worker 读 A、写自己的工作区——与"只读 + 需写自行拷出"一致。

## 业务布局(约定)

项目根目录 A 下固定子布局(黑/白/灰盒同此布局,可只存在部分):
- `src-repo/` — 源码(白盒全/部分源码;黑盒为反编译伪代码)
- `docs-out/` — 资料/产品文档
- `graphify-out/` — 领域知识图谱(graphify 构建)
- `scan-out/` — 源码静态扫描结果
- `codegraph-out/` — 代码图(codegraph,https://github.com/colbymchenry/codegraph)

## 关键决策(已对齐)

- 每项目新增**可选**字段 `project_root = A`(宿主机绝对路径)。不填则行为同现状(无 `./project`)。
- A **只读挂载**到工作区子路径 **`./project`**;worker 的 cwd 仍是托管工作区(skill 与 worker 草稿落在工作区,不污染 A)。
- docker:容器创建时 `bind-mount A → /home/kali/workspace/project`(`mode=ro`)。local:工作区内 `project` **软链接**到 A。两端同一相对路径 `./project`。
- prompt 新增 `{project_knowledge}` 占位:仅当 `project_root` 设置时渲染;dispatcher **探测 A 实际存在哪些子目录**,只列出存在的,并给出每类的用法 + "优先复用,勿重复前期分析"。
- Hints 不变(文字指引)。

## 组件

### 1. 数据层(`server/db.py`)
- `projects` 加列 `project_root TEXT`(可空),走既有 `_ensure_project_columns` 迁移守卫。

### 2. 模型(`server/models.py`)
- `CreateProjectRequest.project_root: str | None = None`
- `ProjectMeta.project_root: str | None = None`(回带,前端可显示/编辑)

### 3. 创建项目(`server/routers/projects.py` + `services.py`)
- INSERT 带 `project_root`;`project_meta_from_row` 回读。
- 校验:若提供 `project_root`,服务端校验它是**存在的目录**(server 宿主机视角);不存在 → 400。

### 4. 运行时挂载(`Runtime` 协议 + 两实现)
挂载必须在**容器创建时**完成(运行中容器无法追加挂载),因此 `ensure_running` 需要知道 `project_root`:
- 协议 `ensure_running(project_id, project_root: str | None = None) -> str`(可选参数,默认 None,保持兼容)。
- **ContainerManager**:创建容器时若 `project_root` 设置,加 `volumes={project_root: {"bind": "/home/kali/workspace/project", "mode": "ro"}}`;不复制。容器复用时沿用创建时的挂载(注:`project_root` 应在首次运行前设好,事后修改需重建容器)。
- **LocalRuntime**:`ensure_running` 建工作区后,若 `project_root` 设置,创建软链接 `<workspace>/project → project_root`(已存在则跳过)。
- 调度器在三个 task 调 `ensure_running` 处传入 `project.project.project_root`。
- 测试 fake runtime(`FakeContainerManager`/`LocalContainerManager`/`_FakeRuntime`)的 `ensure_running` 同步加可选参数。

### 5. prompt 指令(`prompting.py` + 模板 + 任务层)
- `prompting.format_project_knowledge(project_root, present_subdirs: list[str]) -> str`:`project_root` 为空或无子目录时返回 ""。否则渲染:
  ```
  ## Project Knowledge (prior analysis, read-only at ./project)
  Reuse these prior results; do NOT redo upfront analysis.
  - source code: read/grep ./project/src-repo        (仅当存在)
  - code graph: query with `codegraph` CLI (query/explore/node/callers/impact) over ./project/codegraph-out   (仅当存在)
  - domain knowledge graph: `graphify query` over ./project/graphify-out   (仅当存在)
  - static scan findings: read ./project/scan-out      (仅当存在)
  - product docs: read ./project/docs-out              (仅当存在)
  ```
  只列出**实际存在**的子目录。
- 模板 `prompts/default/{bootstrap,bootstrap_conclude,reason,explore,explore_conclude}.md` 加 `{project_knowledge}` 占位(Context 区,`{skills}` 旁)。
- `tasks/common.py` 加 helper `prepare_project_knowledge(project_root) -> str`:探测 A 下存在哪些约定子目录(`src-repo/docs-out/graphify-out/scan-out/codegraph-out`),调 `format_project_knowledge` 返回文本。dispatcher 在宿主机有 A 的访问权(docker 模式它也是挂载方),故由它探测。
- 三个 task 在 `ensure_running` 后:`pk = prepare_project_knowledge(project.project.project_root)`,把 `"project_knowledge": pk` 加入 `render_prompt` 替换字典。`project_root` 为空 → 空文本,等价现状。

### 6. 前端(`server/static/index.html`)
- New Project 表单加一个可选字段 "Project root (host path)" → 提交到 `project_root`。
- (可选)项目卡片/详情显示 `project_root`。最小实现:仅 New Project 输入。

## 数据流(一次 explore,docker 后端,project_root=A)

1. 调度器 `ensure_running(project_id, A)` → 容器创建时把 A 只读挂到 `/home/kali/workspace/project`。
2. `prepare_project_knowledge(A)`:探测 A 下存在的子目录 → 文本。
3. `render_prompt(explore.md, {..., "project_knowledge": 文本, "skills": ...})`。
4. worker 在容器内:`./project/src-repo` 读源码、`codegraph ... ./project/codegraph-out` 查代码图、`graphify query ./project/graphify-out` 查知识图谱、读 `scan-out`/`docs-out`,优先复用。

local 同理,`./project` 是软链接到 A。

## 假设与约束(写入文档)

- **查询工具需在 worker 环境就绪**:要真正用上图查询,worker 运行环境需有 `graphify` 与 `codegraph` CLI(装进镜像/宿主,或作为 skill 提供)。B 只负责"挂载 A + 指路";工具缺失时,指令里引导 agent **退化为直接读** `codegraph-out`/`graphify-out` 下文件。
- **Docker 挂载宿主路径**:Docker Desktop 需在文件共享中允许 A 所在路径;daemon 要能访问 A。compose 容器化 dispatcher 时,dispatcher 也需能访问 A(宿主路径)。
- `project_root` 在容器创建时生效;事后修改需重建该项目容器。
- 安全:A 以只读挂载,worker 不能改你的前期产物;worker 自身输出写工作区。

## 错误处理

- `project_root` 提供但目录不存在 → 创建项目 400。
- 运行时挂载失败(权限/路径)→ docker 记错误、任务失败并给明确原因;local 软链接失败记 warning(知识缺失但任务可继续,因为知识是增强非必需)——与 skills 的"best-effort 增强"一致。
- A 下某子目录缺失 → `format_project_knowledge` 不列它,不报错。
- `project_root` 为空 → 无挂载、`{project_knowledge}` 空,等价现状。

## 测试

- 模型/DB:`project_root` 字段 create/read,默认 None;迁移守卫。
- 创建路由:带 `project_root` 存读;不存在的目录 → 400。
- LocalRuntime:`ensure_running(pid, A)` 在工作区建 `project → A` 软链接;无 `project_root` 不建。
- ContainerManager:用 mock docker 客户端断言创建容器时带只读 volume(`A → /home/kali/workspace/project`, ro);无 `project_root` 不带。
- `format_project_knowledge`:空 project_root → "";只列存在的子目录;含 codegraph/graphify 用法文案。
- `prepare_project_knowledge`:探测临时 A(部分子目录)→ 只列存在项。
- 任务层:三个 task 传 `project_knowledge`(集成在既有 mock e2e,断言不破)。
- 前端:手测(New Project 填 project_root → 创建 → 回读)。

## 不在本轮范围

- 安装/管理 `graphify`/`codegraph` 等查询工具(属环境/skill)。
- 项目知识的版本管理、热更新(改 project_root 后自动重挂)。
- 把 A 之外的任意多路径挂载(本轮只挂一个项目根 A;Hints 仍可补充文字)。

## 改动范围概览

- 修改:`server/db.py`(列)、`server/models.py`(`project_root`)、`server/routers/projects.py` + `services.py`(存读 + 校验)、`server/static/index.html`(New Project 字段)、`dispatcher/runtime/base.py`(`ensure_running` 签名)、`dispatcher/runtime/containers.py`(只读 bind-mount)、`dispatcher/runtime/local/runtime.py`(软链接)、`dispatcher/tasks/{bootstrap,reason,explore}.py`(传 `project_knowledge` + `ensure_running` 传 `project_root`)、`dispatcher/tasks/common.py`(`prepare_project_knowledge`)、`prompting.py`(`format_project_knowledge`)、`prompts/default/*.md`(`{project_knowledge}` 占位)、测试 fake runtime 的 `ensure_running` 签名。
- 文档:server-protocol / dispatcher-design / README 增补。
