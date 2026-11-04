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
!macroend

!macro customInit
  nsExec::Exec 'taskkill /IM NanoLika.exe /T'
  Sleep 1200
  nsExec::Exec 'taskkill /F /IM NanoLika.exe /T'
  Sleep 600
!macroend

; Первая страница мастера: предупредить, что установка долгая, и показать Лику.
!macro customWelcomePage
  !define MUI_WELCOMEPAGE_TITLE "Установка nanoLika"
  !define MUI_WELCOMEPAGE_TEXT "Лика переезжает на этот компьютер вместе с моделями, Node и средой управления экраном.$\r$\n$\r$\nУстановка занимает 5–15 минут: распаковываются десятки тысяч файлов. Так и должно быть — окно можно свернуть и вернуться позже."
  !insertmacro MUI_PAGE_WELCOME
!macroend

; Удаление приложения — это выход из аккаунта. Профиль в ~/.nanolika переживает
; деинсталляцию (чаты и память нужны после переустановки), но сессия и ключи
; провайдеров — нет: иначе новая установка сразу открывается в чужом аккаунте
; (2026-11-03, поймано на .8: снесли, поставили заново — уже залогинены).
!macro customUnInstall
  Delete "$PROFILE\.nanolika\desktop\nanolika\cloud.json"
  Delete "$PROFILE\.nanolika\desktop\.credentials.yaml"
!macroend
