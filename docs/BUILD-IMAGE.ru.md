# Сборка универсального образа SD-карты

Поставляемый артефакт — полный загрузочный образ для чистой SD-карты Raspberry Pi 2/3/4 ARMv8. В нём уже находятся DietPi, exxpiano, стандартное фортепиано и механика. Один ZIP можно записывать на несколько карт: serial платы при сборке и настройке не вводится.

## Подготовка

Нужны Docker Desktop на macOS либо Linux с Docker, официальный `DietPi_RPi234-ARMv8-Trixie.img.xz` и банки:

```bash
scripts/make-soundfonts.sh -p standard -o build
```

По умолчанию сборщик использует `build/piano-standard.sf2` и `build/mech.sf2`. Другие проверенные банки можно передать через `--piano` и `--mech`; образ без любого из двух банков не выпускается.

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
- `manifest.json` — версии, SHA-256 основы и результата, банки и схема идентичности;
- `README.txt` — краткая инструкция для конкретного выпуска.

Версия продукта берётся из `VERSION`, версия DietPi — из `dietpi/.version` базового образа. Если версия DietPi не читается, обязательный банк отсутствует или smoke-проверка завершается ошибкой, готовый ZIP не считается выпущенным.

## Запись и первый запуск

1. В Raspberry Pi Imager выберите «Choose OS» → «Use custom» и укажите ZIP либо выберите ZIP в balenaEtcher.
2. Запишите чистую SD-карту, вставьте её в Raspberry Pi 2, 3 или 4 и включите питание.
3. Первый запуск занимает несколько минут и не требует интернета, Ethernet, сохранённой Wi-Fi или ввода serial.
4. Установка сама создаёт постоянный случайный ID из 16 шестнадцатеричных символов в `/var/lib/exxpiano/installation-id`.
5. Из ID получаются уникальные сеть `exxpiano-Setup-<ID>` и hostname `exxpiano-<id-в-нижнем-регистре>`. Подключитесь к setup-сети и откройте `http://10.42.0.1:8080/`; точные ID, имя сети и QR-коды показаны в web-интерфейсе.

Клонированные из одного ZIP карты получают разные ID при своём первом запуске. ID создаётся атомарно и затем не меняется, поэтому перезапуск не меняет Wi-Fi или hostname.

## Проверка

Для обычных изменений запускайте быстрые тесты:

```bash
python3 -m unittest tests.test_build_sd_image tests.test_wifilib tests.test_exxpiano_wifi tests.test_web tests.test_demo_web
```

Полный Docker-конвейер и формирование образа запускаются только при изменениях самого конвейера или перед выпуском. Контейнерный smoke test не заменяет проверку на настоящих Raspberry Pi 2, 3 и 4. На устройстве проверьте `/var/log/exxpiano-firstboot.log`, появление уникальной setup-сети, web-интерфейс, MIDI, звук и повторный автономный запуск.
