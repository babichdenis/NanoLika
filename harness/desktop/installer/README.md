# Windows setup tips

This decorates the existing assisted NSIS installer. It does not perform installation,
make network requests, read accounts or change permissions. The progress bar and errors
remain native NSIS controls. Eight tips, with existing Lika illustrations, rotate every
12 seconds. Pause is available; Windows' disabled animation preference starts it paused.
Minimizing the installer pauses rotation. Leaving the page closes the panel.

`slides.json` owns the English, Chinese and Russian copy. The four PNGs under
`resources/installer-slides/` are uncropped format conversions of the existing bundle
illustrations. No new avatar or asset identity is introduced.

`node scripts/prepare-installer.mjs` compiles SetupTips.cs with Windows' installed
.NET Framework compiler. No runtime or installer plugin is downloaded. The generated EXE
is ignored by Git and excluded from the installed app; NSIS embeds it as a temporary
installation resource. It runs as the invoking process and exits if there is no valid
NSIS progress page. A failed optional panel must not stop installation.

On Windows, run `node --test tests/installer-tips.test.mjs` from the desktop directory.
The shared GDI renderer checks every real tip at two widths, three DPI scales and all
three languages without showing or operating the user's desktop. With the cached NSIS
compiler it also links the actual welcome, progress and finish pages in a compile-only
fixture. `tests/windows-installer-profile.test.mjs` checks account preservation separately.
Before release, inspect the actual installer at normal and enlarged DPI, test Pause,
minimize/restore, cancellation, error visibility, completion and the launch checkbox.
The finish checkbox is checked by default; the standard-user launch uses StdUtils.

The owner asked to hold installer rebuilds on 2026-11-05 and collect other fixes first.
This UI is prepared in source only. The next explicit build request lifts that hold;
use the coordinated release recipe in ../BUILDING.md, never replace published 1.0.9 bits.

## 中文

此模块只装饰现有 NSIS 安装界面，不执行安装、不联网、不读取账户，也不更改权限。
八条提示配合现有 Lika 插画，每 12 秒切换。可暂停；系统关闭动画时默认暂停，
最小化时停止切换，离开安装进度页后退出。真正的进度和错误仍由 NSIS 显示。

英文、中文、俄文文案在 slides.json。四张 PNG 是现有插画的未裁切格式转换。
Windows 使用系统已有的 .NET Framework 编译器生成临时 EXE，不下载依赖。
生成文件不提交 Git，也不安装到应用目录。界面模块失败不应中断安装。

测试覆盖两种宽度、三种 DPI 和三种语言的实际渲染，以及实际 MUI 页面编译。
发布前仍须检查真实安装流程中的暂停、最小化、取消、错误和完成页启动选项。
启动复选框默认选中，通过 StdUtils 以普通桌面用户身份运行应用。

所有者在 2026-11-05 要求暂缓重建安装包，待收集其他问题后再统一构建。
当前仅准备源码；收到下一次明确构建指令后按 ../BUILDING.md 发布新版本，
不要用不同字节覆盖已经发布的 1.0.9。
