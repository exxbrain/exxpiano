# Tasks

## 1. Основа: закрепление Alpine и контекст проекта

- [ ] 1.1 Добавить `alpine/repositories` (ветка `v3.24`, HTTPS-зеркало `main` и `community`), `alpine/keys/*.rsa.pub` (официальные ключи aarch64) и `.github/alpine-image.env` с SHA-256 ключей. Проверка: тест в `tests/test_build_tools.py` сверяет SHA-256 ключей с `alpine-image.env` и то, что репозитории указывают одну ветку по HTTPS.
- [ ] 1.2 Обновить `context` в `openspec/config.yaml` (Alpine 3.24, OpenRC, `/etc/exxpiano.conf` как файл окружения init-скриптов, `.apk`) и `Purpose` в `openspec/specs/piano/configuration/spec.md` (без EnvironmentFile systemd). Проверка: `openspec validate --specs --strict` проходит.

## 2. Окружение служб и общий слой ОС

- [ ] 2.1 Добавить `pi/exxpiano-env`: разбор `/etc/exxpiano.conf` через `exxpianoconf.parse_conf` с семантикой `EnvironmentFile`, разбиение `$FX_ARGS` на аргументы, `exec` команды. Проверка: `tests/test_exxpianoconf.py` получает тесты на кавычки, `\`-экранирование, `$(...)`, обратные кавычки и `;`, которые передаются буквально и не выполняются, а также на отсутствующий файл.
- [ ] 2.2 Добавить `pi/oslib.py` (`service_state`, `restart`, `synth_pid`, `read_messages`, `parse_syslog_errors`) на стандартной библиотеке. Проверка: новый `tests/test_oslib.py` на фикстурах вывода `rc-service`, на фальшивом `/proc` и на файлах busybox syslog (уровни, теги, дедупликация, лимит, отсечение до `btime`, переход через новый год, OOM `fluidsynth` из `kern.err`, ротация `.0`, нечитаемый журнал).
- [ ] 2.3 Добавить `pi/exxpiano-respawn` (перезапуск только при ненулевом коде с задержкой и выход с 0 при штатном завершении дочернего процесса). Проверка: тест в `tests/test_build_tools.py` запускает обёртку с программами, завершающимися кодами 0 и 1.

## 3. Init-скрипты OpenRC и правила устройств

- [ ] 3.1 Заменить `pi/*.service` на `pi/init.d/exxpiano`, `exxpiano-fx`, `exxpiano-web`, `exxpiano-wifi` и `exxpiano-firstboot` по таблице из design.md (supervise-daemon, `respawn_max=0`, `rc_ulimit`, `nicelevel`, `need`/`after`, `logger --prio-prefix`). Удалить `exxpiano-usb@.service`. Проверка: новый `tests/test_openrc.py` проверяет `sh -n`, `#!/sbin/openrc-run`, зависимости, запуск через `exxpiano-env`, отсутствие `source` конфигурации и то, что Wi-Fi запускается через `exxpiano-respawn`.
- [ ] 3.2 Переписать `pi/90-exxpiano.rules` для eudev: `setsid -f rc-service --ifstarted exxpiano restart` для звуковых карт и `setsid -f exxpiano-usb.py add|remove /dev/%k` для накопителей. Проверка: `tests/test_openrc.py` (или `test_usb_import.py`) разбирает правила и проверяет отсутствие `systemctl`/`SYSTEMD_WANTS` и наличие `--ifstarted`. Убедиться, что `exxpiano-usb.py remove` идемпотентен (тест в `test_usb_import.py`).
- [ ] 3.3 Перенести `dietpi/exxpiano-console.sh` в `pi/exxpiano-console.sh`, а логику `Automation_Custom_Script.image.sh` — в init-скрипт `exxpiano-firstboot` (расширение раздела, `ensure-name`, выбор `piano-lite.sf2`, метка). Проверка: тест запускает логику выбора банка с фальшивым `/proc/meminfo` (512 МБ и 1 ГБ) и проверяет `PIANO_SF2` в `/etc/exxpiano.conf`, а также ошибку расширения раздела, которая не прерывает остальное.

## 4. Web-интерфейс и Wi-Fi на Alpine

- [ ] 4.1 Перевести `pi/exxpiano-web.py` на `oslib`: состояние служб, PID синтезатора, перезапуск, служебные сообщения, OOM, строка `audio output`. Удалить `parse_journal_errors`. Проверка: `tests/test_web.py` (сохранив текущие незакоммиченные изменения) и `tests/test_demo_web.py` проходят. API ошибок отдаёт тот же формат и признак недоступности при нечитаемом журнале. Обновить `scripts/demo-web.py`, если он подменяет `systemctl`/`journalctl`.
- [ ] 4.2 Перевести `pi/exxpiano-wifi.py` на прямой запуск `wpa_supplicant -B` и `udhcpc` с PID-файлами в `/run/exxpiano-wifi`, убрать stanza `interfaces.d`, `ifup`/`ifdown` и `dhclient`. Определять чужой менеджер по `networkmanager`, `iwd` и `connman` в OpenRC. Проверка: `tests/test_exxpiano_wifi.py` переписан под новые команды. Сценарии подключения, неверного пароля с восстановлением, забывания последней сети и чужого менеджера проходят.
- [ ] 4.3 В `pi/wifilib.py` заменить `hostnamectl` на атомарную запись `/etc/hostname`, `hostname -F` и `rc-service --ifstarted avahi-daemon restart`, а `group=nogroup` — на `group=nobody` в `dnsmasq_conf`. Проверка: тесты `test_exxpiano_wifi.py` и `test_deviceid.py` на смену имени и `/etc/hosts` проходят на временном каталоге.

## 5. Пакеты `.apk`

- [ ] 5.1 Добавить `packaging/alpine/exxpiano/APKBUILD` с `.post-install`, `.post-upgrade` и `.pre-deinstall` (без действий в chroot, `rc-update`, демо-MIDI один раз, сохранение `.apk` в `/var/lib/exxpiano/packages/` с двумя последними), а также `packaging/alpine/exxpiano-soundfonts/APKBUILD`. Перенести `exxpiano-install-banks` на `rc-service`. Проверка: новый `tests/test_apk.py` сверяет список файлов APKBUILD с `pi/` (ни один файл программы не пропущен), `depends` без графических пакетов, `/etc/exxpiano.conf` в `/etc` и `sh -n` install-скриптов.
- [ ] 5.2 Добавить `packaging/alpine/py3-mido/APKBUILD` и `packaging/alpine/py3-python-rtmidi/APKBUILD` с закреплёнными версиями и `sha512sums`. Проверка: сборка в контейнере arm64 и `python3 -c "import mido, rtmidi; mido.get_input_names()"` в контейнере (порты ALSA там пусты, импорт и backend работают).
- [ ] 5.3 Добавить `scripts/build-apks.py` (Docker `alpine:3.24 --platform linux/arm64`, `abuild -r`, `dist/apk/aarch64/` с подписанным `APKINDEX`, ключ из `EXXPIANO_APK_KEY` или `~/.abuild`, dev-ключ с предупреждением). Ключ не должен попадать в журнал. Удалить `scripts/build-deb.py`, `packaging/debian/` и `tests/test_deb.py`. Проверка: тест на разбор аргументов и маскировку ключа в `tests/test_build_tools.py`, реальная сборка в `image-pipeline`.
- [ ] 5.4 Добавить `scripts/test-apk-upgrade.sh`: в контейнере `alpine:3.24` с OpenRC и фальшивыми программами-заглушками он проверяет установку, `.apk-new` для изменённой конфигурации, обновление, откат при «неподнявшейся» службе, отказ пакету с чужой подписью и отсутствие предыдущей версии. Проверка: скрипт проходит локально и в `image-pipeline`.

## 6. Обновление и развёртывание

- [ ] 6.1 Переписать `deploy/upgrade-on-pi.sh` и `deploy/rollback-on-pi.sh` на `apk verify`, `apk add --simulate`, `apk add ./file.apk`, `rc-service` и чтение журнала из `/var/log/messages`. Откат идёт через `apk add` прежнего файла. `--allow-untrusted` разрешён только при `EXXPIANO_DEV_UNTRUSTED=1`. Проверка: `tests/test_deploy.py` обновлён, контейнерный сценарий — в 5.4.
- [ ] 6.2 Перевести `scripts/deploy-to-pi.sh` на `build-apks.py` и копирование `.apk`, убрать упоминания DietPi-пароля. Удалить `pi/install.sh` (установка вне образа больше не поддерживается) и ссылки на него. Проверка: `tests/test_deploy.py` и `bash -n`.

## 7. Образ SD-карты

- [ ] 7.1 Заменить `scripts/sd-image-inner.sh` сборщиком из пакетов по п. 1 design.md: MBR, FAT32 и ext4, `apk.static --initdb` с закреплёнными ключами, eudev, OpenRC runlevels, `config.txt`/`cmdline.txt` с PARTUUID, `inittab` с консолью, `/etc/network/interfaces` только с `lo`/`eth0`, `SYSLOGD_OPTS`, `sshd` с `PermitRootLogin` по наличию пароля, публичный ключ проекта, банки. Чтение образа обратно проверяет: службы в runlevel `default`/`boot`, `apk info -e exxpiano`, отсутствие getty на tty1 и автовхода, ядро и dtb для BCM2837, BCM2711 и BCM2712, `dtparam=audio=on`, отсутствие installation ID и отсутствие файлов Debian и DietPi (`/var/lib/dpkg`, `/boot/dietpi*`).
- [ ] 7.2 Переписать `scripts/sd-builder.Dockerfile` на `alpine:3.24` с `apk-tools-static`, `e2fsprogs`, `dosfstools`, `sfdisk`, `xz`, а `scripts/build-sd-image.py` — на один образ `exxpiano-rpi-v<версия>-alpine-<версия-alpine>-sd.zip`: без `--target` и `--dietpi`, манифест с веткой, зеркалом, ключами и `apk info -v`, README без DietPi. Совместимость с `scripts/sd-registry.py` сохраняется. Проверка: `tests/test_build_sd_image.py` обновлён (имя, манифест, секреты не попадают в аргументы и журнал, ошибка при отсутствующем банке).
- [ ] 7.3 Заменить `scripts/test-sd-image-pipeline.sh` и `scripts/sd-smoke-test.sh` на сборку реального образа Alpine с заглушками банков (без стенд-ина DietPi) и удалить `scripts/sd-fake-dietpi.sh` и каталог `dietpi/`. Проверка: конвейер проходит в Docker с `--privileged` и binfmt arm64.

## 8. CI и релиз

- [ ] 8.1 Обновить `.github/workflows/image-pipeline.yml` (пути-триггеры `alpine/**`, `packaging/alpine/**`, сборка `.apk`, `test-apk-upgrade.sh`, конвейер образа) и `build.yml` (проверка init-скриптов). Проверка: workflow проходит на ветке PR.
- [ ] 8.2 Обновить `.github/workflows/release.yml` (секрет `EXXPIANO_APK_KEY` с ошибкой при его отсутствии, один ZIP, без DietPi) и `.github/release-description.md` (Pi 3/4/5, Alpine). Удалить `dietpi-mirror.yml` и `.github/dietpi-image.env`. Проверка: `scripts/release-notes.py` и тесты на совпадение тега с `VERSION` проходят, а dry-run release workflow на тестовом теге в форке собирает ZIP.

## 9. Документация

- [ ] 9.1 Синхронно обновить `README.md` и `README.ru.md`: требования (Alpine 3.24, Pi 3/4/5, Pi 2 убрана), сборка образа, обновление, журналы (`/var/log/messages`, `rc-service`), раздел о переносе с DietPi из п. 10 design.md, статус аппаратных проверок. Проверка: в обоих README нет упоминаний `apt`, `dpkg`, `systemctl`, `journalctl` и DietPi, кроме раздела о переносе (проверяется `grep` в тесте документации, если он есть, иначе вручную).
- [ ] 9.2 Синхронно обновить `docs/BUILD-IMAGE*.md` и `docs/TROUBLESHOOTING*.md` (команды OpenRC, eudev, syslog, `apk`), а также `NOTICE.md` (лицензии пакетов Alpine вместо Debian и DietPi) и `CHANGELOG.md`. Проверка: те же проверки `grep`.

## 10. Проверка на оборудовании (нельзя заменить автотестом)

- [ ] 10.1 Raspberry Pi 3 B (1 ГБ): первая загрузка без сети, расширение раздела, консоль tty1, setup-сеть и QR-код, подключение к Wi-Fi, звук через 3,5 мм и USB-карту, горячее подключение USB-карты, накопителя (FAT32, exFAT, ext4) и MIDI-клавиатуры, отсутствие xrun при `PERIOD_SIZE=64` и полифонии 192 по сравнению с DietPi. Результаты записываются в CHANGELOG.
- [ ] 10.2 Raspberry Pi 3 A+ (512 МБ): автоматический выбор `piano-lite.sf2` и стабильная работа без OOM.
- [ ] 10.3 Raspberry Pi 4 и Raspberry Pi 5: загрузка того же образа, Pi 5 с USB-звуковой картой и ошибка «нет аудиоустройства» без неё.
- [ ] 10.4 Обновление через `scripts/deploy-to-pi.sh` на работающем устройстве и откат при искусственно сломанной службе.
