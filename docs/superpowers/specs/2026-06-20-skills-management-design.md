# Skills 管理 + 工作区投递(F + A)— 设计

日期:2026-06-20
状态:待评审(brainstorming 已确认全部关键决策)

## 目标

让 docker 和 local 两种环境下的 worker 智能体(claude code / codex / opencode / pi)能用上一组**平台内置、可在 Web 上增删改查的 skill**(如 `decompile` 反编译能力)。项目运行时,启用的 skill 自动投递进智能体工作区,并在 prompt 中明确告知智能体优先使用。

本设计覆盖:
- **F 工作区投递(地基)**:任务开始时把一组文件投递进 agent 工作区,docker + local 统一。
- **A Skills 管理**:`/skills` 管理页(与 `/engines` 同级)+ 运行时注入 + prompt 指令。

**项目维度的知识注入(源码/文档/领域图谱)是子项目 B,本轮不做。**

## 背景与既有事实

- Agent 的能力来自工作区文件:`.claude/skills/<name>/SKILL.md`(claude 原生发现)、`AGENTS.md`/`CLAUDE.md`。
- 现状是把 skill **构建期烤进 docker 镜像**(`container/.agents/skills/tsec-actions`)+ local 由 `LocalRuntime._seed_agent_config` 种入。本设计改为**运行时动态注入**,以支持 Web CRUD。
- 仓库 `skills/`(未跟踪)已有标准 skill:`decompile / graphify / skill-creator / xiaoyi-interact-skill`,均为 `SKILL.md` 格式。
- Hints 已经会渲染进 prompt(`format_hints` → `{hints}`);`prompting.render_prompt` 是简单 `{key}` 替换。
- 调度器已能把图快照单文件 `write_text_file` 进容器;本设计扩展为"投递目录树"。

### 跨引擎 skill 发现(实测 `--help` 结论)
| 引擎 | 原生 skill 发现 | 本轮处理 |
| --- | --- | --- |
| claude | ✅ `.claude/skills/`(`/skill-name`) | 投递到 `.claude/skills/` 即原生可用 |
| codex | ❌ 无(纯 AGENTS.md) | 靠 prompt 指令 + 读工作区文件 |
| opencode | ❌ 无(只有 plugin,且 `--pure`) | 靠 prompt 指令 + 读工作区文件 |
| pi | ✅ `--skill <path>`,但 driver 现传 `--no-skills`/`--no-context-files` | 本轮靠 prompt 指令;原生 `--skill` 留作后续可选 |

**决策(已对齐)**:本轮**只做"文件投递到 `.claude/skills/` + prompt 指令"**;不做 AGENTS.md/CLAUDE.md 注入、不内联 SKILL.md 全文、不改 pi driver。先看效果。

## 存储

- skills 根:`~/.cairn/skills/`(`CAIRN_HOME` 可覆盖),与 `engines.json`/`workspaces/` 同处 cairn 家目录。
- 首次访问时若为空,从仓库 `skills/` 目录**种入**初值(bundled skills)。
- 启用状态:`~/.cairn/skills/.registry.json`(`{ "<name>": {"enabled": true} }`);缺省视为启用。

## 组件

### 1. `cairn/src/cairn/skills_store.py`(新建,中立共享模块)

被 server(CRUD)与 dispatcher(注入)共用,**不耦合 server/dispatcher**(自行从 `CAIRN_HOME`/`~/.cairn` 解析根,模式同 `resolve._engines_config_path`):

```
skills_root() -> Path                       # ~/.cairn/skills
seed_if_empty(repo_skills_dir: Path) -> None # 首次从仓库 skills/ 种入
list_skills() -> list[SkillMeta]            # name, description(读 SKILL.md frontmatter), enabled, path
read_skill_md(name) -> str
write_skill_md(name, content) -> None        # 编辑/新建
create_skill(name, skill_md) -> None
delete_skill(name) -> None                   # 删整个目录
set_enabled(name, enabled: bool) -> None     # 写 .registry.json
import_zip(data: bytes) -> str               # 解压一个 skill 包到 <root>/<name>/,返回 name
enabled_skill_dirs() -> list[Path]           # dispatcher 注入用:启用 skill 的目录列表
```
- `SkillMeta`:`{name, description, enabled, path}`。`description` 从 `SKILL.md` 的 YAML frontmatter `description:` 解析(无则空)。
- `name` 取目录名;非法/路径穿越名拒绝(只允许 `[A-Za-z0-9._-]`)。
- 写操作原子化(临时文件 + `os.replace`/`shutil`);损坏 `.registry.json` 容错为 `{}`。

### 2. 服务端

- `server/models.py`:`SkillInfo{name, description, enabled}`;`SkillContent{name, content}`(读/写 SKILL.md);`SkillCreate{name, content}`;`SkillEnable{enabled}`。
- `server/routers/skills.py`(新建):
  - `GET /skills` → `list[SkillInfo]`
  - `GET /skills/{name}` → `SkillContent`(SKILL.md 文本)
  - `POST /skills`(body `SkillCreate`)→ 新建一个 skill(写 `SKILL.md`)
  - `PUT /skills/{name}`(body `SkillContent`)→ 编辑 SKILL.md
  - `PUT /skills/{name}/enabled`(body `SkillEnable`)→ 启用/停用
  - `DELETE /skills/{name}` → 删除
  - `POST /skills/upload`(multipart,zip 文件)→ `import_zip`,返回新 skill 的 `SkillInfo`
  - 启动时 `seed_if_empty(<repo>/skills)`(或首次 `GET /skills` 时种入)
- `server/app.py`:挂载 `skills.router`。

### 3. F — 工作区投递(Runtime 能力)

`Runtime` 协议新增:
```
install_skills(workspace_key: str, skill_dirs: list[Path]) -> None
```
- 把每个 `skill_dir` 放到工作区 `.claude/skills/<skill_dir.name>/`(覆盖式,幂等)。
- **ContainerManager**:把这些目录打成 tar,用 `container.put_archive` 灌到容器工作区的 `.claude/skills/`(现有 `write_text_file` 只能单文件,这里走目录 tar)。
- **LocalRuntime**:`shutil.copytree(skill_dir, <workspace>/.claude/skills/<name>, dirs_exist_ok=True)`。
- 测试 fake runtime(`FakeContainerManager`/`LocalContainerManager`/`_FakeRuntime`)同步加该方法(no-op)。

### 4. A2 — prompt 指令

- `prompting.py` 新增 `format_skills(skills: list[SkillMeta]) -> str`:渲染一段「可用 Skills」清单 + 强引导,例如:
  ```
  ## Available Skills (prefer these)
  You have these skills installed at .claude/skills/<name>/SKILL.md. When a task matches a
  skill, READ its SKILL.md and follow it; prefer skills over ad-hoc approaches.
  - decompile: <description>  (.claude/skills/decompile/SKILL.md)
  - graphify: <description>   (.claude/skills/graphify/SKILL.md)
  ```
  无启用 skill 时返回空串。
- 模板 `prompts/default/{bootstrap,bootstrap_conclude,reason,explore,explore_conclude}.md` 加 `{skills}` 占位(放在合适位置,如 Context 区);`render_prompt` 的简单 `{key}` 替换即可。

### 5. 任务层接线(`dispatcher/tasks/common.py` + 三个 task)

`common.py` 新增 helper:
```
prepare_skills(runtime, workspace_key) -> str
    dirs = skills_store.enabled_skill_dirs()
    runtime.install_skills(workspace_key, dirs)      # F:投递文件
    return format_skills([meta for enabled])          # A2:返回 {skills} 文本
```
- 各 task 在 `ensure_running` 之后调用一次,把返回值作为 `{skills}` 传进 `render_prompt`。
- 启用 skill 为空 → 不投递、`{skills}` 为空,行为与现状一致。

### 6. 前端 `/skills` 页(`server/static/index.html`)

- 路由 `#/skills`(`view==='skills'`)+ 列表头部"Skills"导航(与 Engines/Chat 同处)。
- 列表:每个 skill 显示 `name` + `description` + 启用开关(`PUT …/enabled`)。
- 操作:查看/编辑 `SKILL.md`(文本框 → `PUT /skills/{name}`)、删除(`DELETE`)、新建(填 name + SKILL.md → `POST /skills`)、**ZIP 上传**(`POST /skills/upload`,multipart)。
- 风格与 `/engines` 页一致。

## 数据流(一次 explore,docker 后端)

1. 调度器选中 worker,`ensure_running` 得容器工作区。
2. `prepare_skills(runtime, key)`:`skills_store.enabled_skill_dirs()` 读 `~/.cairn/skills` + `.registry.json` → `runtime.install_skills` 把启用 skill tar 进容器 `.claude/skills/` → 返回清单文本。
3. `render_prompt(explore.md, {..., "skills": <清单文本>})`。
4. worker 在容器内执行;claude 原生发现 `.claude/skills/`,其它引擎按 prompt 指令去读 `.claude/skills/<name>/SKILL.md`。

local 后端同理,只是 `install_skills` 走 `copytree` 到 `~/.cairn/workspaces/<id>/.claude/skills/`。

## 部署说明

- skills 在 `~/.cairn/skills`(cairn 家目录)。**dispatcher 需能读到它**:local-dev(server+dispatcher 都在宿主机)直接可用;若用 compose 把 dispatcher 容器化跑 docker worker,需要把 `~/.cairn` 挂进 dispatcher 容器(compose 卷),否则它读不到 skills。

## 错误处理

- skill 目录无 `SKILL.md` / frontmatter 缺 description → description 空,不报错。
- `.registry.json` 损坏 → 视为空(全部默认启用),写操作重建。
- ZIP 非法 / 无顶层 skill 目录 / 名称穿越 → 4xx + 明确信息,不解压。
- `install_skills` 某个目录复制失败 → 记 warning、跳过该 skill,不中断任务(skill 是增强,不应让任务失败)。
- 启用 skill 为空 → 无注入、`{skills}` 空,等价现状。

## 测试

- `skills_store`:种入空目录;list 读 frontmatter name/description;create/write/read/delete;set_enabled 持久化 + 损坏 registry 容错;`import_zip` 解压合法包、拒绝穿越/非法;`enabled_skill_dirs` 只返回启用项;名称白名单。
- 服务端路由(TestClient):list/get/create/update/enable/delete/upload 全链路;非法名 4xx;**不泄漏文件系统绝对路径之外的敏感信息**。
- `format_skills`:有/无启用 skill 的渲染;含 name+description+path。
- F:`LocalRuntime.install_skills` 把目录复制到 `.claude/skills/<name>`;`ContainerManager.install_skills` 用 fake/mocked docker 客户端断言 `put_archive` 调用(或在 mock e2e 里验证 no-op fake)。
- 任务层:`prepare_skills` 调 `install_skills` + 返回非空文本;启用为空时空文本、不调注入。
- 前端:手测(列表/开关/编辑 SKILL.md/新建/删除/zip 上传/导航)。

## 局限(写入 spec 显著处)

- **不保证模型一定调用 skill**:prompt 是软引导;claude 原生加载较强,codex/opencode/pi 取决于模型是否按指令去读。本轮先观察效果。
- **大文件 skill 成本**:带二进制/脚本的 skill(如 `decompile`)全量投递到每个容器/工作区有体积与耗时成本;用启用开关按需关闭重型 skill。
- 每次任务都做幂等投递(覆盖),未做"每容器仅投递一次"的优化——后续可加。

## 不在本轮范围(后续)

- **B 项目知识注入**(源码/文档/领域图谱,项目维度,尽量复用 Hints)。
- AGENTS.md/CLAUDE.md 注入、SKILL.md 全文内联、pi driver 原生 `--skill`。
- skills 的完整网页文件树编辑(本轮:列表/开关/编辑 SKILL.md/删除/新建/zip 上传)。

## 改动范围概览

- 新增:`cairn/src/cairn/skills_store.py`、`server/routers/skills.py`、前端 `/skills` 页 + 测试。
- 修改:`server/models.py`(Skill* 模型)、`server/app.py`(挂载 + 种入)、`dispatcher/runtime/base.py`(协议加 `install_skills`)、`dispatcher/runtime/containers.py`(`install_skills` via put_archive)、`dispatcher/runtime/local/runtime.py`(`install_skills` via copytree)、`dispatcher/tasks/common.py`(`prepare_skills`)、`dispatcher/tasks/{bootstrap,reason,explore}.py`(接线 + `{skills}`)、`prompting.py`(`format_skills`)、`prompts/default/*.md`(`{skills}` 占位)、测试 fake runtime 加 `install_skills`。
