# Сборка универсального образа SD-карты

Поставляемый артефакт — полный загрузочный образ для чистой SD-карты Raspberry Pi 2/3/4 ARMv8. В нём уже находятся DietPi, exxpiano, стандартное фортепиано и механика. Один ZIP можно записывать на несколько карт.

## Подготовка

Нужны Docker Desktop на macOS либо Linux с Docker, официальный `DietPi_RPi234-ARMv8-Trixie.img.xz` и банки:

```bash
scripts/make-soundfonts.sh -p full -o build
scripts/make-soundfonts.sh -p lite -o build
```

По умолчанию сборщик использует `build/piano-full.sf2` и `build/mech.sf2`. Другие банки передаются через `--piano` и `--mech`.

**Цель.** `--target rpi234` (по умолчанию) или `--target rpi5`. Для Pi 5 передайте образ DietPi для Pi 5 (`--dietpi DietPi_RPi5-ARMv8-Trixie.img.xz` или `--download`). Цель определяет имена (`exxpiano-rpi5-v...-sd.zip`, `manifest-rpi5.json`, `README-rpi5.txt`) и две вещи внутри: в образе для Pi 5 нет банка `lite` (у платы 2 ГБ и больше), и он не трогает настройки встроенного звука, потому что **у Pi 5 нет разъёма 3,5 мм: звук идёт только через USB-звуковую карту или аудиоинтерфейс**. Ни образ для Pi 5, ни его первый запуск на реальной Pi 5 не проверялись.

Перед тегом релиза добавьте в `CHANGELOG.md` раздел `## <VERSION>`: workflow добавляет его в описание GitHub Release (`scripts/release-notes.py`) и останавливается, если раздела нет. Образ без любого из двух банков не выпускается. Если есть `build/piano-lite.sf2` (`scripts/make-soundfonts.sh -p lite` или `--piano-lite ФАЙЛ`), он тоже попадает в образ, и первый запуск на плате с памятью меньше 800 МБ (Pi 3 A+) использует его; release-workflow всегда собирает его.

## Сборка

Пароли лучше передавать через окружение, чтобы они не попадали в список процессов:

```bash
printf 'Пароль DietPi: '; read -r -s SD_PASSWORD; printf '\n'
printf 'Пароль сети настройки: '; read -r -s SD_AP_PASSWORD; printf '\n'
export SD_PASSWORD SD_AP_PASSWORD

python3 scripts/build-sd-image.py \
  --dietpi ~/Downloads/DietPi_RPi234-ARMv8-Trixie.img.xz \
  --country RU --timezone Europe/Moscow --dry-run

python3 scripts/build-sd-image.py \
  --dietpi ~/Downloads/DietPi_RPi234-ARMv8-Trixie.img.xz \
  --country RU --timezone Europe/Moscow

unset SD_PASSWORD SD_AP_PASSWORD
```

`--dry-run` только проверяет входные файлы и показывает план. Полная сборка создаёт в `dist/sd/`:

- `exxpiano-rpi234-v<VERSION>-dietpi-<DIETPI_VERSION>-sd.zip` — образ для записи;
- `manifest-<цель>.json` — версии, SHA-256 основы и результата, банки и схема идентичности;
- `README-<цель>.txt` — краткая инструкция для конкретного выпуска.

Версия продукта берётся из `VERSION`, версия DietPi — из `dietpi/.version` базового образа. Если версия DietPi не читается, обязательный банк отсутствует или smoke-проверка завершается ошибкой, готовый ZIP не считается выпущенным.

> **Пароль setup-сети.** Без `SD_AP_PASSWORD` в образе остаётся `exxpianosetup`, одинаковый у всех карт из него; имя сети уникально для каждой карты (оно заканчивается ID установки). С `SD_AP_PASSWORD` у всех карт этого образа ваш общий пароль. На одном устройстве его меняет `WIFI_AP_PASSWORD` в `/etc/exxpiano.conf`.

## GitHub Release

Публикация тега `v<VERSION>` запускает `.github/workflows/release.yml`. Workflow проверяет точное совпадение тега с `VERSION`, запускает быстрые тесты, собирает full- и lite-банки, скачивает закреплённую основу DietPi RPi 2/3/4 ARMv8 из отдельного prerelease этого репозитория, проверяет SHA-256 из `.github/dietpi-image.env`, собирает и проверяет образ, а затем создаёт продуктовый GitHub Release. Поэтому выпуск не зависит от доступности upstream-сервиса скачивания, но использует точную копию официального входного образа. К продуктовому выпуску прикладываются:

- `exxpiano-rpi234-v<VERSION>-dietpi-<DIETPI_VERSION>-sd.zip`;
- `manifest-rpi234.json`, `manifest-rpi5.json`;
- `README-rpi234.txt`, `README-rpi5.txt`;
- `SHA256SUMS`.

Заголовок релиза содержит название exxpiano, версию и семейство поддерживаемых Raspberry Pi. Английское описание объясняет состав образа, запись и первый запуск, проверку контрольных сумм и запрет клонирования уже запускавшейся карты.

Если любой предыдущий шаг завершился ошибкой, релиз не создаётся. У существующего релиза обновляются название и описание и добавляются недостающие файлы; файл с уже существующим именем никогда не перезаписывается (загрузка завершится ошибкой, решение за оператором).

Prerelease с исходным DietPi не является продуктовым выпуском. Его тег содержит первые восемь символов проверенного upstream SHA-256, а единственный бинарный asset — неизменённый официальный образ, указанный в `.github/dietpi-image.env`. Для обновления DietPi создаются новый prerelease, тег и checksum; существующая закреплённая основа не заменяется. Локальная команда `--download` сохраняется и передаёт upstream-сервису идентифицирующий HTTP User-Agent.

Обычные push и pull request запускают только быстрые тесты. Полный конвейер с образом-заменителем запускается отдельно при изменении сборщика образа, настройки DietPi, упаковки или соответствующего workflow; его также можно запустить вручную в GitHub Actions.

## Запись и первый запуск

1. В Raspberry Pi Imager выберите «Choose OS» → «Use custom» и укажите ZIP либо выберите ZIP в balenaEtcher.
2. Запишите чистую SD-карту, вставьте её в Raspberry Pi 2, 3 или 4 и включите питание.
3. Первый запуск занимает несколько минут и не требует интернета, Ethernet или сохранённой Wi-Fi.
4. Установка сама создаёт постоянный случайный ID из 16 шестнадцатеричных символов в `/var/lib/exxpiano/installation-id`.
5. Из ID получаются уникальные сеть `exxpiano-Setup-<ID>` и hostname `exxpiano-<id-в-нижнем-регистре>`. Подключитесь к setup-сети и откройте `http://10.42.0.1:8080/`; точные ID, имя сети и QR-коды показаны в web-интерфейсе.

Клонированные из одного ZIP карты получают разные ID при своём первом запуске. ID создаётся атомарно и затем не меняется, поэтому перезапуск не меняет Wi-Fi или hostname.

## Проверка

Для обычных изменений запускайте быстрые тесты:

```bash
python3 -m unittest tests.test_build_sd_image tests.test_wifilib tests.test_exxpiano_wifi tests.test_web tests.test_demo_web
```

Полный Docker-конвейер и формирование образа запускаются только при изменениях самого конвейера или перед выпуском; GitHub Actions автоматически соблюдает это разделение. Контейнерный smoke test не заменяет проверку на настоящих Raspberry Pi 2, 3 и 4. На устройстве проверьте `/var/log/exxpiano-firstboot.log`, появление уникальной setup-сети, web-интерфейс, MIDI, звук и повторный автономный запуск.
