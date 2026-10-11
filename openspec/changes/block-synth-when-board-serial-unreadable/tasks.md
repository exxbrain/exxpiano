# Tasks

## 1. Fail-closed проверка платы

- [ ] 1.1 Изменить `deviceid.check()` так, чтобы `locked + bound + status.ok is None` возвращал `EX_CONFIG`/78 с отдельным диагностическим сообщением; заменить старое fail-open ожидание и проверить `python3 -m unittest tests.test_deviceid -v`.
- [ ] 1.2 Сделать вызов checker обязательным в `exxpiano-run` при `DEVICE_LOCK=1`, включая постоянный отказ при недоступном Python/checker, и добавить shell-интеграционные сценарии unreadable serial и unavailable checker; проверить `python3 -m unittest tests.test_audio_select -v` и отсутствие команды FluidSynth в обоих отказах.
- [ ] 1.3 Сохранить разрешение для unlocked, unbound и совпадающей платы и проверить отдельными regression-сценариями функции и wrapper, что они по-прежнему возвращают 0.

## 2. Документация поведения

- [ ] 2.1 Обновить docstring и комментарии в `deviceid.py`, `exxpiano-run`, `exxpiano.service` и `exxpiano.conf`; проверить поиском, что формулировка об разрешении запуска при unreadable serial удалена из runtime-источников.
- [ ] 2.2 Синхронно обновить английские и русские разделы README/build-image docs: locked-карта запускается только при читаемом совпадающем serial; проверить сопоставлением обеих языковых версий.

## 3. Интеграционная проверка

- [ ] 3.1 Запустить `python3 -m unittest discover -s tests -v` и `openspec validate --all --strict --no-interactive`; изменение готово только при полном прохождении обоих наборов без аппаратных предположений.
