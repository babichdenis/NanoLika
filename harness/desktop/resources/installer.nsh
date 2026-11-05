!define NanoLikaInstallerResources "${__FILEDIR__}"
!include "${NanoLikaInstallerResources}\installer-pages.nsh"

; Keep the account through the first upgrade from installers whose old uninstaller
; deleted these two files even with --updated. The backup stays inside the user's
; profile until restored, so an interrupted installer can recover on the next run.
!macro nanoLikaBackupAccount PROFILE_ROOT
  ${If} ${FileExists} "${PROFILE_ROOT}\.nanolika\desktop\nanolika\cloud.json"
    CreateDirectory "${PROFILE_ROOT}\.nanolika\desktop\.upgrade-account"
    ClearErrors
    CopyFiles /SILENT "${PROFILE_ROOT}\.nanolika\desktop\nanolika\cloud.json" "${PROFILE_ROOT}\.nanolika\desktop\.upgrade-account"
    ${If} ${Errors}
      Abort "Account backup failed. Не удалось сохранить вход. 无法备份登录信息。"
    ${EndIf}
  ${EndIf}
  ${If} ${FileExists} "${PROFILE_ROOT}\.nanolika\desktop\.credentials.yaml"
    CreateDirectory "${PROFILE_ROOT}\.nanolika\desktop\.upgrade-account"
    ClearErrors
    CopyFiles /SILENT "${PROFILE_ROOT}\.nanolika\desktop\.credentials.yaml" "${PROFILE_ROOT}\.nanolika\desktop\.upgrade-account"
    ${If} ${Errors}
      Abort "Account backup failed. Не удалось сохранить вход. 无法备份登录信息。"
    ${EndIf}
  ${EndIf}
!macroend

!macro nanoLikaRestoreAccount PROFILE_ROOT
  ${If} ${FileExists} "${PROFILE_ROOT}\.nanolika\desktop\.upgrade-account\cloud.json"
    ${IfNot} ${FileExists} "${PROFILE_ROOT}\.nanolika\desktop\nanolika\cloud.json"
      CreateDirectory "${PROFILE_ROOT}\.nanolika\desktop\nanolika"
      CopyFiles /SILENT "${PROFILE_ROOT}\.nanolika\desktop\.upgrade-account\cloud.json" "${PROFILE_ROOT}\.nanolika\desktop\nanolika"
    ${EndIf}
    ${If} ${FileExists} "${PROFILE_ROOT}\.nanolika\desktop\nanolika\cloud.json"
      Delete "${PROFILE_ROOT}\.nanolika\desktop\.upgrade-account\cloud.json"
    ${EndIf}
  ${EndIf}
  ${If} ${FileExists} "${PROFILE_ROOT}\.nanolika\desktop\.upgrade-account\.credentials.yaml"
    ${IfNot} ${FileExists} "${PROFILE_ROOT}\.nanolika\desktop\.credentials.yaml"
      CopyFiles /SILENT "${PROFILE_ROOT}\.nanolika\desktop\.upgrade-account\.credentials.yaml" "${PROFILE_ROOT}\.nanolika\desktop"
    ${EndIf}
    ${If} ${FileExists} "${PROFILE_ROOT}\.nanolika\desktop\.credentials.yaml"
      Delete "${PROFILE_ROOT}\.nanolika\desktop\.upgrade-account\.credentials.yaml"
    ${EndIf}
  ${EndIf}
  RMDir "${PROFILE_ROOT}\.nanolika\desktop\.upgrade-account"
!macroend

!macro customInstall
  !insertmacro nanoLikaRestoreAccount "$PROFILE"
!macroend

; Установка поверх работающей копии: файлы нельзя менять под живым процессом, иначе
; приложение остаётся в нерабочем состоянии и его приходится «выковыривать» из процессов
; (23.10.2026). Перед распаковкой закрываем запущенную копию: сначала мягко, затем жёстко.
; `preInit` вставлен в .onInit ДО проверки electron-builder «приложение запущено»
; (`ALLOW_ONLY_ONE_INSTALLER_INSTANCE`). Раньше закрытие жило только в `customInit`, то есть
; после проверки: если приложение было открыто, установщик успевал показать «Не удалось
; закрыть NanoLika», хотя taskkill ниже сработал бы. 04.11.2026.
!macro preInit
  nsExec::Exec 'taskkill /IM NanoLika.exe /T'
  Sleep 1200
  nsExec::Exec 'taskkill /F /IM NanoLika.exe /T'
  Sleep 600
  !insertmacro nanoLikaBackupAccount "$PROFILE"
!macroend

!macro customInit
  nsExec::Exec 'taskkill /IM NanoLika.exe /T'
  Sleep 1200
  nsExec::Exec 'taskkill /F /IM NanoLika.exe /T'
  Sleep 600
!macroend

; Первая страница мастера: предупредить, что установка долгая, и показать Лику.
!macro customWelcomePage
  !define MUI_WELCOMEPAGE_TITLE "$(NanoLikaWelcomeTitle)"
  !define MUI_WELCOMEPAGE_TEXT "$(NanoLikaWelcomeText)"
  !insertmacro MUI_PAGE_WELCOME
!macroend

; A real uninstall signs out. electron-builder also runs the uninstaller with
; --updated when replacing a version, which must retain the session and own keys.
; Keep the root explicit so the Windows fixture can exercise this exact macro
; against an isolated profile instead of the person's account.
!macro nanoLikaRemoveAccountFiles PROFILE_ROOT
  ${ifNot} ${isUpdated}
    Delete "${PROFILE_ROOT}\.nanolika\desktop\nanolika\cloud.json"
    Delete "${PROFILE_ROOT}\.nanolika\desktop\.credentials.yaml"
  ${endIf}
!macroend

!macro customUnInstall
  !insertmacro nanoLikaRemoveAccountFiles "$PROFILE"
!macroend
