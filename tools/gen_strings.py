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
    "presence": ("Presence sensor", "Датчик присутствия"),
    "media": ("TV / monitors (calibration condition)", "ТВ / мониторы (условие калибровки)"),
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
    "zone_sensors": ("Sensors of {zone}", "Датчики зоны {zone}", "All optional. Without an illuminance sensor the zone uses a daylight-only estimate and cannot be calibrated. Sensors found in the area are prefilled.",
                     "Все необязательные. Без датчика освещённости зона считает свет по дневному свету и не калибруется. Датчики из области подставлены."),
    "zone_extra_sensors": ("Extra sensors of {zone}", "Дополнительные датчики {zone}", "Only for the behaviour scenarios (fridge, sofa, windows ...). Leave empty if unused.",
                           "Только для сценариев поведения (холодильник, диван, окна ...). Оставьте пустым, если не нужно."),
    "zone_lamps": ("Lamps of {zone}", "Лампы зоны {zone}", "Pick the light entities of this zone (lights of the area are prefilled). Each becomes a lamp with its own auto switch.",
                   "Выберите лампы этой зоны (лампы области подставлены). У каждой будет свой переключатель авто."),
    "group_add": ("New lamp group in {zone}", "Новая группа ламп в {zone}", "A group shares settings and can be used by scenarios. A lamp may be in several groups.",
                  "Группа задаёт общие настройки и используется сценариями. Лампа может входить в несколько групп."),
    "group_edit": ("Group {group}", "Группа {group}", "", ""),
    "params": ("Parameters ({level}: {target})", "Параметры ({level}: {target})", "Empty field = inherit from the level above (global, zone, group, lamp).",
               "Пустое поле = наследовать от уровня выше (глобально, зона, группа, лампа)."),
    "zone_bindings": ("Behaviour of {zone}", "Поведение зоны {zone}", "Which groups and lamps the scenarios use. Empty = scenario off.", "Какие группы и лампы использовать в сценариях. Пусто = сценарий выключен."),
    "pick_zone": ("Pick a zone", "Выберите зону", "", ""), "pick_lamp": ("Pick a lamp", "Выберите лампу", "", ""), "pick_group": ("Pick a group", "Выберите группу", "", ""),
    "zone_remove": ("Remove {zone}?", "Удалить {zone}?", "Its lamps, groups and sensors are removed from the configuration.", "Лампы, группы и датчики зоны будут удалены из конфигурации."),
    "lamp_remove": ("Remove {lamp}?", "Удалить {lamp}?", "", ""), "group_remove": ("Remove {group}?", "Удалить {group}?", "", ""),
    "save": ("Review", "Проверка", "{summary}\n\n{detail}", "{summary}\n\n{detail}"),
}
MENU = {  # step: {option: (en, ru)}
    "user": {"global_sensors": ("Step-by-step wizard", "Пошаговый мастер"), "preset_plan": ("Preset: living room of the YAML plan", "Пресет: гостиная из YAML-плана"),
             "resume_draft": ("Continue the unfinished setup", "Продолжить незавершённую настройку"),
             "discard_draft": ("Discard the unfinished setup", "Удалить незавершённую настройку")},
    "groups_menu": {"group_add": ("Add a lamp group", "Добавить группу ламп"), "more_zones": ("Continue", "Дальше")},
    "more_zones": {"zone": ("Add another zone", "Добавить ещё зону"), "save": ("Finish", "Готово")},
    "init": {"global_sensors": ("Outdoor sensors", "Уличные датчики"), "global_params": ("Global parameters", "Глобальные параметры"),
             "pick_zone": ("Edit a zone", "Изменить зону"), "new_zone": ("Add a zone", "Добавить зону"), "pick_lamp": ("Edit a lamp", "Изменить лампу"),
             "pick_group": ("Edit a group", "Изменить группу"), "save": ("Save and apply", "Сохранить и применить")},
    "zone_menu": {"zone_sensors": ("Sensors", "Датчики"), "zone_extra_sensors": ("Extra sensors", "Доп. датчики"), "zone_lamps": ("Add lamps", "Добавить лампы"),
                  "group_add": ("Add a group", "Добавить группу"), "zone_params": ("Zone parameters", "Параметры зоны"),
                  "zone_bindings": ("Behaviour scenarios", "Сценарии поведения"), "zone_remove": ("Remove zone", "Удалить зону"), "init": ("Back", "Назад")},
    "lamp_menu": {"lamp_params": ("Lamp parameters", "Параметры лампы"), "lamp_remove": ("Remove lamp", "Удалить лампу"), "init": ("Back", "Назад")},
    "group_menu": {"group_edit": ("Name and lamps", "Название и лампы"), "group_params": ("Group parameters", "Параметры группы"),
                   "group_remove": ("Remove group", "Удалить группу"), "init": ("Back", "Назад")},
}
FIELD = {"name": ("Name", "Название"), "area": ("Area (optional)", "Область (необязательно)"), "key": ("Zone key (optional)", "Ключ зоны (необязательно)"), "lamps": ("Lamps", "Лампы"), "share_pause": ("Manual change pauses the whole group", "Ручное изменение ставит на паузу всю группу"),
         "zone": ("Zone", "Зона"), "lamp": ("Lamp", "Лампа"), "group": ("Group", "Группа")}
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
           "selector": {"yes_no": {"options": {"yes": "Yes" if i == 0 else "Да", "no": "No" if i == 0 else "Нет"}}},
           "services": {n: {"name": v[i], "description": v[i]} for n, v in SERV.items()}}
    return out


if __name__ == "__main__":
    tr = ROOT / "translations"
    tr.mkdir(exist_ok=True)
    for i, name in enumerate(("en", "ru")):
        (tr / f"{name}.json").write_text(json.dumps(build(i), ensure_ascii=False, indent=2) + "\n")
    (ROOT / "strings.json").write_text(json.dumps(build(0), ensure_ascii=False, indent=2) + "\n")
    print("ok")
