# Tasks

## 1. Предусловие и контрольный инвентарь

- [ ] 1.1 Убедиться, что `ship-device-image-with-default-piano` реализован и архивирован, а базовая `provisioning-and-upgrades` содержит итоговые требования полного DietPi-образа и release pipeline без требований отдельных Debian-пакетов и installer bundle; проверить через `openspec list --json` и `openspec show provisioning-and-upgrades --type spec` до любых перемещений.
- [ ] 1.2 Сформировать нормализованный контрольный инвентарь всех заголовков, нормативных описаний и сценариев базовых specs без Purpose и capability path; проверить, что инвентарь не содержит дубликатов требований и сохранён для итогового автоматического сравнения.

## 2. Перенос цельных доменов

- [ ] 2.1 Перенести `bank-management` и `soundfont-build` без изменения нормативных блоков в `banks/management/spec.md` и `banks/build/spec.md`; проверить `openspec show banks/management --type spec` и `openspec show banks/build --type spec`, а также сравнение соответствующих частей контрольного инвентаря.
- [ ] 2.2 Перенести `runtime-configuration` в `piano/configuration/spec.md`, а три runtime-требования из `piano-runtime` — в `piano/runtime/spec.md`; проверить оба новых spec через `openspec show` и подтвердить, что требование привязки платы осталось только для последующего переноса в `device/identity`.
- [ ] 2.3 Перенести `wifi-setup` без изменения нормативных блоков в `network/wifi-setup/spec.md`; проверить новый capability через `openspec show network/wifi-setup --type spec` и сравнение его требований и сценариев с контрольным инвентарём.

## 3. Разделение web, device и delivery

- [ ] 3.1 Создать `web/interface`, `web/access-control` и `device/monitoring` из соответствующих требований `web-control`, сохранив их дословно; проверить через `openspec show` наличие двух требований интерфейса и по одному требованию авторизации и мониторинга.
- [ ] 3.2 Создать `device/identity` из требований привязки платы, учёта плат и веб-идентификации/QR-кодов; проверить, что все их сценарии присутствуют ровно один раз и strict validation принимает объединённый spec.
- [ ] 3.3 Создать `device/image`, `device/upgrades` и `delivery/release` из итоговых требований `provisioning-and-upgrades` после архивации change образа; проверить через `openspec show`, что полный DietPi-образ относится только к `device/image`, откат обновления — только к `device/upgrades`, а CI и версия — только к `delivery/release`.

## 4. Согласование активных changes

- [ ] 4.1 Перевести `import-banks-from-usb` с `bank-management` на `banks/management` и с `usb-bank-import` на `banks/usb-import`, включая proposal, concrete delta-spec paths, design и tasks; проверить `openspec validate import-banks-from-usb --strict --no-interactive` и отсутствие старых IDs внутри change.
- [ ] 4.2 Проверить остальные незавершённые changes и обновить все ссылки на семь legacy capability IDs без изменения их intent; проверить каждый затронутый change отдельной strict validation и поиском по `openspec/changes/`.

## 5. Удаление legacy-структуры и интеграционная проверка

- [ ] 5.1 Сравнить нормализованный итоговый инвентарь с контрольным, удалить семь старых плоских каталогов только при полном совпадении и проверить, что каждый leaf целевого двухуровневого дерева содержит ровно один `spec.md`, а specs третьего уровня отсутствуют.
- [ ] 5.2 Запустить `openspec list --specs --json`, `openspec validate --all --strict --no-interactive` и `python3 -m unittest discover -s tests -v`; миграция готова только при ожидаемом наборе capability IDs, полном прохождении проверок и отсутствии изменений runtime-кода.
