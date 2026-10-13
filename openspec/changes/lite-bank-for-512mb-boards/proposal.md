# Proposal

## Why

На Raspberry Pi 3 Model A+ (512 МБ, около 414 МБ доступно) основной банк рояля (около 390 МБ на момент проблемы) не помещается в память: ядро убивает FluidSynth (`Out of memory: Killed process (fluidsynth)`), служба бесконечно перезапускается, звука нет. Страница при этом не показывала причину.

## What Changes

- Образ SHALL содержать, кроме `piano.sf2` (основной банк) и `mech.sf2`, уменьшенный банк `piano-lite.sf2` (пресет `lite`), если он собран; release-workflow собирает его всегда.
- При первом запуске на плате с памятью меньше 800 МБ образ SHALL переключить `PIANO_SF2` на `piano-lite.sf2`; на остальных платах остаётся `standard`.
- Проверка «банк помещается в память» на плате с памятью меньше 800 МБ SHALL оставлять системе 28 % памяти вместо 400 МБ; для плат побольше расчёт не меняется.
- Страница «Монитор» SHALL показывать убитые ядром из-за нехватки памяти программы вместе с подсказкой, что делать.
- Проверка на реальной плате (`lite` на Pi 3 A+ на слух и по памяти) остаётся невыполненной.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `provisioning-and-upgrades`: образ подбирает банк по объёму памяти платы.

## Impact

- `scripts/build-sd-image.py`, `scripts/sd-image-inner.sh`, `dietpi/Automation_Custom_Script.image.sh`, `pi/banklib.py`, `pi/exxpiano-web.py`, `.github/workflows/release.yml`; документация EN/RU.
- Образ вырастает примерно на 237 МБ.
