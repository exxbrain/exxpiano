# Design

## Context

Базовая спецификация сейчас состоит из семи плоских capabilities с 32 требованиями. Большинство цельных файлов можно перенести как единый блок, но `piano-runtime`, `web-control` и `provisioning-and-upgrades` содержат требования из разных предметных областей. Одновременно активны changes, которые ссылаются на старые пути: `ship-device-image-with-default-piano` изменяет `provisioning-and-upgrades`, а `import-banks-from-usb` изменяет `bank-management` и вводит `usb-bank-import`.

## Goals / Non-Goals

**Goals:**

- ввести стабильную двухуровневую схему `<domain>/<capability>`;
- сохранить каждое существующее требование и каждый сценарий без смысловых изменений;
- собрать идентичность устройства в одной capability вместо трёх разных файлов;
- согласовать все незавершённые changes с новыми путями;
- сделать потерю, дублирование или непреднамеренное изменение контракта автоматически обнаружимыми.

**Non-Goals:**

- изменение runtime-кода, API, сборочных артефактов или пользовательского поведения;
- редактирование нормативных формулировок одновременно с переносом;
- создание третьего уровня вложенности;
- реализация или архивация функциональных changes в рамках этой миграции.

## Decisions

### 1. Дерево ограничивается доменом и capability

Целевая структура:

```text
openspec/specs/
  piano/
    runtime/spec.md
    configuration/spec.md
  banks/
    management/spec.md
    build/spec.md
  device/
    identity/spec.md
    monitoring/spec.md
    image/spec.md
    upgrades/spec.md
  web/
    interface/spec.md
    access-control/spec.md
  network/
    wifi-setup/spec.md
  delivery/
    release/spec.md
```

Будущий USB-импорт из активного change получает путь `banks/usb-import/spec.md`. Третий уровень отклонён: он усложнил бы ссылки и навигацию без текущей необходимости.

### 2. Требования переносятся по ответственности

| Исходная capability | Требования | Целевая capability |
|---|---|---|
| `piano-runtime` | запуск синтезатора; выбор аудиовыхода; передача MIDI и механика | `piano/runtime` |
| `piano-runtime` | привязка карты к плате | `device/identity` |
| `runtime-configuration` | все требования | `piano/configuration` |
| `bank-management` | все требования | `banks/management` |
| `soundfont-build` | все требования | `banks/build` |
| `wifi-setup` | все требования | `network/wifi-setup` |
| `web-control` | состояние устройства; применение настроек | `web/interface` |
| `web-control` | защита токеном | `web/access-control` |
| `web-control` | мониторинг | `device/monitoring` |
| `web-control` | идентификация и QR-коды | `device/identity` |
| `provisioning-and-upgrades` | персональный SD-образ | `device/image` |
| `provisioning-and-upgrades` | привязка и учёт плат | `device/identity` |
| `provisioning-and-upgrades` | безопасное обновление | `device/upgrades` |
| `provisioning-and-upgrades` | проверка и публикация сборок | `delivery/release` |

Требования Debian-пакетов и отдельного DietPi-инсталлятора в таблицу не входят: change `ship-device-image-with-default-piano` удаляет их до начала миграции. Альтернатива — переносить каждый старый файл целиком — отклонена, поскольку сохранила бы смешанные границы `web-control` и `provisioning-and-upgrades` под новыми именами.

### 3. Текстовая эквивалентность проверяется до удаления legacy-файлов

Перед переносом фиксируется нормализованный список заголовков требований, нормативных описаний и сценариев после архивации change образа. После создания дерева тот же список строится повторно без учёта capability path и Purpose. Legacy-файлы удаляются только при полном совпадении количества и нормализованного содержимого.

Purpose для новых листьев формулируется из границы capability и не участвует в проверке поведенческой эквивалентности. Нормативные блоки переносятся дословно; улучшение языка выполняется отдельными changes.

### 4. Функциональные changes переводятся на новые пути явно

`ship-device-image-with-default-piano` MUST быть реализован и архивирован первым. После этого миграция работает с его итоговой версией требований образа и release pipeline.

В `import-banks-from-usb` пути и файлы delta-spec меняются следующим образом:

- `bank-management` -> `banks/management`;
- `usb-bank-import` -> `banks/usb-import`.

Proposal, design и tasks этого change обновляются только там, где они называют capability path или уже удаляемый отдельный installer bundle. `block-synth-when-board-serial-unreadable` имеет `skip_specs: true`, поэтому его planning artifacts не требуют миграции capability path.

### 5. Структурная миграция не содержит delta-spec

У change установлен `skip_specs: true`, потому что он не добавляет и не меняет наблюдаемое поведение. Основные specs реорганизуются непосредственно во время apply с сохранением контента; создавать искусственные ADDED/REMOVED deltas для каждого переноса не следует.

## Risks / Trade-offs

- [Миграция запущена до архивации change образа] → считать это жёстким предусловием и остановить apply до изменения файлов.
- [Требование потеряно или продублировано при объединении `device/identity`] → сравнивать нормализованный набор требований и сценариев до удаления исходников.
- [Активный change продолжает ссылаться на старый путь] → после переноса проверять все `openspec/changes/**` поиском старых IDs и строгой валидацией.
- [Слишком мелкие capabilities усложняют навигацию] → сохранять максимум два уровня и выделять лист только для самостоятельной ответственности, которая меняется независимо.
- [История перемещений плохо читается в Git] → переносить неизменяемые файлы отдельно от сборки объединённых specs и не совмещать миграцию с редактурой формулировок.

## Migration Plan

1. Реализовать и архивировать `ship-device-image-with-default-piano`; убедиться, что итоговая `provisioning-and-upgrades` больше не содержит требования отдельных пакетов и installer bundle.
2. Зафиксировать нормализованный инвентарь требований и сценариев базовых specs.
3. Создать двухуровневое дерево, дословно перенести цельные capabilities и собрать разделяемые требования согласно таблице.
4. Обновить `import-banks-from-usb` и любые другие найденные ссылки на legacy capability IDs.
5. Сравнить инвентарь, удалить семь старых плоских каталогов и запустить строгую валидацию всех specs и changes.

Откат состоит в восстановлении семи плоских каталогов и прежних путей в активных changes. Runtime и данные устройства не затрагиваются.
