# Mac 实机检查任务 — NanoLika Desktop 0.1.37

这份文档是给在一台 Mac 上运行的 Cursor（或任何编码代理）看的。把它整个粘贴给代理，它就有了
全部上下文：仓库是什么、怎么在 Mac 上构建桌面端、要检查哪几件事、每件事的预期、代码在哪、
怎么诊断、怎么修、怎么提交。维护者（lgy0404）在 Linux 上开发，没有 Mac，所以 macOS 独有的
行为只能在这里验证。请按顺序做完所有项目，最后按「报告格式」汇报。
[0.1.36 的任务单](mac-check-0.1.36.md)仍然有效（点形象、胶囊焦点、窗口模式、滚动单位、热键等），
这一份只写 0.1.37 新增或改变的预期；时间有限就先做这一份，再补 0.1.36 的 D、E、F.3–F.8。

> 代理须知：不要问「要不要继续」，直接做。能自己查到的信息自己查。不可逆操作（删除用户数据、
> 改系统设置、推送到 main）不要做；修复都提交到自己的分支并开 PR。

## 0. 背景

- 仓库 https://github.com/nano-muse/nanoMuse （GPL-3.0-or-later）。桌面端在 `harness/`：
  `harness/dsh-nanomuse` 是跑在 DeepSeek Harness（dsh）里的 NanoLika 插件包，`harness/desktop`
  是 Electron 外壳（`src/main.ts` 主进程、`src/operator.ts` 操作器、`src/mac-permissions.ts`
  权限模块、`src/preload.ts`、`resources/*.html`），`nanomuse/` 是 Python 运行时（电脑端的「双手」，
  `nanomuse/computer/`），由外壳作为子进程拉起。
- 文档：`docs/desktop.md`（新增「macOS permissions」「Linux notes」两节）、`docs/gui.md`
  （「one path」一段）、`docs/desktop-muse.md`、`CONTRIBUTING.md`。`CHANGELOG.md` 顶部
  `## [Unreleased]` 记录未发布改动。
- 0.1.37 在 macOS 上改了什么（都没有在 Mac 上跑过，所以每一条都要实机确认）：
  1. **启动时就问权限。** 外壳加了两个可选原生依赖 `@computer-use/node-mac-permissions` 2.2.2
     和 `@computer-use/mac-screen-capture-permissions` 1.0.2（UI-TARS-desktop 用的那两个），
     `whenReady` 之后、`boot()` 之前：哪一项没授权就弹系统自己的对话框
     （`CGRequestScreenCaptureAccess` / `AXIsProcessTrustedWithOptions` 带提示），并打开一次
     「屏幕录制」面板。权限页的「请求」按钮也走原生调用，不再用 1×1 的 `desktopCapturer` 探测；
     状态来自 TCC（`getAuthStatus`），不是 Electron 的 `systemPreferences` 猜测。
  2. **截图只有一条路。** 应用在的时候（`NANOMUSE_OPERATOR_URL` 已设置）运行时永远不回退到
     `mss` / `screencapture` / `pyautogui`：操作器给图，或者把操作器的理由作为错误返回
     （`nanomuse/computer/link.py` `_capture_screen`、`hands.py` `pick_backend`、
     `operator.py` `operator_owns_the_screen`）。
  3. **黑图检测。** `operator.ts` 的 `capture()`：`desktopCapturer.getSources` 被拒（Electron ≥ 32 没
     权限时直接 reject）或 libnut 抓到的整张图全黑（采样每通道 < 8）→ `OperatorError(…, 403)`，
     文案 *macOS: switch on NanoLika Desktop under System Settings → Privacy & Security → Screen
     Recording, then quit and reopen the app.*，不再把黑图当截图返回。
  4. **重启走 quit。** `relaunchNow()` 改为 `app.relaunch(); app.quit()`，`before-quit` 会先停掉
     host 和操作器服务器；原先的 `app.exit(0)` 跳过了它们，留下没权限的旧 `nanomuse mcp`。
  5. **Chromium 开关。** 启动前（仅 darwin）
     `app.commandLine.appendSwitch('disable-features', 'ThumbnailCapturerMac:capture_mode/sc_screenshot_manager,ScreenCaptureKitPickerScreen,ScreenCaptureKitStreamPickerSonoma')`
     ——electron/electron#44504，ScreenCaptureKit 缩略图不确定地丢帧。
  6. **签名。** `scripts/desktop-app/package-mac.sh` 的内层签名循环现在也走
     `Resources/app.asar.unpacked`——那里有 `libnut.node`、`permissions.node`、
     `screencapturepermissions.node`；0.1.36 只走 `runtime` 和 `dsh`，Developer ID 公证会因
     「未签名的 Mach-O」被拒。要在有证书的 Mac 上验证一次。见 F 节。
- 规则：不改版本号；新代码放在我们自己的文件/命名空间里；字符串英文在前、中文随后；不往仓库里
  放笔记、截图、日志。提交用 `git commit -s`（DCO 签名）。

## 1. 环境

```bash
xcode-select --install 2>/dev/null || true          # 命令行工具（git、codesign、node-gyp 要用的 clang）
brew install node@22 pnpm python@3.12 cliclick
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"    # Intel Mac 是 /usr/local/opt/node@22/bin
node -v   # v22.x
git clone https://github.com/nano-muse/nanoMuse.git && cd nanoMuse
git checkout -b mac-check-0.1.37
```

## 2. 构建并运行

```bash
cd harness/dsh-nanomuse && pnpm install --frozen-lockfile && pnpm build && pnpm typecheck && pnpm test
cd ../desktop && node scripts/prepare-dsh.mjs
cd ../.. && python3.12 -m venv .venv && .venv/bin/pip install -e ".[hands]"
export NANOMUSE_PY="$PWD/.venv/bin/nanomuse"
cd harness/desktop && npm ci && npm run typecheck
```

`npm ci` 时留意两件事并写进报告：
- `@computer-use/mac-screen-capture-permissions` 的 install 脚本在 Mac 上跑 `node-gyp rebuild`
  （N-API，C 源一文件，需要 Xcode CLT 和 Python 3）。装完确认
  `node_modules/@computer-use/mac-screen-capture-permissions/build/Release/screencapturepermissions.node`
  存在。没有就看 npm 是否跳过了脚本（新 npm 默认拦截 install 脚本——`package.json` 的 `allowScripts`
  已放行这一个；`npm install-scripts ls` 查看）。
- `node_modules/@computer-use/node-mac-permissions/build/Release/permissions.node` 是预编译的
  universal 二进制，直接到位。

两种跑法：

- **开发态** `npm start`：进程是 Electron.app，权限记在 Electron 名下，**不能**用来验证权限流程。
- **打包态**（本任务单的所有权限项都用它）：
  ```bash
  npm run dist:dir && ../../scripts/desktop-app/package-mac.sh arm64     # Intel 用 x64
  open dist/mac-arm64/nanoMuse.app                                        # 或 dist/mac/
  ```
  bundle id `io.github.nanomuse.desktop`。重测首次授权：
  `tccutil reset ScreenCapture io.github.nanomuse.desktop; tccutil reset Accessibility io.github.nanomuse.desktop`。
- 数据目录 `~/.nanomuse/desktop`（日志 `desktop.log`）。从头开始：`mv ~/.nanomuse/desktop ~/.nanomuse/desktop.bak`。

## 3. 检查清单（每项都要做，记录证据）

### A. 原生模块进了包并能加载

- `dist/mac-arm64/nanoMuse.app/Contents/Resources/app.asar.unpacked/node_modules/@computer-use/` 下应有
  `libnut-darwin/build/Release/libnut.node`、`node-mac-permissions/build/Release/permissions.node`、
  `mac-screen-capture-permissions/build/Release/screencapturepermissions.node`（`asarUnpack` 覆盖
  `**/node_modules/@computer-use/**`）。
- 启动打包态后 `desktop.log` 里应有一行
  `permissions: accessibility=<…> screen=<…> (native)`。若括号里是 `systemPreferences — …`，
  原生模块没加载，把后面的原因贴进报告（最常见：`.node` 没编出来，或 `bindings` 在 asar 里找不到）。
- `node scripts/smoke.mjs --app dist/mac-arm64 --operator` 仍要通过（`/info` 可用、无 token → 401、
  320×180 PNG 可解码）——这是在**已授权**的状态下跑；未授权时它应当失败并带 403 的文案（见 C）。
- 代码：`harness/desktop/src/mac-permissions.ts`、`electron-builder.yml`、`package.json`。

### B. 启动时的权限提示（点 1）

- 做法：`tccutil reset` 两项后，`open dist/mac-arm64/nanoMuse.app`。
- 预期：窗口起来的同时（不等 boot 完），系统弹出「屏幕录制」请求和「辅助功能」请求各一次，
  两项都归在 **NanoLika Desktop** 名下，并且「系统设置 → 隐私与安全性 → 屏幕录制」面板被打开一次。
  再次启动（权限仍未授予）：面板再开一次、系统对话框**不**再弹（macOS 只提示一次，之后只能去面板开）。
  两项都已授予时：什么都不弹、什么都不开，日志只有那一行 `permissions: …`。
- 带 `--operator-check=/tmp/op.json` 或 `--screenshot=/tmp/x.png` 启动时，不应有任何提示。
- 权限页（设置 → Computer use → Permissions）：两行状态与系统设置一致——`not-determined` 在
  reset 后、`denied` 在拒绝后、`granted` 在打开后（主进程 `permissionState()` 用 TCC 的
  `getAuthStatus`）。点「请求」：屏幕录制走 `askForScreenCaptureAccess()` 再开面板；辅助功能走
  `askForAccessibilityAccess()`，未授予时开面板。
- 代码：`main.ts` 搜 `ensureMacPermissionsAtLaunch`、`requestScreenRecording`、`requestAccessibility`、
  `nanomuse:permissions:request`。

### C. 没有屏幕录制时：一条路、一句话（点 2、3）

- 做法：屏幕录制未授予（辅助功能可授予也可不授予），打开应用，对 NanoLika 说「截图看看屏幕上有什么」，
  或直接调工具 `computer_screen`。
- 预期：
  1. 聊天里工具失败，错误文案含 *macOS: switch on NanoLika Desktop under System Settings →
     Privacy & Security → Screen Recording, then quit and reopen the app.*，**不是**一张黑图，也**不是**
     mss / screencapture 的报错；
  2. 系统**没有**为 `nanomuse`（运行时进程）弹第二次屏幕录制提示，「屏幕录制」面板里也没有多出
     Python / nanomuse 这样的条目；
  3. `desktop.log` 有 `operator server: POST /screenshot: 403 …` 一行（操作器拒绝截图）；
  4. 运行时侧：`nanomuse mcp` 的 `[hands]` 状态 `available:false`，`reason` 就是那句文案
     （`describe_availability` → `pick_backend` 在 darwin + 操作器变量存在时不再尝试 pyautogui）。
- 直接验证操作器（token 取法见 0.1.36 任务单 F 节）：`POST /screenshot` 应返回 `403 {"error": "no screenshot: …Screen Recording…"}`。
  想分辨是哪一条分支触发的：日志里 `desktopCapturer failed:` 表示 getSources 被拒；没有这行而返回 403，
  表示 libnut 抓到的是全黑（`isBlackImage`）。两种都算通过；记录是哪一种，以及 macOS 版本。
- **误报检查**：授权之后在一个几乎全黑的屏幕上截图（深色壁纸、无窗口、`sudo defaults` 不用，调低亮度不算）
  ——应当**正常返回图片**，不该报 403；采样阈值是每通道 < 8，纯黑壁纸 + 深色窗口边框一般有像素 ≥ 8。若误报，
  把一张触发误报的图（缩小后）贴进报告并提高/改良 `BLACK_LEVEL` 的判定。
- 代码：`harness/desktop/src/operator.ts`（`capture`、`isBlackImage`、`SCREEN_PERMISSION_TEXT`）、
  `operator-server.ts`、`nanomuse/computer/link.py`、`hands.py`、`operator.py`、测试
  `tests/test_computer_operator.py`（`test_mac_*`）。

### D. 授予后的重启（点 4）

- 做法：应用运行中，去「屏幕录制」面板打开 NanoLika Desktop。
- 预期：弹一次「屏幕录制已允许，重新启动后生效 / Screen Recording is on; it takes effect after a
  restart」，按「立即重启」：
  1. `desktop.log` 里先有 `host exited: code=…`（旧 host 被 `before-quit` 停掉），然后新进程的
     `NanoLika Desktop 0.1.37 starting`；
  2. 重启后 `pgrep -fl "nanomuse mcp"` 只有**一个**（新的）；重启前记下旧的 PID，确认它没了；
  3. 重启后截图正常，`permissions: … screen=granted (native)`。
- 对话框只弹一次，「稍后」之后不再弹。
- 代码：`main.ts` 搜 `relaunchNow`、`watchScreenGrant`、`before-quit`。

### E. Chromium 开关（点 5）

- `ps -o command= -p $(pgrep -f "NanoLika.app/Contents/MacOS" | head -1)` 的参数里**不会**显示
  `appendSwitch` 加的开关（它作用于内部命令行）；用 `--remote-debugging-port=9333` 启动后在 CDP 执行
  `Runtime.evaluate` 不行（是主进程），改为在 `desktop.log` 没有现成记录时，临时在 `main.ts` 的
  `whenReady` 后加一行 `log(app.commandLine.getSwitchValue("disable-features"))` 验证值等于
  `ThumbnailCapturerMac:capture_mode/sc_screenshot_manager,ScreenCaptureKitPickerScreen,ScreenCaptureKitStreamPickerSonoma`，
  验证完撤掉。
- 行为上：已授权时连续 20 次 `POST /screenshot`（320×180 PNG）每次都应返回 200 且图片非空、非全黑；
  记录耗时中位数。macOS 14/15 上若仍有偶发空图，记录频率。
- 代码：`main.ts` 顶部 `if (process.platform === "darwin") app.commandLine.appendSwitch(...)`。

### F. 签名脚本（点 6）

- ad-hoc：`codesign --verify --deep --strict dist/mac-arm64/nanoMuse.app` 应 `signature ok`；
  `codesign -dv --verbose=2 ".../app.asar.unpacked/node_modules/@computer-use/node-mac-permissions/build/Release/permissions.node"`
  应显示 ad-hoc 的新签名（0.1.37 起内层循环是 `for inner in runtime dsh app.asar.unpacked`）；
  若仍是 npm 发布时的签名或无签名，说明 `find … -name '*.node'` 那一遍漏了它，修脚本。
- ad-hoc 构建不走公证（entitlements 有 `disable-library-validation`）。有 Developer ID 证书的环境
  （CI 的 `MAC_CERT_P12_BASE64` 等 secrets）做一次完整的 `package-mac.sh`（公证 + staple），确认三个
  `.node` 都是 Developer ID 签名，把 `xcrun notarytool log` 的结论贴进报告。
- 代码：`scripts/desktop-app/package-mac.sh`（`== signature` 一段）。

### G. 顺手检查

- 启动页、Dock 图标、菜单栏图标正常；`desktop.log` 没有刷屏的错误。
- 0.1.36 任务单的 D（胶囊焦点）、E（窗口模式）、F.3–F.8（坐标、滚动、热键、内容保护）若时间允许一并做。

## 4. 修复与提交

- 直接在本分支修。外壳 `harness/desktop/src/`，运行时 `nanomuse/`。不要改版本号。
- 跑检查：`cd harness/desktop && npm run typecheck && node scripts/smoke.mjs --app dist/mac-arm64 --operator`；
  动了 Python 就 `.venv/bin/ruff check nanomuse tests && .venv/bin/mypy nanomuse && .venv/bin/pytest tests -q`。
- `CHANGELOG.md` 的 `## [Unreleased]` 下按平台小节（Desktop / Runtime）写一句话，过去式。
- 提交：`git add -A && git commit -s -m "fix(desktop): <一句话>"`；推送 `git push -u origin mac-check-0.1.37`；
  `gh pr create --base main --title "Mac check 0.1.37: <摘要>" --body-file <报告文件>`。不要把 `~/.nanomuse`、
  密钥、带 token 的日志放进提交。

## 5. 报告格式

每一项 A–G：**通过 / 失败 / 无法测试**，一两句证据（看到了什么、日志行、`codesign -dv` 的输出）；
失败项的根因、修复的提交号、还剩什么没解决。最后列出环境（macOS 版本、芯片、显示器与缩放比例、
Node/npm/Python 版本，`npm ci` 是否编译了 `screencapturepermissions.node`）。
