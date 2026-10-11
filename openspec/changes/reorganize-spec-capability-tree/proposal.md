# Proposal

## Why

Текущие плоские capabilities смешивают независимые области: `provisioning-and-upgrades` объединяет образ устройства, учёт, обновление и release pipeline, а `web-control` — интерфейс, авторизацию, мониторинг и идентичность. Двухуровневое дерево сделает границы требований явными и позволит будущим changes затрагивать только свою предметную область.

## What Changes

- **BREAKING** для ссылок OpenSpec: заменить плоские capability paths двухуровневыми путями вида `<domain>/<capability>`.
- Разнести требования без изменения формулировок и наблюдаемого поведения по доменам `piano`, `banks`, `device`, `web`, `network` и `delivery`.
- Объединить требования идентичности, привязки платы, нумерации, hostname и QR-кодов в `device/identity`.
- Разделить `web-control` на `web/interface`, `web/access-control`, `device/monitoring` и `device/identity`.
- Разделить итоговое содержимое `provisioning-and-upgrades` на `device/image`, `device/identity`, `device/upgrades` и `delivery/release`.
- Перенести остальные цельные capabilities без внутреннего изменения: `piano/runtime`, `piano/configuration`, `banks/management`, `banks/build` и `network/wifi-setup`.
- Обновить пути в активных changes, включая перенос будущего `usb-bank-import` в `banks/usb-import`.
- Ограничить дерево двумя уровнями: домен и capability; каждый лист SHALL содержать один `spec.md`.

## Capabilities

### New Capabilities

Нет. Change реорганизует существующие поведенческие контракты без добавления требований; для него установлен `skip_specs: true`.

### Modified Capabilities

Нет. Тексты требований и сценариев не меняются; изменяется только их расположение и идентификаторы capabilities.

## Impact

- Изменяются только структура `openspec/specs/` и ссылки на capability paths в активных planning artifacts; runtime-код, сборочные сценарии и пользовательское поведение не затрагиваются.
- Change зависит от предварительного применения и архивации `ship-device-image-with-default-piano`, поскольку тот ещё изменяет `provisioning-and-upgrades`.
- Активный change `import-banks-from-usb` потребуется согласованно перевести с `bank-management` и `usb-bank-import` на `banks/management` и `banks/usb-import`.
- Проверка выполняется строгой валидацией OpenSpec и сравнением полного набора требований и сценариев до и после переноса.
- Проверка на реальной Raspberry Pi не требуется, поскольку изменение не затрагивает исполняемое поведение или артефакты устройства.
