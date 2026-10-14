## ADDED Requirements

### Requirement: Банк по объёму памяти платы

Образ SHALL содержать уменьшенный банк `piano-lite.sf2`, а первый запуск на плате с памятью меньше 800 МБ MUST выбрать его вместо `piano.sf2`.

#### Scenario: Плата с 512 МБ

- **WHEN** образ запускается впервые на плате, у которой `MemTotal` меньше 800 МБ
- **THEN** в `/etc/exxpiano.conf` значение `PIANO_SF2` указывает на `piano-lite.sf2`

#### Scenario: Плата с 1 ГБ

- **WHEN** образ запускается впервые на плате с 1 ГБ
- **THEN** `PIANO_SF2` остаётся `piano.sf2`
