# FlockOS 系统级菜单归位 + flock 顶栏对齐 cairn — 设计

日期:2026-06-21
状态:待评审(brainstorming + 可视化伴侣已确认布局与选中态)

## 目标

把 **Engines / Chat / Skills** 从 cairn 子系统顶栏"提升"为 FlockOS 系统级入口,落在 flock 主页(`/flock/`)顶栏;同时把 flock 顶栏重做成 cairn 的浅 slate / indigo-brand 视觉风格,使两个子系统观感统一。

纯 **UI 层**改动:这三个功能的后端路由(`/engines`、`/chat`、`/skills`)与页面实现(cairn SPA 的 hash 视图)**保持不动**,只是入口上移、深链进入。

## 已确认决策(brainstorming + 可视化伴侣)

| 决策 | 选择 |
| --- | --- |
| 提升方式 | UI 入口上移 + 深链(`<a href="/cairn#/engines">` 等),后端/页面仍在 cairn |
| cairn 顶栏那三个按钮 | 从 cairn 移除(系统级只在 FlockOS 主页出现) |
| flock 顶栏改造程度 | 对齐 cairn 风格 + 加系统按钮;保留 flock 自有控件 |
| 顶栏布局 | v2:flock 自有控件在左,系统组(Engines/Chat/Skills + Cairn 控制台)聚右,Connected 最右 |
| 视图切换器选中态 | 方案 B:分段白胶囊(灰轨道 + 白色浮起激活段 + 轻阴影 + brand 字) |
| Publish 选中/主按钮态 | 方案 A:软色调(brand-50 底 + brand-600 字 + brand-200 描边),非实心填充 |
| 强调色 | **cairn 的 indigo brand**(brand-50 `#eef2ff`、brand-100 `#e0e7ff`、brand-200 `#c7d2fe`、brand-400 `#818cf8`、brand-500 `#6366f1`、brand-600 `#4f46e5`)。可视化 mockup 里用的 teal 仅为占位,实际取 cairn 同款 indigo。 |

## 背景(既有事实)

- cairn 顶栏的三个按钮在 `cairn/src/cairn/server/static/index.html`(约 L274–285),用 `location.hash = '/engines' | '/chat' | '/skills'` 切到 cairn SPA 的 hash 视图;样式为 cairn 胶囊:`h-7 px-2.5 rounded-lg border border-slate-200 text-xs text-slate-500 hover:bg-slate-50 hover:text-slate-700`。
- cairn 的 hash 路由与路径无关:在 `/cairn#/engines` 下也能正确渲染对应视图(已验证 `/cairn` 可用),engines 视图含 **Tools(graphify/codegraph)** 分组。
- flock 顶栏在 `flock/src/flock/frontend/src/components/layout/DashboardLayout.tsx`(`<header className="dashboard-header">`),用 className 驱动样式(`view-toggle-group/-button(.active)`、`dashboard-actions`、`controls-toggle(.primary/.active)`);其 CSS 在 layout 的样式文件里(实现时定位,可能是同目录 `.css` 或 `styles/`)。flock 主页已在该 header 里有一个"Cairn 控制台 →"链接(本轮迁移期加的)。
- flock 前端构建产物在 `flockos/static/flock/`(`flockos start` 缺失时自动 build)。

## 组件 A:flock 顶栏(`DashboardLayout.tsx` + 其样式)

### A1. 布局顺序(v2,从左到右)
```
🐔 Flock  |  VIEW [Agent View | Blackboard View]  |  (spacer)
   ── flock 自有控件 ──            ── 系统级 / 跨子系统 ──
   + Publish  Agent Details  Settings  (help?) (clear?)  ┊  Engines  Chat  Skills  Cairn 控制台 →  ┊  ● Connected
```
- flock 自有控件(Publish / Agent Details / Settings / 现有的 help、clear 等图标按钮)保留在 spacer 右侧、靠左。
- 新增系统组:`Engines` `Chat` `Skills` 三个按钮 + 已有的 `Cairn 控制台 →`,聚成一组靠右(用一根分隔竖线与左侧控件区隔开)。
- `● Connected` 状态保持在最右。

### A2. 视图切换器(选中态 = 方案 B,分段白胶囊)
- 轨道:`background: slate-100 (#f1f5f9)`,`border: 1px slate-200 (#e2e8f0)`,圆角(~10px),内边距 3px。
- 未选段:透明底、`text slate-500 (#64748b)`。
- 选中段:`background: #fff`、`text brand-600 (#4f46e5)`、`font-weight:600`、`box-shadow: 0 1px 2px rgba(15,23,42,.12)`、圆角(~7px)。
- 去掉当前的整段实心 brand 填充。

### A3. Publish(主按钮态 = 方案 A,软色调)
- `background: brand-50 (#eef2ff)`、`text: brand-600 (#4f46e5)`、`border: 1px brand-200 (#c7d2fe)`、`font-weight:600`;hover 略深(如 `background:#e0e7ff`)。
- 去掉当前紫色实心填充。`.active` 态可在此基础上略加深背景或描边。

### A4. 其余动作按钮 + 系统按钮(cairn 胶囊)
- Agent Details / Settings / help / clear 等:统一为 cairn 中性胶囊——高 28px、`px 10px`、圆角 8px、`border slate-200`、`text-xs text slate-500`、hover `bg slate-50 / text slate-700`。
- 系统按钮 `Engines` `Chat` `Skills`:同款**中性 slate 胶囊**(与 cairn 原来的三个按钮一致,不抢 brand 色),各为 `<a>`:
  - Engines → `/cairn#/engines`
  - Chat → `/cairn#/chat`
  - Skills → `/cairn#/skills`
  - 点击为整页导航到 cairn SPA(离开 flock 主页);可带小图标(emoji 或 svg),从简。
- `Cairn 控制台 →`:brand-600 文字强调(指向 `/cairn`,即 cairn 项目面板)。
- `● Connected`:保留,改为低饱和的 brand/绿点 + slate 文字的小标记,贴合整体。

### A5. 整体
- 顶栏背景白、底部细 slate 描边,与 cairn 顶栏一致的高度/间距/圆角语言;按钮尺寸统一为 cairn 的小胶囊(不要现在的大按钮)。

## 组件 B:cairn 顶栏(`index.html`)

- 删除三个按钮(约 L274–285:`Engines` / `Chat` / `Skills`,即 `location.hash = '/engines|chat|skills'` 那三段)。
- 顶栏只保留项目相关:`ALL` 计数筛选、`Stop Active`、`+ New Project`、`Human`、设置齿轮。
- **保留** cairn 的 engines/chat/skills 视图与 hash 路由(`#/engines` 等),它们经 flock 主页深链进入;`loadEngines()`/`loadChatWorkers()`/skills 逻辑不动。

## 深链行为与验证

- 在 `/flock/` 点 `Engines` → 浏览器跳 `/cairn#/engines` → cairn SPA 加载、hash 路由渲染 engines 视图(含 Tools 分组)。Chat/Skills 同理。
- cairn 的 `/cairn` 入口仍展示项目面板;`Cairn 控制台 →` 指向它。

## 构建与测试

- 改完 `DashboardLayout.tsx` + 样式后:`cd flock/src/flock/frontend && npm run build`,把 `dist/*` 拷到 `flockos/static/flock/`(与现有流程一致)。
- 自动校验:
  - `GET /flock/` 200,且构建产物/源码里含三个深链 `/cairn#/engines`、`/cairn#/chat`、`/cairn#/skills`。
  - `cairn/src/cairn/server/static/index.html` 不再含 `location.hash = '/engines'`(及 chat/skills)那三个顶栏按钮(grep 确认);但 `#/engines` 视图路由仍在。
  - `uv run pytest flockos/tests -q` 与 `uv run pytest cairn/tests -q` 全绿(后端无改动,应不受影响)。
- 人工:加载 `/flock/` 看顶栏新布局 + 选中态(切换器白胶囊、Publish 软色调);点三个系统按钮能进 cairn 对应视图;`/cairn` 顶栏不再有这三个按钮。

## 不做(YAGNI)

- 不在 flock 里用 React 原生重写 engines/chat/skills 三个页面。
- 不把后端路由从 cairn 迁出(留作以后真正的 FlockOS shell)。
- 不改 engines/chat/skills 视图内部逻辑。
- 不做 flock 顶栏 v2 之外的更大重构。

## 时序

本设计的 UI 改动落地、测试通过后,再把 `feat/flockos-migration` 合并到 `main`(用户已确认此顺序)。
