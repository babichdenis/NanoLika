# Mac 侧协作任务 — NanoLika 0.1.39（Mac 上的 Computer use、iOS 闪退、iOS 账号隔离）

这份文档是给在维护者（lgy0404）的 Mac 上运行的 Cursor 代理看的。把它整个粘贴给代理，它就有了
全部上下文。维护者在 Linux 上开发，Linux 上另有一个代理（下文叫 **L**）同时在做这一版的其他事情；
你（下文叫 **M**）负责 **macOS 和 iOS** 这两块只能在 Mac 上验证的工作。你们是协作关系：各管各的
文件，各开各的分支和 PR，通过 PR 描述和 PR 评论交流，维护者在中间转达。

> 代理须知：不要问「要不要继续」，直接做。能自己查到的信息自己查（仓库里的文档很全）。
> 不可逆操作（删用户数据、改系统设置、推到 main、发布）不要做；修复都提交到自己的分支并开 PR。
> 维护者不在聊天里给你密码；两步验证、Apple 协议勾选这类事由维护者亲自做——需要时直接说
> 「请你在 Xcode 里登录一下 Apple ID」这种一句话，然后继续做不依赖它的事。

## 0. 项目与分工

NanoLika 是一个开源（GPL-3.0-or-later）的个人 agent：手机 app（Android 和 iOS，基于 OpenMinis
子树 `android/`）、桌面 app（`harness/dsh-nanomuse/` 是一个 DeepSeek Harness 插件，
`harness/desktop/` 是包着它的 Electron 壳，壳里带着 Python 运行时给「手」用）、Python 运行时
`nanomuse/`（agent 循环、Sentinel、工具、手机/电脑操作器）、Web 控制台 `web/`、中继 `cloud/`
（登录、模型、hub、会话同步）。先读 [AGENTS.md](../../AGENTS.md)（目录、命令、约定，130 行）和
[CONTRIBUTING.md](../../CONTRIBUTING.md)，再按任务读 [docs/desktop.md](../desktop.md)（尤其
*macOS permissions*、*macOS signing*）、[docs/gui.md](../gui.md)（手怎么看屏幕、怎么点）、
[docs/ios.md](../ios.md)、[docs/every-device.md](../every-device.md)（多端同步）。上一轮的 Mac
任务单 [mac-check-0.1.37.md](mac-check-0.1.37.md) 里的构建步骤和检查项依然有效，先看一遍。

当前状态：**v0.1.38「Loom」已于 2026-10-05 发布**（main 的 merge commit `70524d90`），Android/iOS
build 39，中继 0.20.0。下一版 0.1.39 由 L 统一发布；**你不改任何版本号**（`pyproject.toml`、
`harness/*/package.json`、Android `versionName/versionCode`、iOS `MARKETING_VERSION`、
`scripts/rebrand.py` 里的 `VERSION_*` 都不碰）。

分工：

| 归属 | 范围 |
|---|---|
| **M（你）** | `harness/desktop/mac/**`（权限助手 app）；`harness/desktop/src/mac-helper.ts`、`mac-permissions.ts`；`harness/desktop/src/operator.ts`、`main.ts`、`preload.ts` 里 **darwin 分支**；`harness/dsh-nanomuse/src/hands-check.ts` 和 `src/client/{HandsCheck.tsx,permissions.ts,Capsule.tsx}` 的 **macOS 文案/逻辑**；`scripts/desktop-app/package-mac.sh`；`.github/workflows/desktop-app.yml` 的 macOS step；`nanomuse/computer/mac_window.py`、`screen.py`/`operator.py`/`hands.py` 的 darwin 分支；**整个 `android/src/ios/**`**；`docs/desktop.md` 的两节 macOS、`docs/ios.md`。 |
| **L** | 其余一切：中继、运行时、Web、Android、Linux/Windows 桌面、文档站、技术报告、发布。L 这一轮同时在改：账号切换后的本地数据隔离（契约 C10，见第 4 节，Android/桌面/运行时由 L 做）、Linux 上手的点击失效问题（会动 `operator.ts`/`main.ts` 的 Linux 分支——所以你在这两个文件里的改动请只落在 darwin 分支、尽量小）、自带 key 的更多提供方（OpenAI 等，含能力分级）、去掉 Terminal 版发布、技术报告。 |
| 共用文件 | `CHANGELOG.md`（在 `## [Unreleased]` 下相应 `### Desktop` / `### iOS` 小节**末尾**追加你的条目，过去时、具体、面向用户）；`harness/dsh-nanomuse/src/client/locales.ts`（新 key 追加在 `en` 和 `zh` 两块的末尾）；`Localizable.xcstrings` 全归你。冲突由 L 在合并时解决。 |

仓库约定里和你最相关的几条（全文在 AGENTS.md）：

- **两棵树两套规矩。** `android/` 是上游 OpenMinis 子树，要保持可拉取：新代码放新文件
  （iOS 放 `android/src/ios/NanoMuse/*.swift`），改上游文件时每处带一行 `// nanoMuse:` 注释，
  一处一改；不要改 Kotlin 包名、Gradle namespace、沙盒路径、Xcode 工程结构；品牌替换只走
  `scripts/rebrand.py`（改脚本，不改结果）。
- **每个字符串进每个语言。** 用户可见的字符串先英文、再简体中文、再该文件已有的全部语言：
  `Localizable.xcstrings` 的全部 locale；`locales.ts` 的 `en` 和 `zh`。语气平实具体，不用感叹号、
  不用营销词；中文同样。不要照抄 Meta 的界面文案。
- **iOS 16 目标。** 用 `.nmOnChange(of:)`，不用双参数 `onChange`（iOS 17+）；类型名全局唯一；
  `NanoMuse/` 下的 Swift 文件在 device build 里 **零 warning**（CI 把 warning 当失败）。
- **文档随代码。** 改了人能看到、能做的事就同时改 `docs/` 对应页，并在 `CHANGELOG.md`
  *Unreleased* 下加一条。
- **提交** 用 Conventional Commits（`fix(desktop): …`、`fix(ios): …`）并带 DCO 签名
  （`git commit -s`，用你在这台 Mac 上的 git 身份）；分支名以 `mac/` 开头；PR 到 `main`；
  **不要自己合并**，L 看过 CI 后合并。不 force-push。
- **永远没有秘密。** API key、手机号、邮箱、token、证书、`.p8`、`config/config.toml`、
  `cloud/.env` 不进提交、不进 PR 描述、不贴进聊天；日志贴出来之前把这些打码。
  不在 `cloud.nanomuse.cn` 上注册测试账号——测试用维护者自己的账号（登录验证码由维护者输入）。

## 1. 环境

```bash
xcode-select --install 2>/dev/null || true          # 命令行工具；完整 Xcode 16 也要装好并打开过一次
brew install node@22 pnpm uv cliclick
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"    # Intel Mac 是 /usr/local/opt/node@22/bin
node -v                                              # v22.x（CI 用 Node 22）
git clone https://github.com/nano-muse/nanoMuse.git && cd nanoMuse
uv venv && uv pip install -e ".[dev,hands]" && export NANOMUSE_PY="$PWD/.venv/bin/nanomuse"
```

桌面 app 从源码构建（任务 A 用）：

```bash
cd harness/dsh-nanomuse && pnpm install --frozen-lockfile && pnpm build && pnpm typecheck && pnpm test
cd ../desktop && npm ci && node scripts/prepare-dsh.mjs && npm run typecheck && npm test
bash mac/computer-use/build.sh                       # → mac/computer-use/build/nanoMuse Computer Use.app（ARCHS=arm64 更快）
```

两种跑法：

- **开发态** `NANOMUSE_DESKTOP_HOME=/tmp/nm-mac/home npm start`：进程是 Electron.app，助手 app
  走 `mac/computer-use/build/` 下刚构建的那个（`NANOMUSE_COMPUTER_USE_APP=<路径>` 可指定）。
  一定设 `NANOMUSE_DESKTOP_HOME`，否则会动维护者已安装 app 的数据目录 `~/.nanomuse/desktop`。
- **打包态**（所有权限项都用它）：
  ```bash
  npm run dist:dir && ../../scripts/desktop-app/package-mac.sh arm64     # Intel 用 x64
  open dist/mac-arm64/nanoMuse.app                                        # 或 dist/mac/
  ```
  没有证书时是 ad-hoc 签名，Gatekeeper 会说「无法验证」，到系统设置 → 隐私与安全性里点
  *仍要打开*。已发布的 0.1.38 安装包在
  `https://github.com/nano-muse/nanoMuse/releases/download/v0.1.38/nanoMuse-Desktop-0.1.38-mac-arm64.dmg`
  （x64 同名 `-mac-x64.dmg`），**先用它复现维护者看到的现象，再用源码构建修**。

数据目录 `~/.nanomuse/desktop`（`NANOMUSE_DESKTOP_HOME` 可改）：`desktop.log` 是壳和 Host 的日志
（菜单里有 *Open log folder*），`profiles/nanomuse/cordis.patch.yml` 是账号写入的提供方配置，
`nanomuse/hands.json` 是手用的模型。从头开始：`mv ~/.nanomuse/desktop ~/.nanomuse/desktop.bak`。

权限相关的系统命令：`tccutil reset ScreenCapture io.github.nanomuse.desktop.computer-use`、
`tccutil reset Accessibility io.github.nanomuse.desktop.computer-use`（0.1.37 及之前的 bundle id 是
`io.github.nanomuse.desktop`，一并 reset）；`codesign -dv --verbose=2 <app>`；`spctl -a -vv <app>`；
`xattr -l <app>`；Console.app 里按 `syspolicyd`、`tccd`、`NanoLika` 过滤。

## 2. 任务 A — Mac 上的 Computer use（最高优先级）

### A.1 现状和维护者看到的现象

维护者的原话只有一句：「mac 的 CUA 还是不行」。没有更多细节，所以 **第一步是在这台 Mac 上用
已发布的 0.1.38 复现并说清楚到底哪一步坏了**：是权限面板里没有出现「NanoLika Computer Use」
这一行、授权了但截图是黑的、截图对了但点击落不到位置、点击落对了但 app 没反应、还是 Settings →
Computer use 页面本身就报错。把 `desktop.log` 里 `helper:`、`operator`、`hands`、`runtime:`
开头的行、系统设置两个面板的截图、`codesign`/`spctl`/`xattr` 的输出一起记下来。

### A.2 0.1.38 的实现（代码在哪）

手在电脑上的链路：dsh agent → 运行时的 MCP 工具（`nanomuse mcp`，stdio；
`nanomuse/bridge/mcp_server.py`）→ `computer_screen` / `computer_act` / `computer_task`
（`nanomuse/computer/`：`hands.py` 选后端，`operator.py` 是桌面 app 操作器的 HTTP 客户端，
`coords.py` 把模型看到的图片像素换算回屏幕坐标——坐标以**图片像素**为单位，见 gui.md）→ Electron
壳的操作器服务（`harness/desktop/src/operator.ts`，loopback HTTP，`NANOMUSE_OPERATOR_URL` /
`_TOKEN`；`GET /info`、`POST /screenshot`、`POST /execute`）。

0.1.38 在 macOS 上把截图和输入挪进了一个独立的小 app——**NanoLika Computer Use.app**
（学 Codex 的做法：macOS 把权限记在 LaunchServices 启动的「责任进程」名下，壳的子进程算壳自己，
所以运行时过去从不出现在权限面板里；通过 `open` 启动的 app bundle 对自己负责，面板里就有它自己的
一行）。实现：

- `harness/desktop/mac/computer-use/`：`build.sh`（纯 swiftc，无 Xcode 工程，universal，ad-hoc
  签名），`Info.plist`（bundle id `io.github.nanomuse.desktop.computer-use`，`LSUIElement`），
  `Sources/{main,HTTPServer,Service,Permissions,Screenshot,Input,Keys}.swift`：Network.framework 的
  loopback HTTP，Bearer token；`GET /status`（两项权限的状态、主显示器尺寸和缩放）、
  `POST /request`（触发系统授权提示）、`POST /screenshot`（`CGDisplayCreateImage`，PNG/JPEG，
  `max_pixels` 缩放）、`POST /execute`（`CGEvent` 鼠标键盘；Unicode 文本用
  `keyboardSetUnicodeString`）、`POST /quit`；参数 `--token-file --port-file --parent-pid`，
  父进程退出它也退出。**还没做**：`press`/`release`（按住不放）这两个动作返回 "unknown action"；
  macOS 15 上 `CGDisplayCreateImage` 已 deprecated（ScreenCaptureKit 没接）。
- `harness/desktop/src/mac-helper.ts`：找 bundle（`NANOMUSE_COMPUTER_USE_APP` → 打包后的
  `Contents/Helpers/` → 开发态 `mac/computer-use/build/`），`open -n -g -a` 启动，token 文件 0600，
  `status()` 2 秒缓存，失败后 30 秒退避，`restart()`。
- `harness/desktop/src/operator.ts`：`OperatorOptions.helper`，`runWithHelper()`——助手可用时截图和
  动作走助手，否则回退到进程内的 `@computer-use/nut-js` 路径（那条路的权限记在 Electron/壳名下，
  也就是 0.1.37 的老问题）；动作名映射 `HELPER_ALIASES = { mouse_move: "move", left_single: "click",
  select: "drag" }`；`OperatorInfo.helper` 告诉运行时现在走的是哪条路。
- `harness/desktop/src/mac-permissions.ts`：`useHelper()` / `helperInUse()`；`main.ts` darwin 分支：
  `helperReady()`、`relaunchNow()`（重启时也重启助手）、`watchScreenGrant()`。
- dsh 插件侧：`src/hands-check.ts`（Settings → Computer use 页面的数据）、`src/client/HandsCheck.tsx`、
  `permissions.ts`、`Capsule.tsx`（`helper` 标志决定文案）、`locales.ts` 的 `pmOnlyHelper`、
  `pmTryShotBlackHelper`、`pmBlackBodyHelper`、`pmBlackRelaunchHelper` 等 key。
- 打包：`electron-builder.yml` `mac.extraFiles` 把 `build/nanoMuse Computer Use.app` 放到
  `NanoLika.app/Contents/Helpers/`；`scripts/desktop-app/package-mac.sh` 先签助手再签外层
  （没有证书时都是 ad-hoc）；`.github/workflows/desktop-app.yml` 的 macOS job 跑 `build.sh`
  （CI 上 0 warning 通过）。
- 文档：`docs/desktop.md` → *macOS permissions*、*macOS signing*。

这一切**在真机上一次都没有验证过**（维护者没有 Mac，CI 只能编译）。L 的上一轮报告里列的疑点，
按可能性排序：

1. **Gatekeeper 挡住了嵌套的助手。** 从 dmg 装的 app 带 `com.apple.quarantine`；外层 app 维护者
   手动「仍要打开」过，但 `open -a` 启动的助手是另一个 bundle，ad-hoc 签名 + quarantine 很可能被
   `syspolicyd` 静默拒绝，于是 `mac-helper.ts` 拿不到 `/status`，`operator.ts` 回退到进程内路径——
   表现就是「和以前一样不行」。验证：`xattr -l ".../Contents/Helpers/nanoMuse Computer Use.app"`、
   `spctl -a -vv` 它、Console 里 `syspolicyd` 的拒绝记录、`desktop.log` 里 `helper:` 的报错。
   可能的修法：首启时对自己 bundle 内的助手 `xattr -d com.apple.quarantine`（bundle 在用户可写的
   `/Applications` 里时可行）；或者不走 `open` 而用 `posix_spawn` + 让 TCC 把责任算给助手的别的办法；
   或者两条路都做并记录各自的权限归属结果。拿到 Developer ID 证书之前这是现实约束，文档要如实写。
2. **TCC 归属。** 面板里出现的到底是「NanoLika Computer Use」、「NanoLika」还是「Electron」？
   授权后 app 重启、助手重启，授权是否还在？ad-hoc 签名每次构建 cdhash 变，TCC 会忘记——开发态
   每次重编都要重新授权，这是已知的，写进文档即可；打包态同一个包内必须稳定。
3. **截图。** 授权前是黑帧还是报错（0.1.38 的 UI 文案按「黑帧 → 一句话指引」设计，见
   `pmTryShotBlackHelper`）；授权后 `CGDisplayCreateImage` 在 macOS 14/15 上是否正常；Retina 下
   `/status` 报告的尺寸、缩放和截图像素是否一致；多显示器只取主屏是否符合预期。
4. **输入。** `CGEvent` 需要 Accessibility 授予**助手**；检查一次点击的坐标：模型看到的图片像素 →
   `coords.py` → 操作器屏幕坐标（macOS 上是 point，缩放因子 1）→ 助手里的 `CGPoint`。
   用 `cliclick p` 或系统的指针位置对一下。中文输入、快捷键（`cmd+c`、`cmd+space`）、滚动的方向和
   单位也各试一次。
5. **生命周期。** `--parent-pid` 退出联动；`open -g` 是否仍抢焦点、Dock 是否闪图标；
   Settings → Computer use 的「重启」按钮是否连助手一起重启；助手崩了之后 30 秒退避期内的 UI 提示。
6. **0.1.37 任务单里没做完的项**（`mac-check-0.1.37.md` 的 B–F）凡是还成立的一并过一遍。

### A.3 验收

在一台重置过 TCC 的 Mac 上，用打包态的 app：

1. Settings → Computer use 把人引导到系统设置里**正确的那一行**（NanoLika Computer Use）去开
   屏幕录制和辅助功能，一次说清楚，不多弹、不乱弹；
2. 授权（及必要的重启）后，页面里的测试截图是真实屏幕；
3. 一个真实任务跑通，例如「打开计算器，算 12×34，告诉我结果」或「打开系统设置，把音量调低」：
   舞台（stage）里能看到屏幕，审批卡能点，至少三次点击 + 一次输入都落到了位置上；
4. 退出再打开 app，授权还在，任务仍能跑；
5. 开发态（`npm start`）至少能完成第 2 条，其余差异写进文档；
6. `docs/desktop.md` 的 *macOS permissions* 改成**真实的**行为（哪里不符合预期就改哪里，不要把
   没验证的话留在文档里）；`CHANGELOG.md` *Unreleased* → `### Desktop` 加条目；
7. `cd harness/desktop && npm run typecheck && npm test` 和
   `cd harness/dsh-nanomuse && pnpm typecheck && pnpm test` 通过；`bash mac/computer-use/build.sh`
   0 warning；push 到 `mac/*` 分支会自动触发 **desktop app** workflow 的 macOS job，看它绿。

分支 `mac/cua-0139`，PR 标题形如 `fix(desktop): make the hands work on macOS through the permission helper`，
PR 描述就是你的报告（格式见第 5 节）。先修最影响的那一处就开 PR，别等全部做完；后续改动继续推到
同一分支。

## 3. 任务 B — iOS 闪退（和任务 A 并行，先花半小时拿到崩溃日志）

### B.1 现象

维护者：「iOS 手机版出现闪退的情况，尤其是在用户走完安装引导之后再进软件根本进不去，进去就自动
闪退。」也就是：装好 → 走完 onboarding（选名字、登录等）→ 杀掉再进 → 启动即崩，每次都崩。
TestFlight 上 build 8 = 0.1.37，build 9 = 0.1.38（2026-10-05 上传）。先问维护者是哪个 build、
哪台机型、iOS 几；问不到就两个都测。

### B.2 拿到崩溃日志

- 手机：设置 → 隐私与安全性 → 分析与改进 → 分析数据 → `Minis-2026-10-…ips`，隔空投送到 Mac；
  或者把手机连上 Mac，Xcode → Window → Devices and Simulators → View Device Logs。
- 符号化：Xcode Organizer 里的 TestFlight 构建的 dSYM 需要维护者的 Apple ID 登录 Xcode；拿不到就
  本地 Debug 构建复现——`android/src/ios/Minis.xcodeproj`，scheme `Minis`，模拟器或真机都行，
  Xcode 会直接停在崩溃处。**复现脚本：删 app → 装 → 走完引导 → 从 app 切换器划掉 → 再打开。**
- 判断是不是 0.1.38 新引入：0.1.37 的同样步骤崩不崩。

### B.3 嫌疑（按可能性）

0.1.38 在 iOS 上新加/改的东西都在 `android/src/ios/NanoMuse/`（上游文件里的改动都带
`// NanoLika:`）：

1. **首次拉取写入的数据让启动时的加载崩了。** 0.1.38 的同步契约 C9：登录后第一次拉
   `GET /v1/sync/changes?since=0&scope=…&tail=300`，把最新 300 条消息及其会话落到本地
   （`NanoMuseSync.swift`、`NanoMuseChatGlue.swift`，以及 `Agent/Chat/AIChatViewModel+Persistence.swift`
   ~880 行 `nmRemote` 处）。「走完引导之后每次进都崩」最像持久化数据把启动路径弄崩：解码时
   non-optional 字段缺失、空数组下标、远端会话引用了本地不存在的东西、主线程外改 `@Published`。
   看崩溃栈里有没有 `NanoMuseSync`、`LocalChats`、`AIChatViewModel`。
2. **输入框的 fail-safe。** `NanoMuseComposerField.swift`（`TextField(axis: .vertical)`）、
   `NanoMuseComposerWatch.swift`（`failSafe`、`expect(_:)`），`Views/Chat/AIChatView.swift`
   ~576–589 行按 `failSafe` 在 `.safeAreaInset(edge: .bottom)` 和 `.overlay` 之间切换——
   视图更新中改状态、`@FocusState` 桥接都可能出问题。
3. **在线状态/「某设备正在处理」。** `NanoMusePresence.swift`（过期定时器）、`NanoMuseHub.swift`
   的 `working` 帧、`ChatModels.swift:86` 加的字段（如果模型是 Codable，看解码路径）。
4. **启动画面。** `NanoMuseLaunch.storyboard` + `NanoMuseLaunch.xcassets`，
   `INFOPLIST_KEY_UILaunchStoryboardName = NanoMuseLaunch`（由 `scripts/rebrand.py` 的规则写入
   pbxproj）。NIB 加载失败会在任何 UI 之前崩（崩溃原因里有 `Could not load NIB`），但第一次启动能
   看到引导页就基本排除它。
5. **中继地址选择器**（`NanoMuseRelayPicker.swift`、`NanoMuseCloud.swift` 的 `normalizedRelay` /
   `isPrivateHost` / UserDefaults 读取）。
6. 上游 OpenMinis 自己的引导 → 主界面切换（0.1.37 已发布，可能性低，除非 0.1.37 也崩）。

### B.4 验收

根因写清楚（栈、哪一行、为什么）；修复在 `mac/ios-crash-0139`，新文件放 `NanoMuse/`，上游文件
的改动带 `// NanoLika:`；能加回归测试就加到 `MinisTests/`（0.1.38 加了
`NanoMuseRound5Tests.swift`，照它的样子）；复现脚本在真机或模拟器上走三遍不崩；
`xcodebuild build -project Minis.xcodeproj -scheme Minis -configuration Debug -destination
'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO` 零 warning（`NanoMuse/` 下）；`CHANGELOG.md`
*Unreleased* → `### iOS` 加条目；`.venv/bin/python scripts/rebrand.py` 跑完输出 `clean`。
CI 的 **iOS · build check** 只能手动触发：`gh workflow run "iOS · build check" --repo nano-muse/nanoMuse --ref mac/ios-crash-0139`。
TestFlight 的上传由 L 在发布时做，你不用管。

## 4. 任务 C — iOS：换账号后不再加载别人的本地数据（契约 C10）

维护者的原话：「新登录账号会从本地加载出很多历史的数据/记录，可能是之前其他账号用过的，因为之前
设备上可能登录过其他的账号，这些数据不应该加载进来。」这是四端都有的问题，规则由 L 定、四端一致；
Android、桌面、运行时由 L 做，**iOS 由你做**。规则：

1. **每个本地会话记住它属于哪个账号。** 账号用 `GET /v1/me` 返回的 `account.id`（不透明 id，不是
   手机号/邮箱）。会话第一次被推到或从某账号拉下来时，把该 `account.id` 写进同步映射
   （iOS 在 `NanoMuseSync.swift` 的映射存储里）。登录前创建、从未同步过的会话没有归属。
2. **只显示当前账号的会话。** 已登录账号 B 时，会话列表只显示归属为 B 或无归属的会话；账号 A 的
   会话**隐藏但不删除**（切回 A 时再出现）。未登录时本地有什么显示什么（设备是本人的；此时不会
   加载任何新东西）。
3. **只推送当前账号的会话。** 推送循环只处理归属为 B 或无归属的会话；无归属的会话在第一次推送时
   归属到 B（「登录前的第一段对话也进账号」是既有行为，保留）。**绝不**把 A 的会话推进
   B 的账号——这是隐私问题，比显示更重要。
4. **换账号即重置游标和缓存。** 登录的 `account.id` 和上次不同时：同步游标归零（重新 `tail=300` 拉）、
   远端会话缓存、在线/「正在处理」状态、`working` 缓存全部清掉；A 的映射保留以便切回。
5. **账号的档案跟账号走**（Muse 的名字和形象从中继拉 B 的；0.1.38 已如此），本地记忆文件这一轮不动，
   报告里注明。
6. 不加新界面、不加新字符串；行为写进 `docs/ios.md`（一两句）和 `CHANGELOG.md` → `### iOS`。
   `docs/every-device.md` 里的契约全文由 L 写。

验收：同一台机器上 A 登录 → 聊两句 → 退出 → B 登录：列表里没有 A 的会话，中继上 B 的
`GET /v1/sync/changes?since=0` 里没有 A 的内容（用 B 的 key 直接 curl 中继确认，key 不要贴出来）；
再切回 A，A 的会话回来。测试账号用维护者自己的两个账号（请维护者提供验证码），不要注册新账号。
分支 `mac/ios-accounts-0139`，可以等任务 B 的 PR 开了再做。

## 5. 协作协议

- **沟通渠道是 PR。** 每个任务一个分支一个 PR（`mac/cua-0139`、`mac/ios-crash-0139`、
  `mac/ios-accounts-0139`），PR 描述 = 报告；进展和问题写成 PR 评论（`gh pr comment N --body …`），
  L 用 `gh pr view N --comments` 看并回复。PR 之外要问维护者的事直接在聊天里说。
- **开 PR 前先 `git fetch origin && git rebase origin/main`**，跑你改到的区域的检查
  （AGENTS.md「Commands」一节），确认没有碰到 L 的文件；碰了就在 PR 描述里单列一节「动了分工外的
  文件」说明为什么。
- **报告格式**（写在 PR 描述里）：环境（macOS 版本、芯片、显示器和缩放、Node/Python/Xcode 版本）；
  每一检查项 **通过 / 失败 / 无法测试** + 一两句证据（看到了什么、日志行、命令输出）；失败项的根因、
  修复的提交号、还剩什么没解决、你建议下一步做什么；需要维护者亲自做的事单列（登录 Xcode、点协议、
  提供验证码之类）。日志和截图里的 key、手机号、邮箱、token 打码。
- 不做的事：改版本号；合并自己的 PR；碰 `scripts/release-*`、`cloud/deploy/`、中继的管理接口；
  改 Android；改 `android/` 的工程结构；提交 `config/config.toml`、`.p8`、证书。

## 6. 速查

| 事项 | 在哪 |
|---|---|
| 助手 app 源码 / 构建 | `harness/desktop/mac/computer-use/{build.sh,Info.plist,Sources/}`；bundle id `io.github.nanomuse.desktop.computer-use` |
| 壳里的 macOS 代码 | `harness/desktop/src/{mac-helper,mac-permissions,operator,main}.ts` |
| Settings → Computer use 页面 | `harness/dsh-nanomuse/src/hands-check.ts`、`src/client/{HandsCheck,Sections,Capsule}.tsx`、`src/client/permissions.ts`、`locales.ts`（`pm*`、`cu*` key） |
| 运行时这一侧 | `nanomuse/computer/{hands,operator,coords,link}.py`、`nanomuse/bridge/mcp_server.py` |
| 桌面 app 数据目录 / 日志 | `~/.nanomuse/desktop/desktop.log`（`NANOMUSE_DESKTOP_HOME` 可改） |
| 打包 | `cd harness/desktop && npm run dist:dir && ../../scripts/desktop-app/package-mac.sh arm64` |
| iOS 工程 | `android/src/ios/Minis.xcodeproj`，scheme `Minis`，我们的代码在 `NanoMuse/`，测试在 `MinisTests/`，字符串 `Localizable.xcstrings` |
| 上游改动标记 | `// NanoLika:`；品牌替换 `python scripts/rebrand.py`（必须输出 `clean`） |
| 文档 | `docs/desktop.md`（macOS 两节）、`docs/gui.md`、`docs/ios.md`、`docs/every-device.md`、`docs/hub.md`、`docs/cloud.md` |
| 已发布的安装包 | `https://github.com/nano-muse/nanoMuse/releases/tag/v0.1.38` |
