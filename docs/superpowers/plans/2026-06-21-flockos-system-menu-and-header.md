# FlockOS 系统级菜单归位 + flock 顶栏对齐 cairn Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Engines/Chat/Skills 从 cairn 顶栏移除并作为系统级入口加到 flock 主页顶栏(深链到 cairn 视图),同时把 flock 顶栏重做成 cairn 的浅 slate / indigo 风格(切换器=分段白胶囊,Publish=软色调)。

**Architecture:** 纯前端改动。两处:(1) cairn 的 `static/index.html` 删除三个顶栏按钮;(2) flock React 看板 `DashboardLayout.tsx`(结构 v2 + 系统按钮)与 `DashboardLayout.css`(风格对齐)。后端路由与 cairn 的 hash 视图不动。改完重建 flock 前端到 `flockos/static/flock`。

**Tech Stack:** cairn 静态 SPA(Alpine + Tailwind via vendor)/ flock React 19 + Vite + CSS / uv 工作区 / pytest。

设计来源:`docs/superpowers/specs/2026-06-21-flockos-system-menu-and-header-design.md`。

## 关键既有事实
- cairn 顶栏三按钮:`cairn/src/cairn/server/static/index.html` 约 L274–285,`@click="location.hash='/engines'|'/chat'|'/skills'"`,胶囊样式 `h-7 px-2.5 rounded-lg border border-slate-200 text-xs text-slate-500 hover:bg-slate-50 hover:text-slate-700`。对应的 hash **视图路由**在 ~L1639(`#/engines`)等,**保留不动**。
- cairn brand = indigo:`brand-50 #eef2ff · brand-100 #e0e7ff · brand-200 #c7d2fe · brand-400 #818cf8 · brand-600 #4f46e5`。
- flock 顶栏:`flock/src/flock/frontend/src/components/layout/DashboardLayout.tsx`,`<header className="dashboard-header">` 内有 `dashboard-title`、`view-toggle-container`(`view-toggle-group`>2×`view-toggle-button`)、`dashboard-actions`(`controls-toggle primary`=Publish、`controls-toggle`×2=Agent Details/Settings、`icon-button help-button`、`icon-button clear-button`、`<a href="/cairn" style={{marginLeft:'auto',…}}>Cairn 控制台 →</a>`、`<Header/>`=Connected)。
- flock 顶栏样式:`flock/src/flock/frontend/src/components/layout/DashboardLayout.css`。
- 构建:`cd flock/src/flock/frontend && npm run build` → `dist/`;部署到 `flockos/static/flock/`。
- 验证服务:`uv run flockos start --port <p>` 或 TestClient(`flockos.app:build_app`)。

---

## Task 1: cairn 顶栏移除 Engines/Chat/Skills 三按钮

**Files:**
- Modify: `cairn/src/cairn/server/static/index.html`(约 L273–285)

- [ ] **Step 1: 删除三按钮**

在 `index.html` 顶栏里删除这三个按钮(Engines/Chat/Skills)。它们形如(完整匹配后删除,保留前后的 `Stop Active`/`New Project`/`Human`/齿轮):
```html
      <button @click="location.hash = '/engines'" class="h-7 px-2.5 rounded-lg border border-slate-200 text-xs text-slate-500 hover:bg-slate-50 hover:text-slate-700 transition inline-flex items-center gap-1.5 shrink-0" title="Engine management">
        ...
        Engines
      </button>
      <button @click="location.hash = '/chat'" ...>
        ...
        Chat
      </button>
      <button @click="location.hash = '/skills'" ...>
        ...
        Skills
      </button>
```
先 `grep -n "location.hash = '/engines'\|location.hash = '/chat'\|location.hash = '/skills'" cairn/src/cairn/server/static/index.html` 定位三段(含各自的 `<svg>`/文字),整段删除。**不要**碰 ~L1639 的 hash 路由分支(`hash === '#/engines'` 等)和 engines/chat/skills 的视图模板。

- [ ] **Step 2: 验证移除 + 视图仍在**

Run:
```bash
cd /Users/nicholas/project/ai/Cairn
grep -c "location.hash = '/engines'" cairn/src/cairn/server/static/index.html   # 期望 0
grep -c "location.hash = '/chat'" cairn/src/cairn/server/static/index.html      # 期望 0
grep -c "location.hash = '/skills'" cairn/src/cairn/server/static/index.html    # 期望 0
grep -c "hash === '#/engines'" cairn/src/cairn/server/static/index.html         # 期望 >=1 (路由保留)
```
Expected: 前三个 0,最后一个 ≥1。

- [ ] **Step 3: cairn 后端回归**

Run: `uv run pytest cairn/tests -q`
Expected: 196 passed(后端无改动)。

- [ ] **Step 4: 提交**

```bash
git add cairn/src/cairn/server/static/index.html
git commit -m "feat(flockos): remove Engines/Chat/Skills from cairn header (now system-level)"
```

---

## Task 2: flock 顶栏加系统按钮 + 布局 v2(TSX)

**Files:**
- Modify: `flock/src/flock/frontend/src/components/layout/DashboardLayout.tsx`(L243 附近)

- [ ] **Step 1: 用系统组替换原 Cairn 链接**

把当前第 243 行那一行(`<a href="/cairn" ... >Cairn 控制台 →</a>`)替换为一个**系统组**容器,内含三个系统按钮 + Cairn 链接;并保留其后的 `<Header />`:
```tsx
          <div className="system-group">
            <span className="header-divider" aria-hidden="true" />
            <a className="system-pill" href="/cairn#/engines" title="Engine management">Engines</a>
            <a className="system-pill" href="/cairn#/chat" title="Engine chat / debug">Chat</a>
            <a className="system-pill" href="/cairn#/skills" title="Skills management">Skills</a>
            <a className="cairn-console-link" href="/cairn" title="Cairn 控制台">Cairn 控制台 →</a>
          </div>
```
说明:`marginLeft:auto` 从原 `<a>` 内联样式移除,改由 `.system-group`(Task 3 的 CSS)承担,让 flock 自有控件留左、系统组+Connected 靠右。`<Header />`(第 245 行)保持在 `.system-group` 之后不动。

- [ ] **Step 2: 类型检查(确认无 TSX 错误)**

Run:
```bash
cd /Users/nicholas/project/ai/Cairn/flock/src/flock/frontend
npx tsc --noEmit
```
Expected: 无报错(若报缺少 `.css` 选择器无关——tsc 不校验 CSS 类名)。

- [ ] **Step 3: 提交**

```bash
cd /Users/nicholas/project/ai/Cairn
git add flock/src/flock/frontend/src/components/layout/DashboardLayout.tsx
git commit -m "feat(flockos): flock header system group (Engines/Chat/Skills deep-link to cairn)"
```

---

## Task 3: flock 顶栏样式对齐 cairn(CSS:slate 胶囊 + 切换器 B + Publish A)

**Files:**
- Modify: `flock/src/flock/frontend/src/components/layout/DashboardLayout.css`

先通读该文件里以下选择器的现有规则:`.view-toggle-label`、`.view-toggle-group`、`.view-toggle-button`、`.view-toggle-button.active`、`.controls-toggle`、`.controls-toggle.primary`、`.controls-toggle.active`、`.icon-button`。下面给出目标规则——**替换这些选择器中与配色/边框/阴影/圆角相关的属性**(保留布局相关的 flex/对齐属性即可),并在文件末尾**追加** `.system-group / .system-pill / .header-divider / .cairn-console-link` 四个新规则。

- [ ] **Step 1: 切换器 = 方案 B(分段白胶囊)**

把视图切换器相关规则改成:
```css
.view-toggle-label {
  font-size: 11px;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: #94a3b8;
}
.view-toggle-group {
  display: inline-flex;
  gap: 2px;
  padding: 3px;
  background: #f1f5f9;            /* slate-100 track */
  border: 1px solid #e2e8f0;     /* slate-200 */
  border-radius: 10px;
}
.view-toggle-button {
  height: 26px;
  padding: 0 12px;
  border: none;
  background: transparent;
  color: #64748b;                /* slate-500 */
  font-size: 12px;
  border-radius: 7px;
  cursor: pointer;
  transition: all .15s ease;
}
.view-toggle-button:hover { color: #334155; }
.view-toggle-button.active {
  background: #ffffff;
  color: #4f46e5;                /* brand-600 */
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(15, 23, 42, .12);
}
```

- [ ] **Step 2: 动作按钮 = cairn slate 胶囊;Publish = 方案 A(软色调)**

```css
.controls-toggle {
  height: 28px;
  padding: 0 10px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #e2e8f0;     /* slate-200 */
  border-radius: 8px;
  background: #ffffff;
  color: #64748b;                /* slate-500 */
  font-size: 12px;
  cursor: pointer;
  transition: all .15s ease;
}
.controls-toggle:hover { background: #f8fafc; color: #334155; border-color: #cbd5e1; }
.controls-toggle.active { background: #f8fafc; color: #334155; border-color: #cbd5e1; }
.controls-toggle svg { width: 14px; height: 14px; }

/* Publish — 软色调,非实心 */
.controls-toggle.primary {
  background: #eef2ff;           /* brand-50 */
  color: #4f46e5;                /* brand-600 */
  border: 1px solid #c7d2fe;     /* brand-200 */
  font-weight: 600;
}
.controls-toggle.primary:hover { background: #e0e7ff; color: #4f46e5; border-color: #a5b4fc; }
.controls-toggle.primary.active { background: #e0e7ff; border-color: #a5b4fc; }

/* 纯图标按钮(help/clear)= 方形 slate 胶囊 */
.icon-button {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #ffffff;
  color: #64748b;
  cursor: pointer;
  transition: all .15s ease;
}
.icon-button:hover { background: #f8fafc; color: #334155; border-color: #cbd5e1; }
.icon-button svg { width: 14px; height: 14px; }
```

- [ ] **Step 3: 追加系统组样式**

在文件末尾追加:
```css
.system-group {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;            /* 把系统组 + Connected 推到最右 */
}
.header-divider {
  width: 1px;
  height: 22px;
  background: #e2e8f0;
  margin: 0 2px;
}
.system-pill {
  height: 28px;
  padding: 0 10px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #e2e8f0;     /* slate 中性,贴合 cairn 原三按钮 */
  border-radius: 8px;
  background: #ffffff;
  color: #64748b;
  font-size: 12px;
  text-decoration: none;
  transition: all .15s ease;
}
.system-pill:hover { background: #f8fafc; color: #334155; border-color: #cbd5e1; }
.cairn-console-link {
  height: 28px;
  padding: 0 10px;
  display: inline-flex;
  align-items: center;
  border: 1px solid #c7d2fe;     /* brand-200 */
  border-radius: 8px;
  color: #4f46e5;                /* brand-600 */
  font-size: 12px;
  text-decoration: none;
  transition: all .15s ease;
}
.cairn-console-link:hover { background: #eef2ff; }
```

- [ ] **Step 4: 构建确认无 CSS/TS 错误**

Run:
```bash
cd /Users/nicholas/project/ai/Cairn/flock/src/flock/frontend
npm run build
```
Expected: build 成功(`tsc && vite build` 无错;chunk-size warning 可忽略)。

- [ ] **Step 5: 提交**

```bash
cd /Users/nicholas/project/ai/Cairn
git add flock/src/flock/frontend/src/components/layout/DashboardLayout.css
git commit -m "feat(flockos): flock header styled to cairn (slate pills, segmented toggle, soft Publish)"
```

---

## Task 4: 部署构建产物 + 端到端验证 + 回归

**Files:**
- Modify: `flockos/static/flock/`(构建产物)

- [ ] **Step 1: 重建并部署到 flockos 静态目录**

Run:
```bash
cd /Users/nicholas/project/ai/Cairn/flock/src/flock/frontend
npm run build
rm -rf ../../../../flockos/static/flock
mkdir -p ../../../../flockos/static/flock
cp -R dist/* ../../../../flockos/static/flock/
ls ../../../../flockos/static/flock/index.html
```
Expected: `index.html` 存在。

- [ ] **Step 2: 构建产物含三条深链**

Run:
```bash
cd /Users/nicholas/project/ai/Cairn
grep -rl "/cairn#/engines" flockos/static/flock && echo OK-engines
grep -rl "/cairn#/chat" flockos/static/flock && echo OK-chat
grep -rl "/cairn#/skills" flockos/static/flock && echo OK-skills
```
Expected: 三个 `OK-*` 都打印(深链打进了产物)。

- [ ] **Step 3: 统一 app 端到端**

Run:
```bash
cd /Users/nicholas/project/ai/Cairn
export FLOCKOS_HOME=/tmp/flockos-t4 CAIRN_HOME=/tmp/flockos-t4c
rm -rf /tmp/flockos-t4 /tmp/flockos-t4c
uv run flockos start --port 8014 >/dev/null 2>&1
curl -s -o /dev/null --retry 20 --retry-connrefused --retry-delay 1 http://127.0.0.1:8014/flock/
echo "flock home: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8014/flock/)"
echo "cairn page: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8014/cairn)"
echo "engines view (deep-link target): $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8014/engines)"
uv run flockos stop >/dev/null 2>&1
```
Expected: 三个都 200。

- [ ] **Step 4: 全量回归**

Run:
```bash
cd /Users/nicholas/project/ai/Cairn
uv run pytest flockos/tests -q
uv run pytest cairn/tests -q
```
Expected: flockos 17 passed、cairn 196 passed。

- [ ] **Step 5: 人工目视(用户)**

`uv run flockos start --foreground` → 浏览器:
- `/flock/` 顶栏:flock 自有控件在左(Publish 软色调、切换器白胶囊激活段),系统组 Engines/Chat/Skills + Cairn 控制台 靠右,Connected 最右;整体 cairn slate 风格。
- 点 Engines/Chat/Skills → 跳到 `/cairn#/engines|chat|skills` 对应视图(engines 含 Tools 分组)。
- `/cairn` 顶栏不再有这三个按钮。
Ctrl-C 停。

- [ ] **Step 6: 提交构建产物**

```bash
git add flockos/static/flock
git commit -m "build(flockos): rebuild flock dashboard with cairn-aligned header + system menu"
```

---

## Self-Review(写计划后自检)

**Spec 覆盖:**
- 提升方式(UI 上移+深链):Task 2(系统按钮 `<a href="/cairn#/...">`)✓
- cairn 顶栏移除三按钮:Task 1 ✓
- 布局 v2(自有控件左、系统组右、Connected 最右):Task 2(结构)+ Task 3(`.system-group margin-left:auto`)✓
- 切换器 B / Publish A / slate 胶囊 / indigo brand:Task 3 ✓
- 深链行为验证:Task 4 Step 2/3 ✓
- 构建+回归:Task 4 ✓
- 不做(后端不动、不重写视图):无对应改动 ✓

**占位扫描:** 无 TBD/TODO;CSS 规则均给出完整属性与 hex;index.html 删除给了 grep 定位法(因三段含 svg,用 grep 定位整段删除,非伪代码)。

**类型/命名一致性:** TSX 新增类名 `system-group / system-pill / header-divider / cairn-console-link` 与 Task 3 CSS 选择器逐一对应 ✓;深链路径 `/cairn#/engines|chat|skills` 与 Task 4 grep 断言一致 ✓;构建产物相对路径 `../../../../flockos/static/flock`(从 `flock/src/flock/frontend`)与 Task 4 一致 ✓。
