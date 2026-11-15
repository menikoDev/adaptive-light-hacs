"""Generates strings.json and translations/{en,ru}.json from one table (run: python tools/gen_strings.py)."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "adaptive_light"
sys.path.insert(0, str(ROOT.parent))

PARAM = {  # name: (en, ru)
    "target_lux_day": ("Target lux, day", "Целевая освещённость днём, лк"),
    "target_lux_evening": ("Target lux, evening", "Целевая освещённость вечером, лк"),
    "w_outdoor": ("Weight of outdoor light", "Вес уличного света"),
    "w_indoor": ("Weight of indoor light", "Вес комнатного света"),
    "evening_start_hour": ("Evening starts at (hour)", "Начало вечера (час)"),
    "scene_work": ("Scene multiplier: work", "Множитель сцены: работа"),
    "scene_bright": ("Scene multiplier: bright", "Множитель сцены: ярко"),
    "scene_soft": ("Scene multiplier: soft", "Множитель сцены: мягко"),
    "need_blind": ("Need when blinded (no trust in lux)", "Потребность без доверия к люксам, %"),
    "override_minutes": ("Manual override pause, minutes", "Пауза после ручного изменения, мин"),
    "mode_entity": ("Mode entity (input_select with Day/Evening/Night/Movie)", "Сущность режима (input_select: Day/Evening/Night/Movie)"),
    "scene_entity": ("Scene entity (Work/Bright/Soft)", "Сущность сцены (Work/Bright/Soft)"),
    "min": ("Minimum brightness, %", "Минимальная яркость, %"),
    "max": ("Maximum brightness, %", "Максимальная яркость, %"),
    "gain": ("Gain", "Усиление"),
    "evening_mult": ("Evening multiplier", "Вечерний множитель"),
    "w_out": ("Lamp weight outdoor (-1 = inherit)", "Вес улицы для лампы (-1 = наследовать)"),
    "w_in": ("Lamp weight indoor (-1 = inherit)", "Вес комнаты для лампы (-1 = наследовать)"),
    "scene_scaled": ("Scale by scene", "Масштабировать сценой"),
    "work_hours_only": ("Switch on only in work hours", "Включать только в рабочие часы"),
    "legacy_ratio": ("Legacy ratio (desk scenes)", "Коэффициент старой сцены"),
    "ctrl_modes": ("Controller works in modes (comma separated)", "Режимы работы контроллера (через запятую)"),
    "fridge_block": ("Do not adjust while fridge zone is active", "Не менять при активной зоне холодильника"),
    "calib_levels": ("Calibration levels (3 numbers)", "Уровни калибровки (3 числа)"),
    "k": ("Lux per 1% (set by calibration)", "Люксов на 1% (задаёт калибровка)"),
    "dark_lux_offset": ("Dark lux offset", "Тёмный фон, лк"),
    "blind_lux_floor": ("Blind lux floor", "Порог слепоты по люксам"),
    "open_k": ("Open-window k", "k открытости окна"),
    "open_min_daylight": ("Min daylight for openness", "Мин. дневной свет для открытости"),
    "open_hyst": ("Openness hysteresis", "Гистерезис открытости"),
    "open_confirm": ("Openness confirm ticks", "Тиков подтверждения открытости"),
    "radiation_full_scale": ("Radiation for 100% daylight, W/m2", "Радиация для 100% дневного света, Вт/м²"),
    "step_pct": ("Controller step, %", "Шаг контроллера, %"),
    "threshold_pct": ("Controller threshold, %", "Порог контроллера, %"),
    "transition_s": ("Transition, s", "Плавность перехода, с"),
}
ROLE = {
    "radiation": ("Outdoor radiation sensor", "Датчик уличной радиации"),
    "elevation": ("Sun elevation sensor (daylight source)", "Высота солнца (источник дневного света)"),
    "illuminance": ("Illuminance sensor", "Датчик освещённости"),
    "presence": ("Presence sensors", "Датчики присутствия"),
    "media": ("TV / monitors (calibration warning)", "ТВ / мониторы (предупреждение при калибровке)"),
    "sofa": ("Sofa sensor", "Датчик дивана"), "table": ("Table sensor", "Датчик стола"),
    "fridge": ("Fridge zone sensor", "Датчик зоны холодильника"), "entrance": ("Entrance sensor", "Датчик входа"),
    "carpet": ("Carpet sensor", "Датчик ковра"), "window_advice": ("Window advice sensor", "Подсказка по окну"),
    "window_left": ("Left window sensor", "Левое окно"), "window_right": ("Right window sensor", "Правое окно"),
    "vent_mode": ("Ventilation mode switch", "Переключатель режима проветривания"),
    "color_store": ("Colour store (input_text)", "Хранилище цвета (input_text)"),
}
SLOT = {
    "top_group": ("Top lights group", "Группа верхнего света"), "desk_group": ("Desk lights group", "Группа настольных ламп"),
    "wake_group": ("Night wake group", "Группа ночной подсветки"), "fridge_lamp": ("Fridge lamp", "Лампа у холодильника"),
    "vent_left": ("Ventilation blink, left lamp", "Проветривание: левая лампа"),
    "vent_right": ("Ventilation blink, right lamp", "Проветривание: правая лампа"), "green_lamp": ("Green fill lamp", "Лампа зелёной подсветки"),
}
ERR = {
    "bad_key": ("Invalid key", "Недопустимый ключ"), "duplicate_key": ("Duplicate key", "Повторяющийся ключ"),
    "duplicate_lamp_entity": ("The same light is used twice", "Одна лампа указана дважды"),
    "unknown_zone": ("Unknown zone", "Неизвестная зона"), "unknown_lamp": ("Unknown lamp", "Неизвестная лампа"),
    "lamp_zone_mismatch": ("A group mixes lamps of different zones", "В группе лампы из разных зон"),
    "bad_role": ("Unknown sensor role", "Неизвестная роль датчика"), "role_zone_mismatch": ("Sensor role does not fit its zone", "Роль датчика не подходит зоне"),
    "bad_sensor_domain": ("The entity type does not fit the role", "Тип сущности не подходит роли"),
    "zone_needs_name": ("Enter a zone name", "Введите название зоны"),
    "bad_binding": ("Invalid behaviour binding", "Некорректная привязка поведения"), "no_zones": ("Add at least one zone", "Добавьте хотя бы одну зону"),
    "group_needs_lamps": ("Pick at least one lamp", "Выберите хотя бы одну лампу"),
    "bad_detect_rule": ("Invalid detection rule", "Некорректное правило по детекции"),
    "detect_needs_lamps": ("Pick at least one lamp of this zone", "Выберите хотя бы одну лампу этой зоны"),
    "detect_needs_sensor": ("Pick the zone presence or one binary sensor", "Выберите присутствие зоны или один бинарный датчик"),
    "detect_needs_brightness": ("Pick adaptive, or a fixed brightness", "Выберите адаптивную яркость или укажите фиксированную"),
    "detect_no_fridge_sensor": ("Add the fridge sensor first (extra sensors)", "Сначала укажите датчик холодильника (доп. датчики)"),
}
UI = {  # key: (en, ru)
    "title": ("Adaptive Light", "Адаптивный свет"),
    "user": ("Set up", "Настройка", "Build the configuration step by step or start from the preset of the YAML plan (living room). Progress is saved after every step, so closing this window by accident does not lose it.\n\n{draft}",
             "Соберите конфигурацию по шагам или начните с пресета YAML-плана (гостиная). Прогресс сохраняется после каждого шага: случайно закрытое окно его не теряет.\n\n{draft}"),
    "global_sensors": ("Outdoor light", "Уличный свет", "Optional sensors that tell how bright it is outside. Without them the integration cannot compute the daylight factor.",
                       "Необязательные датчики уличного света. Без них дневной свет не посчитать."),
    "zone": ("New zone", "Новая зона", "A zone is one room. Pick one of your Home Assistant areas to prefill its name, key, lamps and sensors, or leave the field empty for a custom zone.",
             "Зона — это комната. Выберите одну из областей (Areas) Home Assistant: название, ключ, лампы и датчики подставятся сами. Или оставьте поле пустым для своей зоны."),
    "new_zone": ("New zone", "Новая зона", "A zone is one room. Pick one of your Home Assistant areas to prefill its name, key, lamps and sensors, or leave the field empty for a custom zone.",
                 "Зона — это комната. Выберите одну из областей (Areas) Home Assistant: название, ключ, лампы и датчики подставятся сами. Или оставьте поле пустым для своей зоны."),
    "zone_name": ("Name of the zone", "Название зоны", "Area: {area}. The name is shown in Home Assistant; change it if you like. The key is a short latin id used in entity ids and settings; leave it empty to derive it from the name.",
                  "Область: {area}. Название видно в Home Assistant, его можно изменить. Ключ — короткий латинский id для сущностей и настроек; пустой = получить из названия."),
    "zone_sensors": ("Sensors of {zone}", "Датчики зоны {zone}", "All optional. Without an illuminance sensor the zone uses a daylight-only estimate and cannot be calibrated. Sensors found in the area are prefilled. Several presence sensors are allowed: the zone is occupied if any of them is on. Presence and TV / monitors never block the calibration: they only produce a warning, because people, a running TV and daylight distort the result. While a calibration runs, do not turn the lamps on or off and do not change them by hand.",
                     "Все необязательные. Без датчика освещённости зона считает свет по дневному свету и не калибруется. Датчики из области подставлены. Датчиков присутствия может быть несколько: зона занята, если включён любой из них. Присутствие и ТВ / мониторы калибровку не блокируют, а только дают предупреждение: люди, работающий ТВ и дневной свет искажают результат. Пока идёт калибровка, не включайте и не выключайте лампы и не меняйте их вручную."),
    "zone_extra_sensors": ("Extra sensors of {zone}", "Дополнительные датчики {zone}", "Only for the behaviour scenarios (fridge, sofa, windows ...). Leave empty if unused.",
                           "Только для сценариев поведения (холодильник, диван, окна ...). Оставьте пустым, если не нужно."),
    "zone_lamps": ("Lamps of {zone}", "Лампы зоны {zone}", "Pick the light entities of this zone (lights of the area are prefilled). Each becomes a lamp with its own auto switch.",
                   "Выберите лампы этой зоны (лампы области подставлены). У каждой будет свой переключатель авто."),
    "group_add": ("New lamp group in {zone}", "Новая группа ламп в {zone}", "A group shares settings and can be used by scenarios. A lamp may be in several groups.",
                  "Группа задаёт общие настройки и используется сценариями. Лампа может входить в несколько групп."),
    "group_edit": ("Group {group}", "Группа {group}", "", ""),
    "params": ("Parameters ({level}: {target})", "Параметры ({level}: {target})", "Empty field = inherit from the level above (global, zone, group, lamp).",
               "Пустое поле = наследовать от уровня выше (глобально, зона, группа, лампа)."),
    "zone_bindings": ("Behaviour of {zone}", "Поведение зоны {zone}", "Which groups and lamps the scenarios use. Choose Off to remove a binding. An empty field means the same: that scenario is off.",
                      "Какие группы и лампы использовать в сценариях. «Выкл» снимает привязку. Пустое поле значит то же самое: сценарий выключен."),
    "zone_detect": ("On detection in {zone}", "По детекции: {zone}",
                    "Rules turn lamps on when a sensor trips and, after a delay, off when it clears. An empty list does nothing. The two templates are optional: they only fill the form, nothing is saved until you submit it.\n\n{rules}",
                    "Правила включают лампы, когда срабатывает датчик, и выключают их через заданную задержку после того, как датчик погас. Пустой список ничего не делает. Два шаблона необязательны: они только заполняют форму, пока вы её не сохраните, ничего не записывается.\n\n{rules}"),
    "detect_add": ("Detection rule in {zone}", "Правило по детекции в {zone}",
                   "One trigger, the lamps, and when to turn them off. Adaptive turns the lamps on and leaves the brightness to the controller. Fixed asks for a brightness and, if you want, a colour temperature. Off delay 0 does not turn the lamps off. A fridge sensor at 30%, 4500 K and 10 s still runs the fridge scenario.",
                   "Один триггер, лампы и когда их выключить. Адаптивная включает лампы и отдаёт яркость контроллеру. Фиксированная спрашивает яркость и, если нужно, цветовую температуру. Задержка 0 лампы не выключает. Датчик холодильника с 30%, 4500 K и 10 с по-прежнему запускает сценарий холодильника."),
    "detect_delete": ("Delete a detection rule in {zone}", "Удалить правило по детекции в {zone}",
                      "The chosen rule is removed. The others stay.",
                      "Выбранное правило будет удалено. Остальные останутся."),
    "pick_zone": ("Pick a zone", "Выберите зону", "", ""), "pick_lamp": ("Pick a lamp", "Выберите лампу", "", ""), "pick_group": ("Pick a group", "Выберите группу", "", ""),
    "zone_remove": ("Remove {zone}?", "Удалить {zone}?", "Its lamps, groups and sensors are removed from the configuration.", "Лампы, группы и датчики зоны будут удалены из конфигурации."),
    "lamp_remove": ("Remove {lamp}?", "Удалить {lamp}?", "", ""), "group_remove": ("Remove {group}?", "Удалить {group}?", "", ""),
    "save": ("Review", "Проверка", "{summary}\n\n{detail}", "{summary}\n\n{detail}"),
}
MENU = {  # step: {option: (en, ru)}
    "user": {"global_sensors": ("Step-by-step wizard", "Пошаговый мастер"), "preset_plan": ("Preset: living room of the YAML plan", "Пресет: гостиная из YAML-плана"),
             "resume_draft": ("Continue the unfinished setup", "Продолжить незавершённую настройку"),
             "discard_draft": ("Discard the unfinished setup", "Удалить незавершённую настройку")},
    "groups_menu": {"group_add": ("Add a lamp group", "Добавить группу ламп"),
                    "zone_detect": ("On detection", "По детекции"), "more_zones": ("Continue", "Дальше")},
    "more_zones": {"zone": ("Add another zone", "Добавить ещё зону"), "save": ("Finish", "Готово")},
    "init": {"global_sensors": ("Outdoor sensors", "Уличные датчики"), "global_params": ("Global parameters", "Глобальные параметры"),
             "pick_zone": ("Edit a zone", "Изменить зону"), "new_zone": ("Add a zone", "Добавить зону"), "pick_lamp": ("Edit a lamp", "Изменить лампу"),
             "pick_group": ("Edit a group", "Изменить группу"), "save": ("Save and apply", "Сохранить и применить")},
    "zone_menu": {"zone_sensors": ("Sensors", "Датчики"), "zone_extra_sensors": ("Extra sensors", "Доп. датчики"), "zone_lamps": ("Add lamps", "Добавить лампы"),
                  "group_add": ("Add a group", "Добавить группу"), "zone_params": ("Zone parameters", "Параметры зоны"),
                  "zone_bindings": ("Behaviour scenarios", "Сценарии поведения"),
                  "zone_detect": ("On detection", "По детекции"),
                  "zone_remove": ("Remove zone", "Удалить зону"), "init": ("Back", "Назад")},
    "zone_detect": {
        "detect_add": ("Add a rule", "Добавить правило"),
        "detect_tpl_room": ("Template: room presence → main group, adaptive, off 10 min after leave",
                            "Шаблон: присутствие комнаты → основная группа, адаптивно, выкл через 10 мин"),
        "detect_tpl_fridge": ("Template: fridge sensor → one lamp, fixed 30% at 4500 K, off after 10 s",
                              "Шаблон: датчик холодильника → одна лампа, 30% и 4500 K, выкл через 10 с"),
        "detect_delete": ("Delete a rule", "Удалить правило"),
        "zone_menu": ("Back", "Назад"),
        "groups_menu": ("Back", "Назад"),
    },
    "lamp_menu": {"lamp_params": ("Lamp parameters", "Параметры лампы"), "lamp_remove": ("Remove lamp", "Удалить лампу"), "init": ("Back", "Назад")},
    "group_menu": {"group_edit": ("Name and lamps", "Название и лампы"), "group_params": ("Group parameters", "Параметры группы"),
                   "group_remove": ("Remove group", "Удалить группу"), "init": ("Back", "Назад")},
}
FIELD = {"name": ("Name", "Название"), "area": ("Area (optional)", "Область (необязательно)"), "key": ("Zone key (optional)", "Ключ зоны (необязательно)"), "lamps": ("Lamps", "Лампы"), "share_pause": ("Manual change pauses the whole group", "Ручное изменение ставит на паузу всю группу"),
         "zone": ("Zone", "Зона"), "lamp": ("Lamp", "Лампа"), "group": ("Group", "Группа"),
         "sensor": ("Trigger", "Триггер"),
         "brightness": ("Brightness", "Яркость"),
         "brightness_pct": ("Brightness, %", "Яркость, %"),
         "color_temp_kelvin": ("Colour temperature, K", "Цветовая температура, K"),
         "off_delay_s": ("Off delay, s", "Задержка выключения, с"),
         "rule": ("Rule", "Правило")}
CALIB_DESC = (
    "Measures lux per 1% of every lamp of the zone at three levels (about 4 minutes per lamp at the default settle time) and stores "
    "candidates; nothing is applied until calibrate_apply. A lamp whose test levels raise the illuminance by less than 1 lx "
    "(the sensor is far or high, the lamp is weak) does not stop the calibration: it is not measured and keeps its base k (its "
    "current value, 0.1 by default), marked as base; if no lamp of the zone shows growth the whole zone is calibrated 'by base' and "
    "no dark_lux_offset is proposed (see the calibration_source and base_lamps attributes of the calibration sensor). It refuses to start only when the zone has no illuminance sensor or "
    "a calibration of the zone is already running. Darkness, mode Night, an empty room and a switched-off TV give the best result, "
    "but none of them is required: unfavourable conditions only produce a warning (log, persistent notification, the warnings "
    "attribute of the calibration sensor and the service response). Do not turn the lamps on or off and do not change them by hand "
    "while the calibration runs. Daylight, people in the room and a running TV or monitor distort the result.",
    "Измеряет люксы на 1% у каждой лампы зоны на трёх уровнях (около 4 минут на лампу при времени ожидания по умолчанию) и сохраняет "
    "кандидатов; пока не вызвана calibrate_apply, ничего не применяется. Лампа, у которой на тестовых уровнях освещённость выросла "
    "меньше чем на 1 лк (датчик далеко или высоко, лампа слабая), калибровку не останавливает: она не измеряется и сохраняет базовое "
    "k (текущее значение, по умолчанию 0.1) с пометкой base; если прироста нет ни у одной лампы зоны, вся зона калибруется «по базе» "
    "и dark_lux_offset не предлагается (атрибуты calibration_source и base_lamps у датчика калибровки). Отказ только по техническим причинам: у зоны нет датчика "
    "освещённости или калибровка зоны уже идёт. Лучший результат дают темнота, режим Night, пустая комната и выключенный ТВ, "
    "но ничего из этого не обязательно: неблагоприятные условия дают только предупреждение (журнал, постоянное уведомление, атрибут "
    "warnings у датчика калибровки и ответ службы). Пока идёт калибровка, не включайте и не выключайте лампы и не меняйте их "
    "вручную. Дневной свет, присутствие людей и работающий ТВ или монитор искажают результат.")
CALIB_FIELDS = {
    "zone": ("Zone key (the main zone by default).", "Ключ зоны (по умолчанию основная)."),
    "settle_s": ("Seconds to wait after every lamp change before reading the illuminance.", "Секунд ожидания после каждого изменения лампы перед чтением освещённости."),
    "wait": ("Wait until the calibration finishes (can take many minutes). Do not touch the lamps meanwhile.",
             "Ждать окончания калибровки (может занять много минут). Не трогайте лампы в это время."),
}
APPLY_DESC = (
    "Writes the calibration candidates into the k of the lamps and, when something was measured, into dark_lux_offset of the zone. "
    "Lamps marked base (no growth, not measured) get their base k written explicitly; it is NOT a measurement. A run where no lamp "
    "showed growth never writes dark_lux_offset. The response lists the base lamps and whether dark_lux_offset was written.",
    "Записывает кандидатов калибровки в k ламп и, если что-то было измерено, в dark_lux_offset зоны. Лампам с пометкой base (нет "
    "прироста, не измерены) явно записывается базовое k; это НЕ измерение. Запуск, где ни одна лампа не показала прироста, "
    "dark_lux_offset не пишет никогда. В ответе перечислены лампы base и записан ли dark_lux_offset.")
SERV = {
    "controller_tick": ("Run controller tick", "Один такт контроллера"), "clear_pauses": ("Clear manual pauses", "Сбросить паузы"),
    "openness_reset": ("Reset openness", "Сбросить открытость"), "calibrate": ("Calibrate lamps (night)", "Калибровка ламп (ночь)"),
    "calibrate_cancel": ("Cancel calibration", "Отменить калибровку"), "calibrate_apply": ("Apply calibration", "Применить калибровку"),
    "calibrate_open_k": ("Calibrate open_k (day)", "Калибровка open_k (день)"), "set_param": ("Set a parameter", "Задать параметр"),
}


def build(i: int) -> dict:
    steps = {}
    for sid, v in UI.items():
        if sid == "title":
            continue
        steps[sid] = {"title": v[i], "description": v[2 + i]}
    data = {sid: {} for sid in steps}
    for n, v in PARAM.items():
        data["params"][n] = v[i]
    for r, v in ROLE.items():
        for sid in ("global_sensors", "zone_sensors", "zone_extra_sensors"):
            data[sid][r] = v[i]
    for s, v in SLOT.items():
        data["zone_bindings"][s] = v[i]
    for sid in ("group_add", "group_edit"):
        for f in ("name", "lamps", "share_pause"):
            data[sid][f] = FIELD[f][i]
    data["zone"]["area"] = data["new_zone"]["area"] = FIELD["area"][i]
    data["zone_name"]["name"], data["zone_name"]["key"] = FIELD["name"][i], FIELD["key"][i]
    data["zone_lamps"]["lamps"] = FIELD["lamps"][i]
    for sid, f in (("pick_zone", "zone"), ("pick_lamp", "lamp"), ("pick_group", "group")):
        data[sid][f] = FIELD[f][i]
    for f in ("sensor", "lamps", "brightness", "brightness_pct", "color_temp_kelvin", "off_delay_s"):
        data["detect_add"][f] = FIELD[f][i]
    data["detect_delete"]["rule"] = FIELD["rule"][i]
    for sid in steps:
        if data[sid]:
            steps[sid]["data"] = data[sid]
        if sid in MENU:
            steps[sid]["menu_options"] = {o: v[i] for o, v in MENU[sid].items()}
    for sid, opts in MENU.items():
        steps.setdefault(sid, {"title": UI["title"][i]})["menu_options"] = {o: v[i] for o, v in opts.items()}
    for sid in ("groups_menu", "more_zones", "lamp_menu", "group_menu", "zone_menu", "init"):
        steps[sid].setdefault("title", UI["title"][i])
    err = {c: v[i] for c, v in ERR.items()}
    out = {"title": UI["title"][i], "config": {"step": steps, "error": err,
           "abort": {"single_instance_allowed": "Already configured." if i == 0 else "Уже настроено."}},
           "options": {"step": steps, "error": err},
           "selector": {
               "yes_no": {"options": {"yes": "Yes" if i == 0 else "Да", "no": "No" if i == 0 else "Нет"}},
               "detect_brightness": {"options": {
                   "adaptive": "Adaptive (the controller sets the brightness)" if i == 0 else "Адаптивная (яркость задаёт контроллер)",
                   "fixed": "Fixed" if i == 0 else "Фиксированная",
               }},
           },
           "services": {n: {"name": v[i], "description": v[i]} for n, v in SERV.items()}}
    out["services"]["calibrate"]["description"] = CALIB_DESC[i]
    out["services"]["calibrate_apply"]["description"] = APPLY_DESC[i]
    out["services"]["calibrate"]["fields"] = {f: {"name": f, "description": d[i]} for f, d in CALIB_FIELDS.items()}
    return out


if __name__ == "__main__":
    tr = ROOT / "translations"
    tr.mkdir(exist_ok=True)
    for i, name in enumerate(("en", "ru")):
        (tr / f"{name}.json").write_text(json.dumps(build(i), ensure_ascii=False, indent=2) + "\n")
    (ROOT / "strings.json").write_text(json.dumps(build(0), ensure_ascii=False, indent=2) + "\n")
    print("ok")
