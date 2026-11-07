# Mac 实机检查任务 — NanoLika Desktop 0.1.36

这份文档是给在一台 Mac 上运行的 Cursor（或任何编码代理）看的。把它整个粘贴给代理，它就有了
全部上下文：仓库是什么、怎么在 Mac 上构建桌面端、要检查哪几件事、每件事的预期、代码在哪、
怎么诊断、怎么修、怎么提交。维护者（lgy0404）在 Linux 上开发，没有 Mac，所以 macOS 独有的
行为只能在这里验证。请按顺序做完所有项目，最后按「报告格式」汇报。

> 代理须知：不要问「要不要继续」，直接做。能自己查到的信息自己查。不可逆操作（删除用户数据、
> 改系统设置、推送到 main）不要做；修复都提交到自己的分支并开 PR。

## 0. 背景

- 仓库 https://github.com/nano-muse/nanoMuse （GPL-3.0-or-later）。桌面端在 `harness/`：
  `harness/dsh-nanomuse` 是跑在 DeepSeek Harness（dsh）里的 NanoLika 插件包（TypeScript，React
  通过 `h()` 写，样式在 `src/client/styles.ts` 一个文件里），`harness/desktop` 是 Electron 外壳
  （`src/main.ts` 主进程、`src/preload.ts`、`resources/*.html` 启动页/发光层/胶囊），
  `nanomuse/` 是 Python 运行时（电脑端的「双手」，`nanomuse/computer/`），由外壳作为子进程拉起。
- 文档：`docs/desktop.md`（是什么、数据目录 `~/.nanomuse/desktop`、`desktop.log`）、
  `docs/desktop-muse.md`（界面逐项说明，含「Computer use」一节）、`docs/gui.md`（双手/坐标约定）、
  `CONTRIBUTING.md`（代码约定）。`CHANGELOG.md` 顶部 `## [Unreleased]` 记录未发布改动。
- 0.1.35 在 Mac 上已知的问题：**点顶部中央的形象没有反应**（Linux 正常）。0.1.36 的修法是
  让聊天顶栏不再是窗口拖拽区（`styles.ts` 平台块，`MuseHeader.tsx` 里新增的空拖拽条
  `.nm-header-drag`），并把形象整体下移到 macOS 标题栏带以下。这需要实机确认。
- 规则：不改版本号（版本号只在发布提交里改）；新代码放在我们自己的文件/命名空间里；改
  上游文件要留 `// NanoLika:` 注释；字符串英文在前、中文随后（`locales.ts` 两份）；不往仓库里
  放笔记、截图、日志。提交用 `git commit -s`（DCO 签名）。

## 1. 环境

```bash
xcode-select --install 2>/dev/null || true          # 命令行工具（git、codesign）
# Homebrew 已装则跳过
brew install node@22 pnpm python@3.12 cliclick       # Node 22（CI 同版本）、pnpm 11、Python 3.12、cliclick（读/写鼠标位置）
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"    # Intel Mac 是 /usr/local/opt/node@22/bin
node -v   # v22.x
git clone https://github.com/nano-muse/nanoMuse.git && cd nanoMuse
git checkout -b mac-check-0.1.36
```

## 2. 构建并运行

```bash
# 2.1 插件包
cd harness/dsh-nanomuse
pnpm install --frozen-lockfile && pnpm build && pnpm typecheck && pnpm test
# 2.2 把 dsh 和插件包放到外壳的 resources/dsh（CI 的同一步）
cd ../desktop && node scripts/prepare-dsh.mjs
# 2.3 运行时：开发时不用 PyInstaller，直接用 venv 里的可执行文件
cd ../.. && python3.12 -m venv .venv && .venv/bin/pip install -e ".[hands]"
export NANOMUSE_PY="$PWD/.venv/bin/nanomuse"
# 2.4 外壳
cd harness/desktop && npm ci && npm run typecheck
```

两种跑法：

- **开发态**（改代码快速看效果）：`npm start`。注意此时进程是 Electron.app，macOS 的权限
  （辅助功能、屏幕录制）会记在 Electron 名下，不能用来验证权限流程。
- **打包态**（验证权限、启动页、Dock 图标等）：
  ```bash
  npm run dist:dir && ../../scripts/desktop-app/package-mac.sh arm64     # Intel 用 x64
  open dist/mac-arm64/nanoMuse.app                                        # 或 dist/mac/
  ```
  本地没有证书时是 ad-hoc 签名；首次打开若被 Gatekeeper 拦下，系统设置 → 隐私与安全性 → 仍要打开。
  bundle id 是 `io.github.nanomuse.desktop`。重测权限提示用
  `tccutil reset ScreenCapture io.github.nanomuse.desktop; tccutil reset Accessibility io.github.nanomuse.desktop`。
- 数据目录 `~/.nanomuse/desktop`（日志 `desktop.log`）。想从头开始：`mv ~/.nanomuse/desktop ~/.nanomuse/desktop.bak`。
- 看渲染进程内部：加 `--remote-debugging-port=9333` 启动，然后用 CDP（`curl 127.0.0.1:9333/json`
  拿到 webSocketDebuggerUrl，用 node 的 `ws` 或 `chrome-remote-interface` 执行 `Runtime.evaluate`）。
  检查完关掉，不要带着调试端口长期运行。

## 3. 检查清单（每项都要做，记录证据）

### A. 点形象（0.1.35 的 bug，0.1.36 的修复要确认）

- 做法：打开主聊天，用鼠标点顶部中央的大头像一次。
- 预期：右侧滑出「个人面板」（活动/审批/每日/灵魂），`document.documentElement` 多出
  `data-nm-profile` 属性；再点一次关闭。另外：在头像上方的空白条（约 36 px）、左侧栏顶部、
  聊天栏顶部按住拖动，窗口要能跟着动；**按住头像拖动不能移动窗口**。
- 若失败，按顺序排查：
  1. CDP 里执行
     `getComputedStyle(document.querySelector('.nm-header-face')).webkitAppRegion`（应为 `no-drag`）、
     `getComputedStyle(document.querySelector('header[data-window-drag]')).webkitAppRegion`
     （应为 `none`/空，不是 `drag`）、`document.documentElement.dataset.nmPlatform`（应为 `darwin`）。
  2. `document.querySelector('.nm-header-face').click()` 能打开面板而真实点击不能 → 是原生层
     截走了点击（标题栏带或拖拽区）。试着把 `styles.ts` 里
     `html[data-nm-platform='darwin']:not([data-nm-fullscreen]) .nm-header { top: 40px }` 改大
     （比如 56px，同时把 `header[data-window-drag]:has(.nm-header)` 的 `min-height` 加同样的量），
     或把 `harness/desktop/src/main.ts` 里 `trafficLightPosition: { x: 16, y: 18 }` 的 y 改小，看哪一个起效。
  3. 全屏（绿灯按钮）下也点一次：全屏时 `data-nm-fullscreen` 生效，头像回到 `top: 12px`。
- 代码：`harness/dsh-nanomuse/src/client/styles.ts`（搜 `nm-header-drag`、`data-nm-platform='darwin'`）、
  `src/client/MuseHeader.tsx`、`harness/desktop/src/main.ts`（`titleBarStyle: "hiddenInset"`）。

### B. 权限文案与「试一下」行

- 位置：设置 → Computer use（电脑操作）→ 权限页（`harness/dsh-nanomuse/src/client/Sections.tsx`，
  文案键 `pm*` 在 `locales.ts`，英文一份、中文一份；主进程探测在 `main.ts` 的 `nanomuse:permissions*`）。
- 预期：辅助功能、屏幕录制两行的状态与系统设置一致（授予/未授予），按钮能跳到对应的系统设置
  面板；「试一下」会截一张图并做一次无害的鼠标移动，然后用一句话报告结果；屏幕录制未授予时
  截图是黑的，要出现 `pmTryShotBlack` 那句（让人去系统设置里打开并重启）。中英文都看一遍，
  文案要准确、不夸张、无错别字。
- 用打包态测试（见上）。把 `tccutil reset` 后的「首次授权」流程也走一遍。

### C. 屏幕录制的重启对话框

- 做法：应用运行中，到系统设置 → 隐私与安全性 → 屏幕录制，打开 NanoLika Desktop。
- 预期：应用弹一次对话框「屏幕录制已允许，重新启动后生效」（英文 “Screen Recording is on; it
  takes effect after a restart”），按钮「立即重启 / 稍后」；点「立即重启」应用重开（`app.relaunch`），
  之后截图不再是黑的。对话框只出现一次，不重复弹；「稍后」之后也不要再弹。
- 代码：`harness/desktop/src/main.ts` 搜 `Screen Recording granted while running`。

### D. 胶囊 `showInactive` 是否抢焦点

- 做法：发起一个用到双手的任务（例如「截图看看屏幕上有什么」），然后切到别的应用（比如备忘录）
  继续打字。
- 预期：右上角出现胶囊（步骤 + 停止），**不抢焦点**——备忘录里的输入不中断；整屏的发光层是
  点击穿透的（能正常点到下面的窗口）。胶囊上的「停止」能点。
- 代码：`main.ts` 的 `overlayWindow`（`showInactive`、`setIgnoreMouseEvents`、`focusable: false`）。

### E. 窗口模式的点击与 Retina 映射

- 背景：macOS 上 `[hands] mode = auto` 时，一旦指定目标应用，双手就进入「窗口模式」——截图只
  截那个窗口，事件直接发给那个进程（`nanomuse/computer/mac_window.py`）。Retina 屏的窗口截图是
  点数的两倍，`WindowFrame.to_screen` 负责把图上的像素映射回屏幕点。
- 做法：开一个 TextEdit 窗口放在屏幕右下（避开左上角默认位置），对 NanoLika 说「在 TextEdit 里
  点一下工具栏最左边的按钮」或通过工具直接调用：先 `computer_screen`（app=TextEdit）看图，再
  `computer_act click` 到图上某个明确的按钮坐标。
- 预期：点在图上那个按钮上（用 `cliclick p` 读当前鼠标位置，和换算后的屏幕点比对；差距应在 2 pt 内）。
  如果偏差恰好是 2 倍，是 Retina 比例没除；如果偏移固定一个窗口原点，是窗口 frame 没加。
- 代码：`nanomuse/computer/mac_window.py`、`nanomuse/computer/link.py`（`_capture_window`、`_where`）。

### F. 新的桌面操作器（0.1.36：Electron 内的 nut.js 操作器，源自 UI-TARS-desktop）

- 背景：0.1.36 起，桌面端的截图和鼠标键盘由 Electron 主进程里的操作器完成
  （`harness/desktop/src/operator.ts`、`operator-server.ts`，依赖 `@computer-use/nut-js` 4.2.0），通过
  回环 HTTP 暴露给 Python 运行时（每次启动随机端口和 token，环境变量 `NANOMUSE_OPERATOR_URL`/
  `NANOMUSE_OPERATOR_TOKEN`，经 Host → preset 白名单 → `nanomuse mcp`）。运行时侧是
  `nanomuse/computer/operator.py`（`desktop` 后端，`auto` 在有操作器时优先选它）和 `coords.py`
  （模型的坐标单位是**它看到的那张图片**，运行时在动作前一次性换算到操作器的屏幕像素）。
  在 Linux X11（4K、缩放 2）上实测点击误差 ≤1 px；macOS 的原生二进制（`@computer-use/libnut-darwin`）、
  权限归属、坐标空间、滚动单位、热键映射、内容保护都需要实机确认。
- HTTP 契约（排查时可以直接 `curl`，token 在 `desktop.log` 不会打印，取法见下）：
  `GET /info` → `{available, reason, platform, display:{width, height, scaleFactor, logical:{width,height}}}`，
  `display.width/height` 就是操作器的坐标空间；`POST /screenshot` `{width?, height?, format?, quality?}` →
  `{base64, mime, width, height, screen:{width,height}, scaleFactor}`；`POST /execute`
  `{action, x?, y?, x2?, y2?, dy?, text?, keys?, seconds?}`，action ∈
  `move|click|double_click|right_click|middle_click|drag|scroll|type|key|wait|open_application`。
  所有请求带 `Authorization: Bearer <token>`；缺 token → 401，不可用 → 503。
- 做法与预期（按顺序做，每步记录证据）：
  1. **原生模块能加载。** 打包后：`node scripts/prepare-dsh.mjs` 之后 `npm run dist:dir`，确认
     `dist/mac-arm64/*.app/Contents/Resources/app.asar.unpacked/node_modules/@computer-use/libnut-darwin/build/Release/libnut.node`
     存在；跑 `node scripts/smoke.mjs --app dist/mac-arm64 --operator`（`/info` 可用、无 token → 401、
     320×180 PNG 可解码）。另外 `open -a "NanoLika Desktop" --args --operator-check=/tmp/op.json`
     （加 `--operator-move` 会把鼠标移到屏幕中心）写出一份自检 JSON，贴进报告。
  2. **权限归属。** 首次使用前 TCC 应各弹一次提示，并且两项都归在 **NanoLika Desktop** 名下
     （不是 Electron，不是 Python）。`tccutil reset ScreenCapture io.github.nanomuse.desktop` /
     `tccutil reset Accessibility io.github.nanomuse.desktop` 后重做一次。权限 `not-determined` 时
     `/info` 应为 `available:false` 且 reason 说清缺哪一项；此时运行时会退回 pyautogui 并缓存到重启——
     授予权限后重启应用，确认 `/info` 变为 `available:true`。
  3. **坐标空间。** Mac 上 `/info` 的 `display` 应是**点**（`scaleFactor` 报 1，`width/height` 等于
     逻辑尺寸）。`POST /execute {"action":"move","x":W/2,"y":H/2}` 后 `cliclick p` 应读到屏幕正中央
     （±1）。Retina 外接屏 / 不同缩放比例时重复（若有条件）。
  4. **端到端点击。** 让模型做「把鼠标移到屏幕正中央」和「点一下 Dock 里的访达」：发光层里的红色虚线环
     要落在模型点击的地方，聊天里 Hands 卡片的坐标与之一致，Dock 图标确实被点到。
  5. **滚动单位。** `scroll dy=±3` 在 Mac 上应约滚三格（Mac 按像素、Windows 按 clicks×120、Linux 按
     wheel click）；偏多偏少记录倍数。
  6. **热键与输入。** 模型发 `ctrl+c` 在 Mac 上应变成 ⌘C（`operator.ts` 的热键表）；`type` 含中文时走剪贴板
     粘贴，粘贴后用户原来的剪贴板内容应被恢复。
  7. **内容保护。** Mac 上发光层（glow）不应出现在 `/screenshot` 的图里（Linux 是截图瞬间隐藏；Mac 靠
     `setContentProtection`）。截一张有标记时的图核对。
  8. **窗口模式**（与 E 节一起）：`[hands] mode = window` 时点击仍应准确，Retina 映射不重复缩放。
- 诊断：`desktop.log` 中搜 `operator`（启动行有端口和 available；token 不打印）。要 `curl` 时从
  运行时进程的环境里取：`PID=$(pgrep -f "[n]anomuse mcp" | head -1); ps eww -p "$PID" | tr ' ' '\\n' | grep NANOMUSE_OPERATOR_`
  （只在本机终端里用，不要把 token 写进报告）。常见原因：权限未归到 NanoLika Desktop → 按 2 节 reset；`libnut.node` 缺失 → `asarUnpack` 没生效，
  检查 `electron-builder.yml`；坐标偏一倍 → `display.scaleFactor` 在 Mac 上没取 1，看 `operator.ts`
  的 `screenSpace`。
- 代码：`harness/desktop/src/operator.ts`、`operator-server.ts`、`main.ts`（启动/注入/`--operator-check`）、
  `resources/glow.html`（标记）、`nanomuse/computer/operator.py`、`coords.py`、`link.py`、
  `harness/dsh-nanomuse/presets/nanomuse.patch.yml`（env 白名单）。

### G. 启动页与图标

- 启动时先出现启动页：NanoLika 图标在一个安静的加载圆环里，下面是字标和「正在启动」；没有
  龙、没有任何形象。深浅色模式都看。Dock 图标、菜单栏托盘图标正常。
- 代码：`harness/desktop/resources/loading.html`、`main.ts` 的 `createWindow`。

### H. 顺手检查

- 红绿灯按钮位置是否和左侧栏顶部（78 px）协调；窗口最小尺寸下顶栏不重叠。
- 语言跟随系统（中文系统下界面是中文）。
- `desktop.log` 里没有重复刷屏的错误。

## 4. 修复与提交

- 直接在本分支修。代码放对位置：插件 `harness/dsh-nanomuse/src/`（客户端在 `src/client/`），
  外壳 `harness/desktop/src/`，运行时 `nanomuse/`。不要改版本号。
- 跑检查：`cd harness/dsh-nanomuse && pnpm typecheck && pnpm test && pnpm build`；
  `cd harness/desktop && npm run typecheck`；动了 Python 就 `.venv/bin/ruff check nanomuse tests &&
  .venv/bin/mypy nanomuse && .venv/bin/pytest tests -q`。
- `CHANGELOG.md` 的 `## [Unreleased]` 下按平台小节（Desktop / Runtime）写一句话，过去式，说清
  改了什么、为什么。
- 提交：`git add -A && git commit -s -m "fix(desktop): <一句话>"`；推送 `git push -u origin mac-check-0.1.36`；
  `gh pr create --base main --title "Mac check 0.1.36: <摘要>" --body-file <报告文件>`（没有 gh 就把
  分支名和报告发回来）。不要把 `~/.nanomuse`、密钥、带 token 的日志放进提交。

## 5. 报告格式

每一项 A–H：**通过 / 失败 / 无法测试**，一两句证据（看到了什么、`cliclick p` 的数字、日志行），
失败项的根因、修复的提交号、还剩什么没解决。最后列出环境（macOS 版本、芯片、显示器与缩放比例、
Node/Python 版本）。
