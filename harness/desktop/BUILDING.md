# Desktop rebuild checklist

Read this before rebuilding the desktop installers. Use the source checkout and Node 22.
The supported CI recipe is `.github/workflows/desktop-app.yml`. Do not package a previously
staged `dsh/`, `node/` or `runtime/` and assume it contains current source changes.

## Validate the source

1. Record the commit and working-tree changes. Exclude unrelated changes from other sessions.
2. Check that the plugin and Electron shell versions match. Version changes belong to the
   coordinated release commit, see `CONTRIBUTING.md`. Never replace published installers or
   update metadata with different bytes under an existing release version.
3. In `harness/dsh-nanomuse`, run `pnpm build`, `pnpm typecheck`, and `pnpm test`.
   Run the relay tests when the account protocol changes. Desktop Google/Yandex sign-in
   requires the relay's `desktop_oauth.py` and matching `oauth.py` to be deployed first.
4. In `harness/desktop`, run `npm run typecheck` and `npm test`.
   Do not skip errors or publish a package after a failed check.

## Build candidates

Use GitHub Actions, **desktop app**, **Run workflow**, the reviewed branch, with
`release_tag` empty for an unpublished candidate. A normal branch push builds Linux only;
manual dispatch builds Windows x64, macOS Apple Silicon and macOS Intel as well.
Verify both `macos-arm64` and `macos-x64` jobs actually started and report their run URL.
A queued build is not a finished installer. Download artifacts only after the jobs succeed.

The workflow stages the harness and freshly built plugin, builds and checks the Python
runtime, prepares the platform's Node and native helpers, packages and smoke-tests the app.
macOS uses `npm run dist:dir` followed by `scripts/desktop-app/package-mac.sh arm64` or
`x64` on the matching native runner. Do not cross-package the other architecture using
native modules from the current machine. Apple signing/notarization must finish before
calling the public macOS build ready. Follow the workflow rather than inventing a shorter
local packaging command that omits these steps.

## Test install and update

- Test a clean first launch and an upgrade over the version users actually downloaded.
  Existing account, chats and settings must survive. Test an open window, tray/background
  processes and an app that has already exited. Confirm launch after installation.
- Windows: keep `perMachine: true`, the installer process shutdown hooks, differential
  packages and the `.exe.blockmap`/`latest.yml` checks. Run the NSIS profile fixture on
  Windows; `--updated` must preserve credentials, an intentional uninstall removes them.
  A newly fixed uninstaller does not fix the uninstaller already shipped in an older
  version. The first upgrade from that version is a separate required acceptance test.
- If Windows cannot close a process but none exists, inspect the actual installer log,
  elevation and file integrity label. Run the downloaded candidate on a real Windows
  machine. Do not disable antivirus, UAC or browser download protection globally.
- Google and Yandex: each opens the system browser, returns to the app, and signs in to
  the expected account. Also test cancellation, retry, timeout, email sign-in and logout.
  Tests use a fake provider; a real account flow must be checked before public release.
- macOS: test each architecture, Gatekeeper, first launch, permissions helper and upgrade.

Only publish a new release after these checks, with coordinated versions and update-feed
verification. Keep the previous release available for rollback. Record artifact checksums,
commit, CI run, signing status and any unverified acceptance checks in the worklog.

## Для других сессий

Сначала прочитайте этот файл и актуальную запись `docs/WORKLOG.md`. Сборка приложения,
сборка установщика и публикация обновления являются отдельными действиями. Если владелец
попросил пока закончить авторизацию, установщики не запускать. Не использовать старые
подготовленные папки вместо исходников и не выпускать изменённый установщик под уже
опубликованной версией. Перед выпуском обязательно проверить обновление поверх версии,
которая сейчас стоит у пользователей, с сохранением входа и переписки.
