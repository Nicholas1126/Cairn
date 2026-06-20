# 引擎管理页(Engine Management Page)— 设计(子项目 B)

日期:2026-06-20
状态:待评审(brainstorming 已确认全部关键决策)

## 目标

给 Local 模式提供一个"宿主机引擎管理页":探测宿主机上已安装的 agent CLI(claude / codex / opencode / pi),展示是否可启动、版本、路径、来源;并允许在页面里编辑 `~/.cairn/engines.json` 覆盖项,给自动探测不到的引擎(如本机的 `pi`)手填路径/启动方式。cairn 风格,与现有 UI 一致。

本设计是子项目 B,基于子项目 A(本地引擎 worker)已落地的 `probe_engine` / 解析器 / `~/.cairn` 数据根。

## 范围与边界(已对齐)

- **只管理 Local 模式的宿主机 agent**。docker 模式的 agent **不纳管**:它们由 `container/Dockerfile` 在构建期以固定版本装入镜像,存在性/版本由镜像构建保证,运行期没有探测/配置/修复的余地。需要探测的不确定性只存在于宿主机手装(PATH 各异、版本不一、可能缺失)。
- 探测在 **server 进程所在主机**执行(`shutil.which` 等)。Local 模式下 server 在宿主机,页面正确反映"宿主机能否启动这些 agent"。**容器化 server 的部署明确排除**(那是用 docker worker、不用 local engine 的场景,引擎页对它无意义)。
- **Level 2 连通性测试不在 B**(需要 provider 凭据,server 不掌握)——留给子项目 C(对话模式)。
- 范围 = **(b) 探测 + `engines.json` 覆盖编辑器**。

## 复用(子项目 A 已有)

- `cairn/src/cairn/dispatcher/runtime/local/resolve.py`:
  - `BINARY = {"claudecode":"claude","codex":"codex","opencode":"opencode","pi":"pi"}`
  - `probe_engine(worker_type) -> {launchable, path, version, source}`
  - `resolve_engine` / `launch_argv` / `_load_overrides` / `_engines_config_path`(覆盖文件 = `~/.cairn/engines.json`,`CAIRN_HOME` 可覆盖)
- `resolve.py` 仅依赖 `os/shutil/subprocess/json`,**无 docker 依赖**,server 端 import 安全。

## 组件与架构

### 1. 解析器补充公开函数(`runtime/local/resolve.py`)

把覆盖文件的读写做成公开、可测的函数(目前 `_engines_config_path`/`_load_overrides` 是私有):
```
engines_config_path() -> Path          # 公开化(= ~/.cairn/engines.json)
load_overrides() -> dict               # 公开化(读,不存在/损坏返回 {})
set_override(worker_type, path, launcher) -> None   # 合并写入一个引擎的覆盖
remove_override(worker_type) -> None    # 删除一个引擎的覆盖
```
`set_override`/`remove_override` 读现有 JSON → 合并/删除该 key → 原子写(临时文件 + `os.replace`),首次自动建 `~/.cairn/`。`launcher` 仅接受 `direct|cmd|powershell`(非法值拒绝)。`_load_overrides`/`_engines_config_path` 保留为私有别名转调公开函数,避免破坏现有引用。

### 2. 服务端模型(`server/models.py`)

```python
class EngineInfo(BaseModel):
    type: str            # "claudecode" | "codex" | "opencode" | "pi"
    binary: str          # 对应的二进制名
    launchable: bool     # probe 结果:能否实际启动
    path: str | None
    version: str | None
    source: str | None   # "override" | "path" | None
    override: EngineOverride | None   # 当前 engines.json 里该引擎的覆盖(若有)

class EngineOverride(BaseModel):
    path: str
    launcher: Literal["direct", "cmd", "powershell"] = "direct"
```

### 3. 服务端路由(`server/routers/engines.py`,新建)

- `GET /engines` → `list[EngineInfo]`:遍历 `resolve.BINARY` 的 4 个类型,对每个跑 `probe_engine`,并附上 `load_overrides()` 里该类型的当前覆盖。**绝不返回任何凭据/env**(本页不涉及 env)。
- `PUT /engines/{type}/override`(body `EngineOverride`)→ 调 `set_override`,返回该类型刷新后的 `EngineInfo`。`type` 不在 `BINARY` 内 → 404。
- `DELETE /engines/{type}/override` → 调 `remove_override`,返回刷新后的 `EngineInfo`。
- 在 `server/app.py` 挂载 `engines.router`。

### 4. 前端(`server/static/index.html`,Alpine SPA)

- 路由:`handleRoute()` 增加 `#/engines` → `view = 'engines'`;首页加一个导航入口(齿轮区或顶栏)跳 `#/engines`。
- 页面:一个引擎表/卡片列表,每个引擎显示:`type`、可启动徽章(launchable 绿/否 灰)、`version`、`path`、`source`。
- "Refresh" 按钮 → 重新 `GET /engines`。
- 每个引擎一个"覆盖"编辑:展开后填 `path` + `launcher`(下拉 direct/cmd/powershell)→ `PUT …/override`;有覆盖时显示"清除覆盖"→ `DELETE …/override`。保存后就地刷新该行。
- 文案点明:本页反映**当前 server 主机**能否启动这些 agent;只对 Local 模式有意义。

## 数据流

1. 打开 `#/engines` → `GET /engines` → server 对 4 个类型跑 `probe_engine`(读 PATH + 增广目录 + 现有 `engines.json`)→ 返回列表。
2. 用户给 `pi` 填覆盖路径 → `PUT /engines/pi/override` → `set_override` 写 `~/.cairn/engines.json` → 返回刷新后的 `pi` 行(此时 `source="override"`、`launchable=true`)。
3. dispatcher 下次本地执行 pi 时,`resolve_engine` 读同一 `engines.json`,命中覆盖 → 正常启动。

## 错误处理

- 探测某引擎抛错 → 该行 `launchable=false`、`path/version=None`,不影响其它行。
- `engines.json` 损坏 → `load_overrides()` 返回 `{}`(不崩);`set_override` 会以合法内容重写。
- 写入失败(权限)→ 接口 500 + 明确信息;前端提示。
- 非法 `type` / `launcher` → 4xx + 明确信息。

## 测试

- `resolve` 覆盖读写:`set_override` 合并不覆盖其它引擎;`remove_override` 只删目标;损坏 JSON 时 `load_overrides` 返回 `{}`;`engines_config_path` 跟随 `CAIRN_HOME`;原子写不留临时文件。
- `GET /engines`(TestClient + monkeypatch `probe_engine`):返回 4 个类型;含 override 字段;不含任何 env/凭据。
- `PUT/DELETE /engines/{type}/override`:写入后再 `GET` 反映;非法 type→404;非法 launcher→422。
- 前端:手测(列表渲染、Refresh、填/清覆盖、徽章颜色)。

## 局限

- 反映的是 **server 进程主机**的可见性;容器化 server 不适用(已排除)。
- 不做连通性测试(Level 2 在 C)。
- 不做引擎"选择/启用"(cairn 的引擎选择在 `dispatch.yaml` 的 worker,不在本页)。

## 改动范围概览

- 修改:`runtime/local/resolve.py`(公开化 + 覆盖读写)、`server/models.py`(EngineInfo/EngineOverride)、`server/app.py`(挂载)、`server/static/index.html`(路由+页面+导航)。
- 新增:`server/routers/engines.py` + 测试。
