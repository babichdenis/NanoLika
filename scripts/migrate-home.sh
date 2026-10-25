#!/bin/sh
# Переезд домашнего каталога приложения с историческим именем на текущее.
#
# Приложение хранило данные в ~/.nanomuse/desktop (так называлось до ребрендинга).
# Код уже ищет ~/.nanolika/desktop и при запуске переименовывает каталог сам, но это
# удаётся только когда каталог свободен: работающее приложение держит в нём открытые
# файлы, и переименование откладывается. Этот скрипт делает то же самое, пока
# приложение закрыто.
#
#   sh ~/Dev/nanoMuse/scripts/migrate-home.sh
#
# Ничего не удаляет: при любой ошибке каталог остаётся на месте, а скрипт говорит, что именно
# не получилось.
set -eu

OLD="$HOME/.nanomuse/desktop"
NEW="$HOME/.nanolika/desktop"

if [ ! -d "$OLD" ]; then
  if [ -d "$NEW" ]; then
    echo "Уже переехало: $NEW"
    exit 0
  fi
  echo "Нечего переезжать: $OLD не существует"
  exit 1
fi

if pgrep -x NanoLika >/dev/null 2>&1; then
  echo "Приложение ещё запущено. Закройте его (Cmd+Q) и запустите скрипт снова."
  exit 2
fi

if [ -d "$NEW" ]; then
  echo "Оба каталога существуют:"
  echo "  старый: $OLD"
  echo "  новый:  $NEW"
  echo "Ничего не делаю, чтобы не потерять данные. Разберите вручную."
  exit 3
fi

mkdir -p "$(dirname "$NEW")"
if mv "$OLD" "$NEW"; then
  echo "Переехало: $OLD → $NEW"
else
  echo "Не удалось переименовать. Каталог остался на месте: $OLD"
  exit 4
fi

# Симлинки внутри профиля абсолютные: после переезда проверяем, что они ведут туда, куда надо.
LINK="$NEW/profiles/nanolika/node_modules/dsh-nanomuse"
if [ -L "$LINK" ]; then
  TARGET=$(readlink "$LINK")
  if [ ! -e "$LINK" ]; then
    PLUGIN="$HOME/Library/Application Support/nanolika-desktop/payload/dsh/node_modules/dsh-nanomuse"
    if [ -d "$PLUGIN" ]; then
      ln -sfn "$PLUGIN" "$LINK"
      echo "Симлинк плагина переставлен на $PLUGIN"
    else
      echo "Внимание: симлинк плагина ведёт в $TARGET, и цели нет. Запустите приложение: оно починит."
    fi
  else
    echo "Симлинк плагина цел: $TARGET"
  fi
fi

echo "Готово. Запускайте NanoLika."
