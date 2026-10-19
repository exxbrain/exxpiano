## REMOVED Requirements

### Requirement: Совместимость с сетевым менеджером

**Reason**: Требование было сформулировано для ifupdown и Raspberry Pi OS с NetworkManager, а поддержка Debian и Raspberry Pi OS удаляется.
**Migration**: Заменяется требованием «Невмешательство в чужой сетевой менеджер» для Alpine (NetworkManager, iwd, connman).

## ADDED Requirements

### Requirement: Невмешательство в чужой сетевой менеджер

Система MUST NOT управлять беспроводным интерфейсом, если активен другой сетевой менеджер, который владеет Wi-Fi (NetworkManager, iwd или connman). В штатном образе Wi-Fi supervisor SHALL быть единственным владельцем `wlan0`, а проводные интерфейсы SHALL настраиваться стандартной сетевой службой системы.

#### Scenario: Активен сторонний сетевой менеджер

- **WHEN** на устройстве запущена служба NetworkManager, iwd или connman
- **THEN** Wi-Fi supervisor завершает работу без изменения сетевой конфигурации и веб-интерфейс объясняет недоступность функции

#### Scenario: Штатный образ

- **WHEN** устройство запущено из поставляемого образа
- **THEN** Wi-Fi supervisor управляет `wlan0`, а системная сетевая служба не поднимает `wlan0` сама
