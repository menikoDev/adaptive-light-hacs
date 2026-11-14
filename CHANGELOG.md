# Changelog

Все заметные изменения проекта описаны в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии следуют [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

## [0.2.0] - 2026-11-14

### Added
- Интеграция Home Assistant с мастером настройки и options flow.
- Несколько зон, группы ламп (общие настройки и действия), лампы произвольного количества.
- Наследование параметров: глобально, зона, группа, лампа.
- Контроллер с шагом 10 %, пауза после ручного изменения, режим shadow/apply.
- Выключатель `light_<лампа>_auto` для каждой лампы, общий выключатель сценариев.
- Исполнители сценариев (кино, проветривание, холодильник, вечерний и ночной свет, стол).
- Калибровка `k` лампы и `dark_lux_offset` ночью, `open_k` днём.
- Сервисы: `calibrate`, `calibrate_apply`, `calibrate_cancel`, `calibrate_open_k`, `set_param`, `controller_tick`, `openness_reset`, `clear_pauses`.
- Сохранение состояния (паузы, openness, кандидаты калибровки) между перезапусками.
- Переводы ru/en, YAML панели управления, инструкция по миграции с YAML-плана.

[Unreleased]: https://github.com/menikoDev/adaptive-light-hacs/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/menikoDev/adaptive-light-hacs/releases/tag/v0.2.0
