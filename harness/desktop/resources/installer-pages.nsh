; The picture panel is optional, offline and independent of NSIS's install thread.
; Keep all actual file work, progress, errors and account preservation in NSIS.
!define MUI_BGCOLOR "F7F1E6"
!define MUI_TEXTCOLOR "2B2622"
!define MUI_WELCOMEFINISHPAGE_BGCOLOR "F7F1E6"

!macro nanoLikaInstallerStrings
  LangString NanoLikaWelcomeTitle 1033 "Welcome to NanoLika"
  LangString NanoLikaWelcomeTitle 2052 "欢迎使用 NanoLika"
  LangString NanoLikaWelcomeTitle 1049 "Знакомьтесь, Лика"
  LangString NanoLikaWelcomeText 1033 "Lika helps with documents, files and tasks on your computer.$\r$\n$\r$\nWhile the app installs, discover what you can ask her to do."
  LangString NanoLikaWelcomeText 2052 "Lika 可以协助处理电脑上的文档、文件和日常任务。$\r$\n$\r$\n安装期间，了解可以交给她的事情。"
  LangString NanoLikaWelcomeText 1049 "Лика помогает с документами, файлами и делами на компьютере.$\r$\n$\r$\nПока устанавливается приложение, познакомьтесь с его возможностями."
  LangString NanoLikaFinishTitle 1033 "Lika is ready"
  LangString NanoLikaFinishTitle 2052 "Lika 已准备就绪"
  LangString NanoLikaFinishTitle 1049 "Лика готова"
  LangString NanoLikaFinishText 1033 "The app is installed. Sign in and try your first task."
  LangString NanoLikaFinishText 2052 "应用已安装。登录账户，试着交给 Lika 第一件事。"
  LangString NanoLikaFinishText 1049 "Приложение установлено. Войдите в аккаунт и поручите Лике первое дело."
  LangString NanoLikaRun 1033 "Open Lika"
  LangString NanoLikaRun 2052 "打开 Lika"
  LangString NanoLikaRun 1049 "Запустить Лику"
!macroend

!macro customHeader
  !insertmacro nanoLikaInstallerStrings
!macroend

!macro customPageAfterChangeDir
  Var NanoLikaProgressPage
  Var NanoLikaTipsWindow
  !define MUI_PAGE_CUSTOMFUNCTION_SHOW NanoLikaTipsShow
  !define MUI_PAGE_CUSTOMFUNCTION_LEAVE NanoLikaTipsLeave
  !define MUI_CUSTOMFUNCTION_GUIEND NanoLikaTipsLeave

  Function NanoLikaTipsShow
    ${IfNot} ${Silent}
      InitPluginsDir
      SetOverwrite try
      File /oname=$PLUGINSDIR\NanoLika.SetupTips.exe "${NanoLikaInstallerResources}\installer-generated\NanoLika.SetupTips.exe"
      SetOverwrite on
      FindWindow $NanoLikaProgressPage "#32770" "" $HWNDPARENT
      ClearErrors
      Exec '"$PLUGINSDIR\NanoLika.SetupTips.exe" "$NanoLikaProgressPage" "$LANGUAGE"'
      ; Failure of a decorative panel leaves the standard installation running.
      ClearErrors
    ${EndIf}
  FunctionEnd

  Function NanoLikaTipsLeave
    ${If} $NanoLikaProgressPage != 0
      FindWindow $NanoLikaTipsWindow "" "NanoLika.SetupTips" $NanoLikaProgressPage
      ${If} $NanoLikaTipsWindow != 0
        SendMessage $NanoLikaTipsWindow ${WM_CLOSE} 0 0 /TIMEOUT=500
      ${EndIf}
    ${EndIf}
  FunctionEnd
!macroend

!macro customFinishPage
  !ifndef HIDE_RUN_AFTER_FINISH
    Function NanoLikaStartApp
      ${If} ${isUpdated}
        StrCpy $1 "--updated"
      ${Else}
        StrCpy $1 ""
      ${EndIf}
      ; An elevated installer hands launch back to the ordinary desktop user.
      ${StdUtils.ExecShellAsUser} $0 "$launchLink" "open" "$1"
    FunctionEnd
    !define MUI_FINISHPAGE_RUN
    !define MUI_FINISHPAGE_RUN_FUNCTION NanoLikaStartApp
    !define MUI_FINISHPAGE_RUN_TEXT "$(NanoLikaRun)"
    ; Do not define MUI_FINISHPAGE_RUN_NOTCHECKED: checked by default, user may clear it.
  !endif
  !define MUI_FINISHPAGE_TITLE "$(NanoLikaFinishTitle)"
  !define MUI_FINISHPAGE_TEXT "$(NanoLikaFinishText)"
  !insertmacro MUI_PAGE_FINISH
!macroend
