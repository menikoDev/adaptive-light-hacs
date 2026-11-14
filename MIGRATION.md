# Миграция с YAML-плана на интеграцию

Интеграция заменяет пакет `adaptive_light.yaml`, шаги 4.2, 5.2-5.12, 6.1, 6.2 плана и скрипты калибровки. Автоматизация 5.1 «Light: set home mode» остаётся вашей: интеграция читает режим из `input_select.living_room_light_mode`.

**Нельзя запускать оба варианта одновременно:** обе системы двигают лампы и будут конфликтовать (удвоенные шаги, взаимные паузы). Порядок ниже это учитывает.

## Соответствие сущностей

Для основной зоны (пресет «гостиная») имена сохранены там, где это возможно.

| YAML-план | Интеграция | Примечание |
|---|---|---|
| `sensor.daylight_factor` | то же | |
| `sensor.living_room_ambient_lux` | то же | |
| `sensor.light_need`, `sensor.light_ambient_trust`, `sensor.light_openness`, `sensor.light_openness_raw`, `binary_sensor.light_evening_quiet` | то же | у второй и следующих зон префикс `<zone>_` |
| `sensor.light_target_<лампа>` | то же | |
| `input_boolean.light_adaptive_enabled` / `light_adaptive_apply` | `switch.light_adaptive_enabled` / `switch.light_adaptive_apply` | домен input_boolean -> switch |
| `input_boolean.light_<лампа>_auto` | `switch.light_<лампа>_auto` | домен input_boolean -> switch |
| `input_number.light_k_<лампа>` | `number.light_k_<лампа>` | домен input_number -> number |
| `input_datetime.light_<лампа>_pause_until` | `sensor.light_<лампа>_pause_until` (timestamp) | только чтение; сброс службой `clear_pauses` |
| `input_datetime.light_adaptive_last_set` | внутреннее | не сущность |
| `input_number.light_override_minutes`, `light_target_lux_*`, `light_w_*`, `light_evening_start_hour`, `light_need_blind` | параметры (глобально/зона/группа/лампа) | в мастере и настройках; изменение из автоматизации: `adaptive_light.set_param` |
| `input_number.light_<лампа>_min/max/gain/evening_mult/w_out/w_in` | параметры лампы `min`, `max`, `gain`, `evening_mult`, `w_out`, `w_in` | `-1` у `w_out/w_in` = наследовать |
| `input_number.light_desk_scene_work/bright/soft` | параметры `scene_work/bright/soft` (множители); применяются к лампам с `scene_scaled` | у пресета это desk и stick |
| `input_number.light_dark_lux_offset`, `light_blind_lux_floor`, `light_open_k`, `light_open_min_daylight`, `light_open_hyst`, `light_open_confirm` | параметры зоны | `dark_lux_offset` и `open_k` пишет калибровка |
| `input_number.light_calibrate_result` | `sensor.light_calibration` (атрибут `candidates`) | |
| `script.light_calibrate` | `adaptive_light.calibrate` + `calibrate_apply` | работает в фоне, не блокирует вызов |
| `script.light_open_calibrate` | `adaptive_light.calibrate_open_k` | |
| `script.light_adaptive_set_defaults` | не нужен | значения по умолчанию заданы в коде |
| автоматизации «Light: adaptive controller», «Light: adaptive manual override» | встроены (контроллер и перехват ручных изменений) | |
| автоматизации 5.2-5.12 | сценарии поведения (`switch.light_adaptive_scenarios`) | нужны привязки зоны, см. README |

Другие ваши автоматизации и дашборды, которые читают старые `input_boolean.light_*_auto` и `input_number.light_k_*`, нужно перенаправить на новые домены.

## Порядок перехода

1. Запишите текущие значения: `light_k_*`, `light_dark_lux_offset`, `light_open_k`, `light_override_minutes`, `light_*_min/max/gain/...`, множители сцен, состояние `*_auto`.
2. Выключите в YAML всё, что двигает лампы: `automation.turn_off` для 5.2-5.12, контроллера и перехвата (или уберите пакет и автоматизации и перезагрузите конфигурацию). Лампы больше никто не ведёт.
3. Установите интеграцию (README), в мастере выберите пресет «гостиная из YAML-плана». Проверьте entity_id лампы, датчиков и `input_select` из пресета (в пресете имена из вашего конфига: `light.living_room_top_lamp_*`, `light.work_station_*`, `sensor.living_room_presence_sensor_light_sensor_light_level` и т. д.); если у вас другие, поправьте в Настройки, зона, датчики и лампы.
4. Перенесите значения из шага 1: `k` лампы через `number.light_k_<лампа>`, остальное в параметрах (Настройки, лампа или зона). Включите `switch.light_<лампа>_auto` нужных ламп.
5. Режим наблюдения: `light_adaptive_enabled` on, `light_adaptive_apply` off, сценарии (`light_adaptive_scenarios`) выключены. Сравните `sensor.light_target_*` и записи logbook с тем, что было в YAML.
6. Включите `light_adaptive_scenarios`, затем `light_adaptive_apply`.
7. Удалите YAML-пакет, автоматизации, скрипты и хелперы плана, которые больше не нужны.

Откат: выключите `switch.light_adaptive_enabled` и `switch.light_adaptive_scenarios`, верните YAML.

## Что меняется в поведении

- Состояние (паузы, openness, переключатели) хранится в Store интеграции, а не в хелперах: переживает рестарт, но не виден в списке хелперов.
- Сценарии вместо автоматизаций: нет отдельных `automation.*`, поэтому «выключить один сценарий» делается привязкой зоны (пусто = сценарий выключен) или общим `switch.light_adaptive_scenarios`.
- Группы теперь ваши (`top`, `desk_group`, `wake` в пресете), а не жёстко зашитые списки ламп.
- Зона может быть без датчика освещённости (с 0.3.0). В YAML-плане датчик был обязателен; в пресете «гостиная» он задан, поведение пресета не изменилось. Подробности, ограничения и поведение калибровки: README, «Зона без датчика освещённости».
