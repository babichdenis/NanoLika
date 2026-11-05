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
native modules from the current machine. Record whether the Mac app is Developer ID signed and notarized or ad-hoc signed.
Never describe an ad-hoc build as notarized; the existing ad-hoc distribution requires
the user to allow its first launch in macOS Privacy & Security. Follow the workflow rather than inventing a shorter
local packaging command that omits these steps.

## Test install and update

- Test a clean first launch and an upgrade over the version users actually downloaded.
  Existing account, chats and settings must survive. Test an open window, tray/background
  processes and an app that has already exited. Confirm launch after installation.
- Windows: keep `perMachine: true`, the installer process shutdown hooks, differential
  packages and the `.exe.blockmap`/`latest.yml` checks. Run the NSIS profile fixture on
  Windows; `--updated` must preserve credentials, an intentional uninstall removes them.
  A newly fixed uninstaller does not fix the uninstaller already shipped in an older
  version. The installer therefore backs up the two account files before replacement
  and restores missing files afterwards. The first upgrade from that version remains a
  separate required acceptance test; the NSIS fixture exercises this legacy deletion.
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

## Publish downloads to S3 and the website

Use `releases/desktop/<version>/<installer filename>` in the configured S3 bucket.
Upload verified CI artifacts with public-read access, an attachment filename, the correct
content type, an immutable cache header and SHA-256 metadata. Never expose account
attachments or change the whole bucket's policy just to publish installers. If an object
already exists, compare its hash and size; refuse to replace it with different bytes.
Check public, unauthenticated HEAD responses, lengths and hashes before changing links.
Publish both Mac ZIP and DMG files; the installed updater consumes ZIP, the website DMG.
Windows needs its EXE, EXE.blockmap and the exact SHA-512 values in latest.yml.

Keep older installers. Back up the existing Windows and Mac download feeds before
switching them to the new version, and copy their referenced files into the existing
mirror as well. Do not break relative URLs expected by already installed clients.
The website's download buttons should link directly to the public S3 objects, separately
for Windows x64, Mac arm64 and Mac x64. Back up download.html before replacing it, then
sync its source and publication copies. Remove obsolete copy claiming that this is a
mockup or that available builds are still pending. Verify the live buttons, file headers,
download targets and layout after publication. Keep file formats, build numbers, sizes
and checksums in release metadata and verification reports, not in the visible download
cards (owner preference, 2026-11-05). Record the build commit, CI URL, checksums,
public URLs and rollback paths in the worklog.
