# Mac 侧协作任务 — iOS：聊天输入框显示不出来（0.1.39 → 0.1.40）

> **状态（2026-10-06 下午）**：Mac 坏了，这一轮由 L 在 Linux 上做完——[PR #102](https://github.com/nano-muse/nanoMuse/pull/102)
> 把输入栏改成消息列表下面的一行（不再是 overlay，也没有第二个宿主），并加了「设置 → 外观 → 输入框检查」，
> 让设备自己给出第 4.2 节要的视图层级（红框 + 可复制的报告）。TestFlight build 11 从该分支构建，在
> iPad 上验收。下面的内容留作 Mac 恢复后的参考；第 7.1 节（Mac dmg 的权限助手）仍只能在 Mac 上做。

这份文档是给在维护者（lgy0404）的 Mac 上运行的 Cursor 代理（下文叫 **M**）看的，**自成一体**：
整个粘贴给一个全新的会话，它就有了全部上下文，不需要再读别的任务单。维护者在 Linux 上开发，
Linux 上的代理（**L**）负责其余一切并统一发布；你负责 **只能在 Mac + 真机上做的事**。这次主要是
一件——把 iOS 聊天界面底部的输入框修到真机上看得见、用得了；顺手还有两项验收（第 7 节）。

> 代理须知：不要问「要不要继续」，直接做。能自己查到的信息自己查（仓库里的文档很全）。不可逆
> 操作（删用户数据、改系统设置、推到 main、发布、改版本号）不要做；修复提交到自己的分支并开 PR，
> **不自己合并**。维护者不在聊天里给密码；两步验证、Apple ID 登录 Xcode、输登录验证码这类事由
> 维护者亲自做——需要时一句话说清楚（「请在 Xcode → Settings → Accounts 登录一下 Apple ID」），
> 然后继续做不依赖它的事。

## 0. 项目、现状、分工

**NanoLika** 是一个开源（GPL-3.0-or-later）的个人 agent：手机 app（Android 和 iOS，基于上游
OpenMinis 子树 `android/`）、桌面 app（`harness/dsh-nanomuse/` 是 DeepSeek Harness 插件，
`harness/desktop/` 是 Electron 壳，壳里带 Python 运行时给「手」用）、Python 运行时 `nanomuse/`、
Web 控制台 `web/`、中继 `cloud/`（登录、模型、hub、会话同步）。先读 [AGENTS.md](../../AGENTS.md)
（目录、命令、约定，130 行），再读 [docs/ios.md](../ios.md)（整页，尤其 *The composer (0.1.38)*
和 *Building on a Mac*）。

**现状（2026-10-06）**：

- `main` = `9740986d`。**v0.1.39「Keys」今天发布**：Android/iOS build 40、中继 0.21.0。
  TestFlight **build 10 = 0.1.39**，已处理完（内部测试员可装；公测链接等 Apple 审核）。
- 上一个 Mac 会话（也叫 M）在 0.1.39 里交付并被合并了三个 PR：#95 Mac 的 Computer Use 助手
  （dmg 的 quarantine 标记导致助手被 App Translocation 启动、权限留不住），#96 iOS 引导后闪退
  （`AIChatView.body` 修饰符链过长，Swift 运行时类型解码爆栈；修法是 `NanoMuseChatModifiers.swift`
  里的两个合并修饰符 + 把输入栏整体装进 `AnyView`），#97 iOS 账号隔离（契约 C10）。它们的分支已删。
  **#96 和 #97 当时都没能在真机上跑**（Mac 磁盘不够，没把 iOS 的原生依赖编出来；靠 CI 的编译检查
  合并的）。维护者用 build 10 测了：**不崩了**，但「**输入的消息框还是显示不出来**」。
- 下一版 **0.1.40 由 L 发布**；你**不改任何版本号**（`pyproject.toml`、`harness/*/package.json`、
  Android `versionName/versionCode`、iOS `MARKETING_VERSION`、`scripts/rebrand.py` 的 `VERSION_*`）。

**分工**：你负责 **整个 `android/src/ios/**`** 和 `docs/ios.md`，以及 `harness/desktop/mac/**`、
`harness/desktop/src/{mac-helper,mac-permissions}.ts`、`operator.ts`/`main.ts` 的 darwin 分支、
`docs/desktop.md` 的两节 macOS。其余一切是 L 的。共用文件：`CHANGELOG.md`（在 `## [Unreleased]`
下 `### iOS` / `### Desktop` 小节**末尾**追加你的条目，过去时、具体、面向用户；现在 Unreleased
下面是空的，小节标题你自己加）；`Localizable.xcstrings` 全归你。冲突由 L 在合并时解决。

**仓库约定里和你最相关的几条**（全文在 AGENTS.md）：

- `android/` 是上游子树，要保持可拉取：新代码放新文件（`android/src/ios/NanoMuse/*.swift`），
  改上游文件时每处带一行 `// NanoLika:` 注释，一处一改；不改 Xcode 工程结构、不改沙盒路径；
  品牌替换只走 `scripts/rebrand.py`（跑完必须输出 `clean`）。
- 用户可见的字符串先英文、再简体中文、再 `Localizable.xcstrings` 已有的全部 locale；语气平实，
  无感叹号、无营销词；不照抄 Meta 的界面文案。
- iOS 16 目标：用 `.nmOnChange(of:)`，不用双参数 `onChange`；类型名全局唯一；`NanoMuse/` 下在
  device build 里**零 warning**（CI 把 warning 当失败）。
- 文档随代码：改了人能看到的行为就同时改 `docs/ios.md`，并在 `CHANGELOG.md` *Unreleased* 加一条。
- 提交用 Conventional Commits（`fix(ios): …`）并带 DCO 签名（`git commit -s`，用这台 Mac 上的 git
  身份）；分支名以 `mac/` 开头；PR 到 `main`；不 force-push。
- 永远没有秘密：API key、手机号、邮箱、token、证书、`.p8`、`config/config.toml`、`cloud/.env` 不进
  提交、不进 PR 描述、不贴进聊天；日志贴出来之前打码。不在 `cloud.nanomuse.cn` 上注册测试账号——
  测试用维护者自己的账号（验证码由维护者输入）。

## 1. 环境（这台 Mac）

上一个会话留下的事实：MacBook Pro（Apple M4 Pro），**macOS 27.0.1**，内建屏 1728×1117 pt @2x；
**Xcode 27.0 (27A266a)**，iOS 27.0 SDK；Homebrew `node@22`（22.23.3）、pnpm、uv、Python 3.12；
**磁盘只剩约 5 GB**（`~/Library/Caches` 约 26 GB）。手边的测试设备是 **iPad（第 8 代，`iPad11,6`），
iPadOS 26.7.1**，上面装着 TestFlight build 10。是否有 iPhone，问维护者。

```bash
# 1) 找到或建立 clone（上一个会话大概率已经 clone 过；路径不详）
mdfind "kMDItemFSName == 'nanoMuse'" -onlyin ~ 2>/dev/null | head; ls -d ~/nanoMuse ~/code/nanoMuse ~/src/nanoMuse 2>/dev/null
# 有：cd <clone> && git fetch origin && git checkout main && git pull --ff-only && git submodule update --init
# 没有：git clone --recurse-submodules https://github.com/nano-muse/nanoMuse.git && cd nanoMuse
git log --oneline -1          # 应是 9740986d 或更新

# 2) 腾磁盘：原生依赖 + 一次 Debug 构建要十几 GB
du -sh ~/Library/Caches/* 2>/dev/null | sort -rh | head; du -sh ~/Library/Developer/Xcode/DerivedData
# 清 ~/Library/Caches 下明显的应用缓存、旧的 DerivedData、~/Library/Developer/Xcode/iOS DeviceSupport 里旧系统版本的符号可以；
# 删别的东西之前先列出来问维护者一句。

# 3) iOS 原生依赖（第一次约一小时；docs/ios.md「Building on a Mac」）
brew install ninja meson llvm lld libarchive pkg-config go libimobiledevice
cd android && ./deps/build_lame.sh && ./deps/build_ffmpeg.sh && ./deps/build_ish.sh && ./deps/prepare_alpine_rootfs.sh && ./deps/build_rclone_ios.sh
cp -n src/ios/Configs/ProviderCustomization.xcconfig.example src/ios/Configs/ProviderCustomization.xcconfig

# 4) 真机 Debug 构建（原生库只有 device 版，模拟器链接不上）
# Xcode 里打开 src/ios/Minis.xcodeproj，scheme Minis，Signing & Capabilities 选维护者的 team（工程里 DEVELOPMENT_TEAM 为空；
# 需要维护者在 Xcode 登录 Apple ID——免费 provisioning 也行），iPad 用 USB 连上并「信任」。
# 命令行编译检查（CI 同款，NanoMuse/ 下零 warning）：
cd src/ios && xcodebuild build -project Minis.xcodeproj -scheme Minis -configuration Debug -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO
```

依赖编译是长活：**先做第 4 节的 4.1（不需要任何构建）**，让依赖在后台编。

## 2. 问题

### 2.1 现象和已有的真机证据

维护者的话：「**输入的消息框还是显示不出来**」。两张截图（2026-10-06，768×1024，是那台 iPad）：

1. **Muse 壳开着、进主聊天**：有 Muse 头部（脸 + 名字胶囊）、有一条「来自 Xiaomi 手机」的灰色
   气泡（这是从账号同步过来的主对话）、有底栏；**底部没有输入胶囊**。像素量过：底栏顶边在
   970/1024（56 pt），最后一个气泡底边在 949——**只差 21 pt**。消息列表的底部内边距是
   `inputBarHeight + floatingBarHeight + 8`，所以要么 `inputBarHeight ≈ 0`（输入条从没报出过高度），
   要么输入条有高度但被画到看不见的地方（这两种情况在 Linux 上分不开，真机的视图层级一看便知）。
2. **设置 → 外观 → 关掉「Muse home」、回到上游 OpenMinis 布局**：上游的输入条**在**（占位文字、
   +、/、话筒、发送、iPad 的拖动把手都在）。但这张是**一个空的新会话**，不是第一张那个主对话——
   所以它只说明「上游路径能画出输入条」，还分不出是壳（host）、胶囊，还是那个同步来的会话的问题。

### 2.2 来历：同一个症状第四轮了，没有一轮是在真机上看着改的

| 版本 | 维护者的话 | 当时的判断与改动 |
|---|---|---|
| 0.1.36 | 输入框要像 Muse | 做了 Muse 的胶囊 `NanoMuse/NanoMuseComposer.swift`（`NanoMuseComposerPill`），字段仍是上游的 `PastableTextView`（UIViewRepresentable） |
| 0.1.37 | 「键盘经常丢掉，用户看不到键盘」 | 认为是上游已知的「composer host 不再布局、零子视图」（上游自己在 `AIChatView.swift` ~1395–1455 的 `[InputBarHealth]` 注释里写了真机上看到 `FloatingBarHostingView nkids=0`）。加了 `NanoMuseComposerWatch.swift`：探针报告栈是否在窗口里、多高，不对就 `rebuildTick` 重建 |
| 0.1.38 | 「输入框还是会掉」，是走完第一次对话的起名卡片之后 | 认为 UIKit host 是祸根：字段换成 SwiftUI 的 `TextField(axis: .vertical)`（`NanoMuseComposerField.swift`）；加 **fail-safe**：聊天出现 / 消息发出 / 一轮结束后一秒，若探针没附着、host 高度 0 或输入条从没报过高度，就把同一个栈改挂到 `.safeAreaInset(edge: .bottom)`。**build 9 一进聊天就崩（#96），这版的输入框从未在真机上被看见。** |
| 0.1.39 | （本任务）「还是显示不出来」 | #96 把 overlay 和 fail-safe 合成一个 `NanoMuseComposerHost`（`NanoMuseChatModifiers.swift`），栈装进 `AnyView`。行为上和 0.1.38 等价。 |

要点：**0.1.37 时输入框至少一开始是在的**（维护者能做完第一次对话和起名，之后才掉）。之后动过
这一块的只有两处——0.1.38 的字段替换 + fail-safe，#96 的 host 合并 + `AnyView`——**两处都没在真机上
被看见过**。现在是一进主聊天就没有，问题大概率在这两处之一，但也可能是壳的几何（第 3 节）。
L 在 Linux 上把壳和胶囊的代码从头读了一遍，没找到能让胶囊**确定性**消失的分支：`inputBar` 在 Release
里无条件构建，胶囊的加号和话筒各 40 pt（所以即使字段零尺寸，胶囊也该有 56 pt 高），iPad 专有的只有
`maxContentWidth = 900` 和拖动把手。**所以这一轮不猜，先看层级。**

## 3. 代码在哪

- 聊天界面 `Views/Chat/AIChatView.swift`（上游文件，6500 行，我们的改动都带 `// NanoLika:`）：
  - `body`（~498 行）：`ZStack { messagesArea.safeAreaInset(edge: .top){…}.modifier(NanoMuseComposerHost(failSafe:stack:)) …; kernelBootOverlay }`。
  - `nmComposerStack` / `nmComposerTree`（~3499 行）：`ZStack(alignment: .bottom) { VStack { NanoMuseChatCardsHost; floatingToolPreview; inputBar }.background(NanoMuseComposerProbe).id(rebuildTick); inputPopupOverlay }`。
  - `inputBar`（~3778 行）：`nmPill` 为真时走 `nmPillRows` → `NanoMuseComposerPill { nmPillField }`；外面 `ComposerSurface(pill:)` 是灰色胶囊；`.onGeometryChange` 给 `inputBarHeight`（消息列表的底部内边距）和 `nmComposer.geometry(height:)`。
  - `nmPill`（~3487 行）`= NanoMuseShellPrefs.shell`；`nmListInset` / `nmListFloating`（~3491 行）在 fail-safe 路径上为 0。
  - `onAppear`（~1198 行）`nmComposer.visible = true; expect("the chat appeared")`；`onDisappear`（~1319 行）。
  - `messagesArea`（~2629 行）：`CollectionViewMessageListV3`——消息列表是 **UIKit 的 UICollectionView**（UIViewRepresentable），输入栏是它的 SwiftUI overlay。上游 2026-08 看到的 `FloatingBarHostingView nkids=0` 就是这个 overlay 的宿主。
- 我们的文件 `NanoMuse/`：`NanoMuseComposer.swift`（胶囊）、`NanoMuseComposerField.swift`（字段）、
  `NanoMuseComposerWatch.swift`（看门狗 + fail-safe + 探针 `NanoMuseComposerProbeView`）、
  `NanoMuseChatModifiers.swift`（`NanoMuseComposerHost`：`content.overlay(alignment: .bottom){ if !failSafe { stack } }.safeAreaInset(edge: .bottom){ if failSafe { stack } }`；`NanoMuseChatHooks`）、
  `NanoMuseChatCards.swift`（起名卡片等虚拟卡片，在输入条上方同一个栈里）、`NanoMuseFirstRun.swift`。
- 壳 `NanoMuse/NanoMuseShell.swift`：`NanoMuseHomeView`（~333 行）= `ZStack { chatLayer … }.safeAreaInset(edge: .bottom) { NanoMuseBottomBar }`（底栏 56 pt，`.background(.bar)`；键盘弹出或语音面板打开时底栏隐藏，`NanoMuseKeyboardWatcher`）；`chatLayer`（~541 行）= `NavigationStack { AIChatView(...).id(id) }.safeAreaInset(edge: .top) { Muse 头部 }.toolbar(.hidden, for: .navigationBar)`。旁聊由 `navigationDestination` push 出来、带系统导航栏。
- 开关：设置 → 外观 → **Muse home**（`NanoMuseAppearance.swift`，`@AppStorage("nanomuse.shell.enabled")`）。壳和胶囊由**同一个开关**决定。
- 日志：我们的 `Logger(subsystem: "io.github.nanomuse.app", category: "nm.composer")`；上游的 `AppLogger(category: "InputBarLayout")` 同一个 subsystem。
- 文档：[docs/ios.md](../ios.md) *The composer (0.1.38)* 一段写的是 0.1.38 的假设，修完改成真相。
  `MinisTests/NanoMuseRound6Tests.swift` 守着 `AIChatView.body` 的类型深度和大小上限——**不要再往
  `AIChatView.body` 上加链接**，新行为进 `NanoMuseChatHooks` / `NanoMuseComposerHost` 或第三个 modifier。

## 4. 先拿证据

### 4.1 不用构建就能做的（15 分钟，趁依赖在编）

iPad USB 连 Mac，**用它上面已经装着的 TestFlight build 10**：

1. **Console.app** 选这台 iPad，Action 菜单勾上 *Include Info Messages* 和 *Include Debug Messages*，
   过滤 `subsystem:io.github.nanomuse.app`；或者终端 `idevicesyslog | grep -E "nm\.composer|InputBarLayout|InputBarHealth"`。
   然后在 iPad 上：杀掉 app → 打开 → 进主聊天 → 等 5 秒 → 切到 Feed 再切回 → 切后台再回来。要看的行：
   - `composer fail-safe: no composer one second after …（attached= hostH= frameH=）`、
     `composer fail-safe: still no composer …`、`composer self-heal: rebuilding …`——**fail-safe 触发了**
     说明探针认为栈不在/没高度（下面的 c 或 d）；
   - `inputBarHeight seeded=…`、`inputBarHeight settled=…`、`inputBarHeight discarded=… offscreen`——
     输入条**报过高度**（哪怕被丢弃）说明它布局出来了，只是看不见（a）；
   - 什么都没有而输入框又不在：看门狗认为一切正常——a 或 d。
2. **壳开着、主聊天里，点一下底栏上方那片空白**：键盘弹出来 → 输入框在、只是看不见（a）；没反应 → 没布局出来。
3. **抽屉 → 新聊天**（旁聊，push 出来的）：里面**有没有胶囊**？有 → 问题在主聊天那条路径（壳的头部 /
   底栏 inset / `.toolbar(.hidden)`）；没有 → 胶囊或 host 自己的事。
4. **关壳、打开同一个主对话**：设置 → 外观 → Muse home 关 → 在上游布局的会话列表里找到那个带 Xiaomi
   气泡的主对话打开。输入条在 → 不是会话的问题；不在 → 是这个同步来的对话本身（看 `NanoMuseSync`
   给它的消息加了什么）。
5. 让维护者（或你，如果有 iPhone）在 **iPhone** 上进主聊天截一张——两张图都是 iPad 的。

把这五条的结果先写进 PR 评论（PR 可以先开成 draft，描述里写「证据收集中」），L 这边会据此准备。

### 4.2 Debug 构建装上 iPad，看视图层级（关键一步）

依赖编好后，Xcode 用 **Debug** 配置把 `Minis` 装到 iPad（**不用删 app、不用重走引导**：装上、进主聊天、
看底部；复现应当是立刻的）。如果 Debug 构建上胶囊反而在，再按完整脚本从头走一遍：删 app → 装 →
走完引导（登录验证码由维护者输）→ 进主聊天，每步截图，记下**输入框第一次看不见是在哪一步**。

输入框看不见的那一刻，Xcode → Debug → **View Debugging → Capture View Hierarchy**。两个现成的锚点：
栈的背景里有一个真正的 UIKit 视图 **`NanoMuseComposerProbeView`**（按类名搜）；胶囊的字段是 SwiftUI
的 `TextField`，在 UIKit 层级里是 `UITextField` 的子类。四种情况只会是其一，修法完全不同：

| 看到的 | 意味着 | 修的方向 |
|---|---|---|
| **a.** 胶囊在层级里、尺寸正常，但 frame 在窗口之外或在底栏/键盘后面 | 安全区 / inset 几何问题（壳的 `safeAreaInset` 底栏、`AIChatView` 的 `.ignoresSafeArea(.keyboard, …)`、iPadOS 26 的键盘安全区变化） | 改壳或 host 的几何，不动看门狗 |
| **b.** 胶囊在层级里但高度 0 / 宽度 0 | `TextField(axis: .vertical)` 在这个 iOS 上在 `HStack(alignment: .bottom)` 里给了零尺寸，或 `lineLimit(1...6)` + `.frame(minHeight:)` 的组合不对 | 字段给固定最小高度 / 换 `TextEditor` / 回上游的 representable 只做显示 |
| **c.** 装胶囊的 hosting view 在，但**没有子视图**（上游 2026-08 的那种） | SwiftUI 把这段子树渲染成空：`NanoMuseComposerHost` 两条 `if` 都没渲染，或 overlay 的宿主没重建 | 看 fail-safe 为什么没救：`failSafe` 是否已为真而 `safeAreaInset` 路径也空；考虑把 `safeAreaInset` 做成唯一路径 |
| **d.** 胶囊压根不在层级里 | `inputBar` 没被构建：`nmPill` 分支、`vm` 状态、`.id(rebuildTick)` 正在重建中 | 顺着 `nmComposerTree` 的条件查 |

顺手记下：胶囊 hosting view 的 `alpha`、`isHidden`、`clipsToBounds`、frame；它上面有没有一层全屏透明
视图（`NanoMuseDrawer` 的 overlay、`kernelBootOverlay`、`SessionLockGateOverlay`）；
`NanoMuseComposerProbeView` 的 frame 和 `window.bounds` 的关系（`maxY` 超过窗口高度减 56 就是在底栏后面）。

Debug 构建还带着上游的调试服务（`Debug/DebugServer.swift`，端口 8321，仅 DEBUG）：`iproxy 8321 8321`
后 `curl http://127.0.0.1:8321/skill` 是协议说明和示例客户端，`/pair`（`"plain": true`，只接受
127.0.0.1）拿 token，`/rpc` 的 `debug.search {"keyword": "TextField"}`、`debug.viewTree`、
`debug.inspect {"address": …}` 能在不断点的情况下导出活的视图树——上游当年就是这么抓到 `nkids=0` 的。
Xcode 的 View Debugger 够用就不必折腾它。

### 4.3 第三个对照（只改本地，不提交）

**壳开、胶囊关**：把 `AIChatView.swift` 里 `private var nmPill: Bool` 临时改成 `false`，Debug 装上再进
主聊天。上游的输入条在壳里显示 → 问题在 `nmPillRows` / `NanoMuseComposerPill` / `NanoMuseComposerField`
（b）；还是不显示 → 问题在 host（`NanoMuseComposerHost` 的 overlay / inset）或壳（a / c / d）。

## 5. 修

证据到手再动手。几条硬约束：

- 新代码放 `NanoMuse/*.swift`；上游文件里每处改动一行 `// nanoMuse:`，一处一改；不加链接到
  `AIChatView.body`（`NanoMuseRound6Tests` 会挡）。
- 以**用户看得见**为准，不以探针为准：如果结论是 a 或 b，`NanoMuseComposerWatch` 的 fail-safe 根本
  不会触发，别指望它；被证明打偏的看门狗代码可以删，连 `docs/ios.md` 一起改成真相，不要留一段写错的
  历史在文档里。
- 如果结论是 c，把 `safeAreaInset` 做成唯一 host 是可选项：消息列表不再从胶囊下面穿过（Android 的
  ChatScreen 也不穿），`inputBarHeight` 那套内边距可以保留但 `nmListInset` 恒为 0。先在真机上确认这条
  路径本身能显示再决定。
- 新字符串先英文、再简体中文、再 `Localizable.xcstrings` 已有的全部 locale；语气平实，无感叹号。
- iOS 16 目标：`.nmOnChange(of:)`。`NanoMuse/` 下零 warning。`scripts/rebrand.py` 跑完输出 `clean`。
- 文档随代码：`docs/ios.md` 的 composer 一段改成真实根因；`CHANGELOG.md` `## [Unreleased]` 下加
  `### iOS` 小节和一条（过去时、具体、面向用户）。

## 6. 验收

在 iPad（和 iPhone，如果有）上，**Debug 和 Release 两种配置**各走一遍：进主聊天、输入、发送、模型回问
名字时起名卡片出现在输入条上方、选名字卡片消失、再发一条、切后台再回来、打开抽屉里的旁聊再返回主聊天、
切到 Feed 再切回 Chat、横竖屏、键盘弹出收起——输入框在每一步都在、能点出键盘、能发出去；三遍。能加回归
测试就加到 `MinisTests/`（照 `NanoMuseRound6Tests.swift` 的样子，真机跑）。

```bash
cd android/src/ios
xcodebuild build -project Minis.xcodeproj -scheme Minis -configuration Debug \
  -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO     # NanoMuse/ 下零 warning
cd ../../.. && .venv/bin/python scripts/rebrand.py                  # 没有 .venv 就 uv venv && uv pip install -e ".[dev]"
gh workflow run "iOS · build check" --repo nano-muse/nanoMuse --ref mac/ios-composer-0140
```

## 7. 顺手的两项验收（证据收集完、等构建的时候做；只报告，发现问题另开 `mac/*` 分支）

1. **Mac 桌面端 0.1.39 的权限助手**（#95 的真机验收，当时只在源码构建上验过）：下载
   `https://github.com/nano-muse/nanoMuse/releases/download/v0.1.39/nanoMuse-Desktop-0.1.39-mac-arm64.dmg`，
   拖进「应用程序」，先 `tccutil reset ScreenCapture io.github.nanomuse.desktop.computer-use` 和
   `tccutil reset Accessibility io.github.nanomuse.desktop.computer-use`，打开 app（Gatekeeper 说无法验证
   → 系统设置 → 隐私与安全性 → 仍要打开），Settings → Computer use 按它的指引走：两个面板里出现的是不是
   **nanoMuse Computer Use** 那一行、授权后测试截图是不是真实屏幕、`~/.nanomuse/desktop/desktop.log` 里
   `helper:` 行有没有 `clearQuarantine`/translocation 的报错、退出再打开授权还在不在、一个真实任务
   （「打开计算器，算 12×34」）跑不跑得通。数据目录 `~/.nanomuse/desktop` 是维护者正在用的：想从头来，
   先 `mv ~/.nanomuse/desktop ~/.nanomuse/desktop.bak`（告诉维护者，完了可以 mv 回去），然后用 Finder 或
   `open -a nanoMuse` 启动——不要从终端直接跑 `Contents/MacOS/nanoMuse`，那样权限会记在 Terminal 名下，
   测的就不是用户看到的流程了。
2. **iPad 上 build 10 的账号隔离**（#97）：A 登录 → 聊两句 → 退出 → B 登录：列表里没有 A 的会话；
   切回 A，A 的回来。需要维护者的两个账号和验证码；没有第二个账号就跳过并注明。

## 8. 交回

分支 `mac/ios-composer-0140`，PR 到 `main`，**不自己合并**。开 PR 前 `git fetch origin && git rebase origin/main`，
跑第 6 节的检查。PR 描述 = 报告：机型/iPadOS/Xcode 版本；4.1 五条的结果；4.2 的四选一和证据（层级截图、
frame 数值）；4.3 的结果；关键日志行（打码）；根因一段话（哪一行、为什么、为什么前三轮没修到）；修复的
提交号；验收表（通过 / 失败 / 没法测 + 一句证据）；第 7 节两项的结果；还剩什么、建议下一步。进展和卡点写
PR 评论（`gh pr comment N --body …`），L 用 `gh pr view N --comments` 看。需要维护者亲自做的事单列。

L 这边同时在做：拿到你的 PR 后合并、发 0.1.40、TestFlight build 11、把公测提审补上。

## 9. 速查

| 事项 | 在哪 |
|---|---|
| iOS 工程 | `android/src/ios/Minis.xcodeproj`，scheme `Minis`，我们的代码在 `NanoMuse/`，测试在 `MinisTests/`，字符串 `Localizable.xcstrings` |
| 原生依赖 | `android/deps/{build_lame,build_ffmpeg,build_ish,prepare_alpine_rootfs,build_rclone_ios}.sh`（[docs/ios.md](../ios.md)、[android/BUILDING.md](../../android/BUILDING.md)） |
| 输入框 | `NanoMuse/NanoMuseComposer*.swift`、`NanoMuse/NanoMuseChatModifiers.swift`、`Views/Chat/AIChatView.swift`（`inputBar`、`nmComposerTree`、`nmPill*`） |
| 壳 | `NanoMuse/NanoMuseShell.swift`（`NanoMuseHomeView`、`chatLayer`、`NanoMuseBottomBar`） |
| 日志 | Console.app，`subsystem:io.github.nanomuse.app`（`nm.composer`、`InputBarLayout`）；`idevicesyslog` |
| 调试服务（DEBUG） | `Debug/DebugServer.swift`，端口 8321，`iproxy 8321 8321` |
| 上游改动标记 | `// NanoLika:`；品牌替换 `python scripts/rebrand.py`（必须输出 `clean`） |
| Mac 桌面端 | `harness/desktop/mac/computer-use/`、`harness/desktop/src/{mac-helper,mac-permissions}.ts`；数据目录 `~/.nanomuse/desktop`（`desktop.log`） |
| 已发布的安装包 | `https://github.com/nano-muse/nanoMuse/releases/tag/v0.1.39`；TestFlight build 10 = 0.1.39 |
| 上一轮的任务单（背景） | [mac-work-0.1.39.md](mac-work-0.1.39.md)、[mac-check-0.1.37.md](mac-check-0.1.37.md) |
