# Design

## Context

Сейчас продукт держится на нескольких частях Debian и DietPi:

- **Образ.** `scripts/sd-image-inner.sh` берёт готовый образ DietPi, ставит в chroot `.deb` через `apt` и настраивает автоматизацию DietPi: `dietpi.txt`, drop-in `dietpi-firstboot`, postboot-скрипт и getty drop-in.
- **Пакеты.** `scripts/build-deb.py` собирает `.deb` на стандартной библиотеке. Скрипты `postinst`, `prerm` и `postrm` вызывают `systemctl`.
- **Службы.** systemd units с `EnvironmentFile=/etc/exxpiano.conf`, `Restart=always` и `LimitRTPRIO`. USB-накопители монтирует шаблон `exxpiano-usb@.service`, который запускается из udev через `SYSTEMD_WANTS`.
- **Web.** `exxpiano-web.py` получает PID синтезатора через `systemctl show -p MainPID`, состояние служб через `systemctl is-active`, служебные сообщения через `journalctl -o json` (`parse_journal_errors`), журнал ядра через `journalctl -k`, а перезапуск делает `systemctl restart`.
- **Wi-Fi.** `exxpiano-wifi.py` и `wifilib.py` работают через `ifup`/`ifdown`, добавляют свою stanza в `interfaces.d`, вызывают `dhclient` для дополнительных проводных адаптеров, `hostnamectl` и `systemctl` для avahi и NetworkManager.
- **Обновление.** `deploy/upgrade-on-pi.sh` и `rollback-on-pi.sh` используют `dpkg`, `apt-get`, `systemctl` и `journalctl`, а откат копирует файлы поверх установки, так что `dpkg` продолжает считать установленной новую версию.

Ограничения: Raspberry Pi 3 с 1 ГБ (и 512 МБ на A+), web-сервер только на стандартной библиотеке Python, `/etc/exxpiano.conf` сохраняет валидацию и атомарную запись. В Alpine 3.24 есть `fluidsynth` 2.5.4 (community), но нет `py3-mido` и `python-rtmidi`.

## Goals / Non-Goals

**Goals:**

- Один образ Alpine 3.24 aarch64 в sys-режиме для Pi 3/4/5, собираемый из пакетов, без готового образа дистрибутива.
- Поведение для пользователя не меняется: звук, механика, web-интерфейс, setup-сеть, импорт банков, MIDI-плеер, QR-коды, консоль без входа и первый запуск без сети работают как раньше.
- Подписанные `.apk` и обновление с откатом, после которого `apk` и файлы на диске согласованы.
- Детерминированные автотесты на всё, что можно проверить без платы, включая сборку пакетов и установку, обновление и откат в контейнере Alpine.

**Non-Goals:**

- diskless/data-режим Alpine и `lbu`.
- Перенос установленных DietPi-устройств на месте. Миграция идёт перезаписью карты (см. «Миграция»).
- Собственный apk-репозиторий для онлайн-обновлений. Обновление по-прежнему доставляется `scripts/deploy-to-pi.sh`.
- Поддержка Raspberry Pi 2, 32-битных сборок и плат вне семейства Raspberry Pi.
- Замена `mido`/`rtmidi` собственной привязкой к ALSA sequencer.

## Decisions

### 1. Образ собирается из пакетов, без готового образа Alpine

Сборщик работает в Docker-контейнере `alpine:3.24` (на Linux возможен и прямой запуск от root). Порядок:

1. Создаётся пустой файл образа с MBR. Раздел 1 — FAT32 на 256 МиБ со смещением 4 МиБ, тип `0x0c`, флаг загрузки. Раздел 2 — ext4 на остаток.
2. `apk.static --root <mnt> --initdb --arch aarch64 --keys-dir alpine/keys --repositories-file alpine/repositories add ...` ставит базу: `alpine-base`, `linux-rpi`, `raspberrypi-bootloader`, `eudev`, `openssh`, `chrony`, `e2fsprogs-extra`, `sfdisk`, `logger` и пакеты проекта из локального подписанного репозитория.
3. Скрипты пакетов (`.post-install`, триггеры `mkinitfs`) выполняются в chroot через qemu-user (binfmt), так же как сейчас для arm64.
4. Загрузочный раздел получает файлы из `/boot` (ядро `vmlinuz-rpi`, `initramfs-rpi`, dtb, overlays и firmware `raspberrypi-bootloader`). `config.txt` и `cmdline.txt` генерируются: `arm_64bit=1`, `dtparam=audio=on`, `root=PARTUUID=<id>-02 rootfstype=ext4 rootwait modules=...`.

В репозитории закрепляются `alpine/repositories` (ветка `v3.24`, конкретное HTTPS-зеркало) и `alpine/keys/*.rsa.pub` (официальные ключи Alpine для aarch64). `apk` проверяет подпись `APKINDEX` и пакетов этими ключами, и при несовпадении сборка падает. Манифест записывает ветку, зеркало, SHA-256 ключей и полный список `apk info -v` образа.

*Альтернативы.* (а) Готовый `alpine-rpi-*.img.gz`/tarball плюс `setup-disk` на устройстве. Это потребовало бы первой загрузки с установкой, а tarball рассчитан на diskless. (б) `alpine-make-rpi-image` и похожие сторонние скрипты: лишняя внешняя зависимость за ту же логику. Свой сборщик на ~200 строк проще проверять, и он заменяет `sd-image-inner.sh`.

*Воспроизводимость.* Стабильная ветка Alpine заменяет пакеты исправленными сборками и не хранит старые версии, поэтому байтовая воспроизводимость между днями не гарантируется. Это осознанно, как и раньше для `apt` внутри образа DietPi. Прослеживаемость обеспечивает список версий в манифесте. Зеркалирование набора `.apk` в GitHub prerelease (аналог `dietpi-mirror`) остаётся возможным продолжением и в этот change не входит.

### 2. Один образ для Pi 3/4/5

С версии 3.19 `linux-rpi` aarch64 — одно ядро для BCM2837, BCM2711 и BCM2712, а `raspberrypi-bootloader` несёт firmware для всех трёх. `config.txt` получает общие настройки. Для Pi 5 используется секция `[pi5]`, если она нужна firmware: что именно нужно, проверяется на плате. `dtparam=audio=on` на Pi 5 ничего не делает. Аудиовыход выбирает `exxpiano-run`, как и сейчас. `--target` и `TARGETS` в `build-sd-image.py` удаляются. Банк `piano-lite.sf2` кладётся в единый образ всегда: на Pi 5 он просто не выбирается.

### 3. OpenRC и `supervise-daemon`

Каждая systemd unit получает свой init-скрипт `/etc/init.d/<имя>`:

| Unit | OpenRC | Ключевые настройки |
|---|---|---|
| `exxpiano` | `supervisor=supervise-daemon`, `respawn_delay=2`, `respawn_max=0` | `rc_ulimit="-r 95 -l unlimited"`, `need localmount`, `after alsasound udev-settle` |
| `exxpiano-fx` | то же | `need exxpiano`, `nicelevel=-5` |
| `exxpiano-web` | то же | `after exxpiano` |
| `exxpiano-wifi` | `start-stop-daemon --background` + обёртка `exxpiano-respawn --on-failure 5` | `after net`, `start_pre`: `checkpath -d -m 0700 /run/exxpiano-wifi` |
| `exxpiano-firstboot` | oneshot, `before exxpiano exxpiano-wifi` | см. п. 7 |

`supervise-daemon` перезапускает процесс при любом завершении. Wi-Fi supervisor штатно завершается с кодом 0, когда сетью владеет другой менеджер или `WIFI_SETUP=0`. Чтобы не было цикла перезапусков, его запускает маленькая Bash-обёртка `exxpiano-respawn`: она перезапускает дочерний процесс только при ненулевом коде и сама завершается с 0. Это повторяет `Restart=on-failure`.

**Окружение.** Init-скрипты OpenRC — это shell. Если сделать `source /etc/exxpiano.conf`, выполнятся `$(...)` и обратные кавычки из значений, а спецификация это запрещает. Поэтому каждый init-скрипт запускает программу через `/opt/exxpiano/exxpiano-env /etc/exxpiano.conf -- <команда>`. Это Python-обёртка, которая разбирает файл тем же `exxpianoconf.parse_conf` с семантикой systemd `EnvironmentFile` (кавычки, `\`-экранирование, комментарии), выставляет переменные и делает `exec`. `$FX_ARGS` разбивается на аргументы по пробелам, как в systemd. Так у валидатора, у web-сервера и у служб остаётся один разборщик.

**Журналирование.** `output_logger` и `error_logger` у `supervise-daemon` направляют stdout и stderr в `logger -t <служба> --prio-prefix -p daemon.info` (пакет `logger` из util-linux). Префиксы `<3>`/`<4>` дают тот же уровень, что `SyslogLevelPrefix` у journald, так что текущие сообщения не надо переписывать. Ядро в syslog пересылает `klogd` из busybox. В `/etc/conf.d/syslog` выставляется `SYSLOGD_OPTS="-t -s 1024 -b 2"`: журнал `/var/log/messages` ротируется и не съедает SD-карту.

### 4. Web-интерфейс: OpenRC и syslog вместо systemd и journald

`exxpiano-web.py` получает небольшой модуль `pi/oslib.py` (только stdlib), который web и Wi-Fi вызывают вместо прямых `systemctl` и `journalctl`:

- `service_state(name)`: `rc-service <name> status`. Коды выхода переводятся в `active`, `inactive`, `failed` (для `crashed`) и `unknown`.
- `restart(name)`: `setsid -f rc-service <name> restart`, неблокирующий аналог `--no-block`.
- `synth_pid()`: PID процесса `fluidsynth`, найденный по `/proc/*/comm` среди потомков supervisor из `/run/exxpiano.pid`. `wait_synth_ready` по-прежнему сравнивает старый и новый PID.
- `read_messages(boot_time)`: читает `/var/log/messages` и `.0` в формате busybox (`Mon dd HH:MM:SS host facility.prio tag[pid]: msg`) с флагом `-t`. Записи до момента загрузки (`btime` из `/proc/stat`) отбрасываются. Год берётся из текущей даты с переходом через новый год. `parse_journal_errors` заменяется на `parse_syslog_errors` с тем же контрактом: `{t, unit, level, text}`, дедупликация и лимит. Поиск OOM работает по строкам `kern.*` с `Out of memory: Killed process ... (fluidsynth)`.
- Чтение журнала службы для `audio output` (`journalctl -u exxpiano`) заменяется фильтром тех же записей по тегу `exxpiano`.

### 5. Устройства: eudev вместо mdev

В образ ставится `eudev`, `setup-devd udev` выполняется в chroot. Правила `90-exxpiano.rules` сохраняют совпадения, но меняют действия:

- Смена звуковой карты: `RUN+="/usr/bin/setsid -f /sbin/rc-service --ifstarted exxpiano restart"`. `--ifstarted` выполняет требование «остановленный вручную синтезатор не запускается».
- USB-накопитель: `RUN+="/usr/bin/setsid -f /opt/exxpiano/exxpiano-usb.py add /dev/%k"` на `add` и `remove` на `remove`. Шаблонная unit с `BindsTo` уходит, а `exxpiano-usb.py` уже идемпотентен по `add`/`remove`. Отсоединение нужно, потому что udev убивает долгие `RUN`.

*Альтернатива* — `mdev`, который в Alpine по умолчанию: нет `ID_BUS`, `ID_FS_USAGE` и `ID_PATH`, пришлось бы самим определять файловую систему и шину. `eudev` штатно поддерживается в Alpine и почти не требует правок правил.

### 6. Сеть: `ifupdown-ng` только для проводной сети, Wi-Fi целиком у supervisor

`/etc/network/interfaces` в образе содержит только `lo` и `eth0` (`inet dhcp`, `udhcpc`). Пакет больше не пишет stanza `wlan0` в `interfaces.d`. В клиентском режиме supervisor сам запускает `wpa_supplicant -B -i wlan0 -c /etc/wpa_supplicant/wpa_supplicant.conf -P /run/exxpiano-wifi/wpa.pid` и `udhcpc -i wlan0 -b -p /run/exxpiano-wifi/udhcpc.pid`, а останавливает их по PID-файлам. Управление `wpa_cli`, сканирование и режим точки доступа через `hostapd`/`dnsmasq` не меняются. Дополнительные проводные USB-адаптеры получают `udhcpc` вместо `dhclient`.

Так исчезает зависимость от синтаксиса `ifupdown` и `ifupdown-ng` (который отличается для `wpa-*`), а шаги «записать stanza, `ifup`, `ifdown --force`» становятся прямыми вызовами, которые проще подменять в тестах. В `dnsmasq_conf` меняется `group=nogroup` на `group=nobody`: в Alpine нет `nogroup`.

**Hostname.** Вместо `hostnamectl set-hostname` выполняются атомарная запись `/etc/hostname`, `hostname -F /etc/hostname`, правка строки `127.0.1.1` в `/etc/hosts` и `rc-service --ifstarted avahi-daemon restart`.

**Чужой сетевой менеджер.** `rc-service -e <svc>` и `status` для `networkmanager`, `iwd` и `connman`.

### 7. Первый запуск: `exxpiano-firstboot` вместо автоматизации DietPi

Oneshot-служба в runlevel `boot` с меткой `/var/lib/exxpiano/image-firstboot.done`:

1. Расширяет раздел 2 до конца карты (`sfdisk -N 2` с `,+`), затем `resize2fs`. При ошибке пишет в журнал и продолжает: устройство остаётся рабочим на исходном размере.
2. Выполняет `exxpiano-wifi.py ensure-name` (installation ID и hostname).
3. Выбирает `piano-lite.sf2` при `MemTotal < 800 МБ`. Логика переносится из `Automation_Custom_Script.image.sh` без изменений.
4. SSH-ключи хоста создаёт штатный `sshd` при первом старте. `PermitRootLogin prohibit-password`, если пароль при сборке не задан, иначе `yes`.

Консоль: в `/etc/inittab` строка `tty1::respawn:/sbin/getty ...` заменяется на `tty1::respawn:/usr/local/sbin/exxpiano-console`, а строки `tty2`–`tty6` удаляются. `exxpiano-console.sh` переносится из `dietpi/` в `pi/` без изменений логики.

### 8. Пакеты `.apk` и подпись

- `packaging/alpine/exxpiano/APKBUILD` (`arch="noarch"`): файлы раскладываются по тем же путям, что и в `build-deb.py`. Units заменяются init-скриптами, udev-правила ставятся в `/usr/lib/udev/rules.d`. `depends`: `fluidsynth alsa-utils python3 py3-mido py3-python-rtmidi hostapd dnsmasq iw wpa_supplicant eudev logger avahi` (`udhcpc` входит в busybox). `.post-install` и `.post-upgrade`: демо-MIDI, `ensure-name`, `rc-update add` и перезапуск служб, только если OpenRC запущен (`[ -e /run/openrc/softlevel ]`; в chroot образа его нет). `.pre-deinstall`: `rc-service stop` и `rc-update del`. `/etc/exxpiano.conf` защищён `apk` как файл в `/etc`, поэтому на изменённый файл ставится `.apk-new`.
- `packaging/alpine/exxpiano-soundfonts/APKBUILD`: банки, `depends="exxpiano"`.
- `packaging/alpine/py3-mido/APKBUILD` (noarch) и `packaging/alpine/py3-python-rtmidi/APKBUILD` (aarch64, `makedepends="alsa-lib-dev py3-gpep517 meson-python cython"`). Исходники берутся по закреплённым версиям и `sha512sums`.
- `scripts/build-apks.py` поднимает контейнер `alpine:3.24 --platform linux/arm64`, запускает `abuild -r` и кладёт результат в `dist/apk/aarch64/` с подписанным `APKINDEX.tar.gz`. Ключ берётся из `EXXPIANO_APK_KEY` (CI-секрет) или из `~/.abuild/`. Если ключа нет, создаётся временный dev-ключ, о чём выводится заметное предупреждение. Публичный ключ кладётся в образ в `/etc/apk/keys/`.
- `build-deb.py`, `packaging/debian/` и `test_deb.py` удаляются. Список файлов пакета проверяет `test_apk.py` (статический разбор APKBUILD и сравнение с `pi/`) плюс контейнерный тест в конвейере образа.

### 9. Обновление и откат через `apk`

`upgrade-on-pi.sh`:

1. `apk verify` пакета: подпись ключом из `/etc/apk/keys`. `--allow-untrusted` разрешён только при `EXXPIANO_DEV_UNTRUSTED=1`.
2. Проверка зависимостей через `apk add --simulate`.
3. Копирование текущего пакета из `/var/lib/exxpiano/packages/` (туда `.post-install` кладёт каждый установленный `.apk`; хранятся два последних) и `/etc/exxpiano.conf` в бэкап.
4. `apk add ./exxpiano-<новая>.apk`.
5. `rc-service exxpiano restart`, ожидание и `rc-service exxpiano status`.
6. Если службы не вернулись, `rollback-on-pi.sh` делает `apk add ./exxpiano-<прежняя>.apk` (явный файл ставит указанную версию, в том числе более старую), восстанавливает конфигурацию и перезапускает службы.

Сценарии «успех», «служба не поднялась», «пакет с чужой подписью» и «нет предыдущей версии» гоняются в контейнере `alpine:3.24` с фальшивыми службами без звука в `scripts/test-apk-upgrade.sh`, который запускается в `image-pipeline`.

### 10. Миграция существующих устройств

Обновить DietPi на Alpine на месте нельзя, нужна перезапись карты. Документация (README, BUILD-IMAGE) получает раздел о переносе: скопировать по SSH `/var/lib/exxpiano/` (installation ID, импортированные банки, профили, MIDI-библиотека) и `/etc/exxpiano.conf`, записать новый образ и вернуть файлы до первой загрузки, положив их на раздел `rootfs` карты с другого Linux, либо после неё через `scp` с последующим перезапуском служб. Сохранённые сети Wi-Fi переносятся вместе с `wpa_supplicant.conf`. Автоматизация переноса в этот change не входит.

### 11. CI

- `build.yml`: быстрые тесты без изменений, плюс `sh -n` и `openrc-run`-совместимая проверка init-скриптов.
- `image-pipeline.yml`: Docker-сборка `.apk` (qemu arm64), `test-apk-upgrade.sh`, сборка образа с заглушками банков и смоук-проверка чтением образа.
- `release.yml`: секрет `EXXPIANO_APK_KEY`, один ZIP, удаление шагов DietPi. `dietpi-mirror.yml` и `.github/dietpi-image.env` удаляются, `.github/alpine-image.env` хранит ветку, зеркало и SHA-256 ключей.

## Risks / Trade-offs

- [Пакеты стабильной ветки Alpine меняются без смены ветки] → версии фиксируются в манифесте. При регрессии можно временно закрепить конкретные версии через `apk add pkg=ver` в списке сборщика.
- [`py3-python-rtmidi` под musl собирается или работает иначе] → сборка пакета и тест `import rtmidi; rtmidi.MidiIn().get_ports()` в контейнере arm64. Проверка реальных ALSA-портов возможна только на Pi.
- [Задержка и производительность FluidSynth на musl ниже, чем на glibc] → FluidSynth выделяет голоса заранее и не зависит от скорости malloc в аудиопотоке. Тем не менее нужно сравнить на Pi 3 загрузку CPU и отсутствие xrun при `PERIOD_SIZE=64` и полифонии 192. При регрессии есть запасной вариант — `mimalloc2` через `LD_PRELOAD` для синтезатора.
- [Загрузка Pi 5 и Pi 4 с одним `config.txt` не проверена] → смоук-проверка образа сверяет наличие `kernel_2712`/dtb для всех трёх SoC. Сама загрузка — аппаратная проверка.
- [Формат busybox syslog без года и с ротацией] → парсер работает с текущим и одним предыдущим файлом, сдвиг года покрыт тестом. Записи, потерянные при ротации, теряются, и в UI это допустимо.
- [`apk add` старого файла может отказать в даунгрейде при конфликте зависимостей] → это отдельно проверяется контейнерным тестом. Если откат не удался, скрипт сообщает об ошибке и оставляет бэкап на месте.
- [eudev и OpenRC в Alpine хуже обкатаны на Raspberry Pi, чем systemd в DietPi] → горячее подключение аудио, накопителей и MIDI входит в обязательную аппаратную проверку до первого релиза на Alpine.
- [Незавершённые changes с дельтами на те же спецификации] → этот change архивируется первым или после явной сверки. `provision-wifi-over-bluetooth` пересматривается под OpenRC и пакеты Alpine (`bluez`, `py3-dbus`, `py3-gobject3`).
- [Пользователи DietPi теряют обновление на месте] → документированный ручной перенос данных (п. 10).

Аппаратная проверка на Raspberry Pi 3, 4 и 5 обязательна перед выпуском и в этом change не выполнена.
