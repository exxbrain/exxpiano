## 1. Сохранность при отключении питания

- [x] 1.1 `fsync` файла и каталога в `wifilib.write_secret`, `exxpianoconf.apply_updates`, `profiles.save`; тест `tests/test_wifilib.py` (`DurableWriteTests`)

## 2. Вынутая звуковая карта

- [x] 2.1 Признак `missing` в состоянии выхода (`pi/exxpiano-web.py`), страница показывает «нет звуковой карты» / «… отключена»; тесты `tests/test_web.py`

## 3. Документация и проверка

- [x] 3.1 CHANGELOG (раздел «Unreleased»)
- [x] 3.2 `python3 -m unittest discover -s tests`, `scripts/lint.sh`
- [x] 3.3 На Pi: выдернуть питание сразу после подключения к Wi-Fi и после изменения настройки; на Pi 5 вынуть USB-карту и посмотреть страницу (проверено владельцем: всё работает)
