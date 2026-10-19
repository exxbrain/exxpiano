# Proposal

## Why

Сейчас устройство собирается поверх DietPi (Debian Trixie): образ тянет онлайн-ориентированную автоматизацию DietPi, которую приходится отключать drop-in-ами, systemd и journald, полноценный Debian-userspace и два отдельных образа для Pi 2/3/4 и Pi 5. Для однофункционального инструмента нужна маленькая, предсказуемая и полностью контролируемая нами система. Alpine Linux 3.24 (поддержка до 2028-06) даёт минимальную базу, OpenRC и подписанные `.apk`, а одно ядро `linux-rpi` (aarch64) с версии 3.19 загружает Raspberry Pi 3, 4 и 5. Это позволяет выпускать один образ.

## What Changes

- **BREAKING** Единственной целевой ОС становится Alpine Linux 3.24 aarch64 в sys-режиме, то есть в обычной установке на ext4. Поддержка Debian/DietPi и Raspberry Pi OS удаляется целиком: образ DietPi, `.deb`-пакеты, `pi/install.sh` для apt, DietPi-автоматизация первого запуска, workflow `dietpi-mirror` и `.github/dietpi-image.env`.
- **BREAKING** Вместо двух образов (`rpi234`, `rpi5`) выпускается один образ `exxpiano-rpi-v<версия>-alpine-<версия-alpine>-sd.zip` для Raspberry Pi 3, 4 и 5. Raspberry Pi 2 больше не входит в список поддерживаемых плат: на ней образ не проверялся и не тестируется. Сборщик сам создаёт rootfs и загрузочный раздел из закреплённых пакетов Alpine, без готового образа дистрибутива.
- **BREAKING** systemd units заменяются на OpenRC init-скрипты с `supervise-daemon`, сохраняющие порядок запуска, автоматический перезапуск и окружение из `/etc/exxpiano.conf`. Устройства обслуживает `eudev`, поэтому udev-правила сохраняются. Вызовы `systemctl` в них заменяются `rc-service`, а шаблонный `exxpiano-usb@.service` — отсоединённым вызовом `exxpiano-usb.py`. Перезапуск при смене звуковой карты и монтирование USB-накопителей сохраняются.
- **BREAKING** Доставка и обновление идут через подписанные ключом проекта пакеты `.apk`, собранные `abuild`: `exxpiano`, `exxpiano-soundfonts`, а также собственные пакеты `py3-mido` и `py3-python-rtmidi`, которых нет в репозиториях Alpine 3.24. Обновление и откат на устройстве работают через `apk` вместо `dpkg`.
- Служебные сообщения и диагностика web-интерфейса читаются из syslog (`/var/log/messages`) и журнала ядра (`dmesg`) вместо `journalctl`. Состояние служб берётся из `rc-service`, а не из `systemctl`.
- Wi-Fi supervisor управляет сетью через `ifupdown-ng`, `wpa_supplicant` и `udhcpc` вместо `ifupdown` и `isc-dhcp-client`. hostname задаётся без `hostnamectl`. Проверка «чужого» сетевого менеджера смотрит на службы OpenRC.
- Консоль tty1 показывает информационный экран через `/etc/inittab` вместо getty drop-in.
- Обновляются спецификации, `openspec/config.yaml` (контекст проекта), README/TROUBLESHOOTING/BUILD-IMAGE на английском и русском, CI и релизный процесс.

## Capabilities

### New Capabilities

- `device/packages`: пакеты приложения для Alpine: состав, подпись, зависимости, сохранение пользовательской конфигурации при обновлении и собственные пакеты MIDI-зависимостей.

### Modified Capabilities

- `device/image`: база образа меняется на Alpine, образ становится единым для Pi 3/4/5, меняется имя артефакта и проверки консоли и первого запуска.
- `device/upgrades`: обновление и откат выполняются через подписанные `.apk` и OpenRC-службы.
- `delivery/release`: требование публикации заменяется версией для Alpine (REMOVED + ADDED): закреплённая основа меняется с образа DietPi на набор пакетов Alpine (репозиторий и версия, `APKINDEX`, ключи), релиз выпускается одним ZIP.
- `piano/configuration`: `/etc/exxpiano.conf` остаётся файлом окружения, но теперь его читают init-скрипты OpenRC, а не systemd.
- `piano/runtime`: добавляется явное требование надзора за службами: перезапуск после сбоя и отсутствие цикла перезапусков у Wi-Fi supervisor, который нормально завершился.
- `network/wifi-setup`: требование совместимости с сетевым менеджером заменяется версией для Alpine (REMOVED + ADDED) (`ifupdown-ng`, NetworkManager, iwd, connman).
- `web/interface`: добавляется требование к служебным сообщениям, которые берутся из системного журнала Alpine и журнала ядра.

## Impact

- Код: `pi/exxpiano-web.py` (`systemctl`, `journalctl`, `parse_journal_errors`), `pi/exxpiano-wifi.py` и `pi/wifilib.py` (`ifup`/`ifdown`, `dhclient`, `hostnamectl`, `systemctl`), `pi/90-exxpiano.rules`, все `pi/*.service`, `pi/install.sh`, `packaging/`, `scripts/build-deb.py`, `scripts/build-sd-image.py`, `scripts/sd-image-inner.sh`, `scripts/sd-fake-dietpi.sh`, `scripts/sd-smoke-test.sh`, `scripts/test-sd-image-pipeline.sh`, `scripts/sd-builder.Dockerfile`, `deploy/*.sh`, `scripts/deploy-to-pi.sh`, каталог `dietpi/`. Тесты: `test_deb`, `test_build_sd_image`, `test_deploy`, `test_web`, `test_exxpiano_wifi`, `test_build_tools`.
- Зависимости среды выполнения: `fluidsynth` (2.5.x из community), `alsa-utils`, `python3`, `hostapd`, `dnsmasq`, `iw`, `wpa_supplicant`, `ifupdown-ng`, `avahi`, `eudev` (вместо `mdev` по умолчанию), `exfat`/`vfat` из ядра `linux-rpi`. Собственные `py3-mido` и `py3-python-rtmidi` собираются вместе с приложением. musl вместо glibc: Python-код только на стандартной библиотеке, поэтому он не меняется, но C++-расширение rtmidi собирается под musl.
- CI: `image-pipeline` и `release` получают Alpine-сборщик и секрет ключа подписи `.apk`. Workflow `dietpi-mirror` удаляется и заменяется закреплением зеркала пакетов Alpine.
- Устройства на DietPi не обновляются на месте: их SD-карты перезаписываются новым образом. Банки, импортированные пользователем, и installation ID на старой карте не переносятся автоматически (см. design.md, «Миграция»).
- Незавершённые changes `provision-wifi-over-bluetooth` (BlueZ, D-Bus, systemd unit), `keep-setup-network-while-joining`, `play-midi-files-from-usb` и `delete-installed-banks` правят те же спецификации. Перед их применением дельты нужно сверить с этим change. Bluetooth-change должен перейти на пакеты Alpine (`bluez`, `py3-dbus`, `py3-gobject3`) и OpenRC.
- Проверка на реальной Raspberry Pi **требуется и в рамках этого предложения не выполнена**: загрузка образа на Pi 3, 4 и 5, звук через 3.5 мм и USB, горячее подключение USB-аудио, накопителей и MIDI-клавиатур через `eudev`, setup-сеть и подключение к Wi-Fi, обновление и откат через `apk`, задержка звука FluidSynth на musl по сравнению с DietPi.
