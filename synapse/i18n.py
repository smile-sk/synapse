"""Synapse — один словарь интерфейсных строк. Здесь и только здесь живёт язык.

ЗАЧЕМ (слово Стаса 25.09.2026): «какой то хаус у нас с языком вышел». До этого
модуля русские строки были вписаны прямо в код панели, отчётов, журнала и речи
существа — и стоило поменять одну, как остальные оставались на прежнем языке.

ГРАНИЦА, ЖЕЛЕЗНАЯ И ПРОВЕРЯЕМАЯ ТЕСТОМ. Есть две категории текста, и они
НИКОГДА не смешиваются:

  ТЕХНИЧЕСКОЕ — всегда по-английски, в любом языке интерфейса, и через i18n
  НЕ проходит: имена инструментов (net_audit, write_probe, certs, diff,
  stimulus, note, ask), состояния миссий (queued, running, done, failed,
  refused), события журнала и его поля, ключи настроек, имена файлов и режимы
  мозга (off, server). Это идентификаторы: оператор видит ровно то слово,
  которое лежит в файле очереди, и может искать по нему.

  ИНТЕРФЕЙС — переводится: заголовки, кнопки, подписи полей, подсказки,
  названия вкладок, текст отчётов, «что это значит» и речь самого существа.
  Всё это берётся только через t().

КАК ПОЛЬЗОВАТЬСЯ:
    from synapse import i18n
    i18n.set_lang("ru")                 # или sync_from_config(cfg) на старте
    i18n.t("btn.live")                  # -> "Жить"
    i18n.t("rep.hosts_line", ip=".88", ports="445 SMB")   # подстановка полей

ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ: если ключа нет, t() возвращает САМ КЛЮЧ (видно
глазами: на кнопке будет «btn.live»), а не пустоту и не английский текст.
Молчаливая подмена прятала бы ошибку — а её надо видеть.
"""
from __future__ import annotations

#: Языки, между которыми можно переключаться. Первый — по умолчанию.
LANGS = ("en", "ru")
DEFAULT = "en"

#: Как язык называется сам для себя — это НЕ переводится.
LANG_NAMES = {"en": "English", "ru": "Русский"}

#: Откуда брать язык/тему в настройках (data/config.json).
CONFIG_SECTION = "ui"
CONFIG_LANG = "lang"

# Русские значения зон лежат в САМОЙ карте ролей (data/body/neuron_roles.json),
# поэтому переводим их только на показ — файл не переписываем.
ZONE_KEYS = {"голова": "zone.head", "середина": "zone.mid", "хвост": "zone.tail"}
ROLE_KEYS = {"sensory": "role.sensory", "inter": "role.inter", "motor": "role.motor",
             "polymodal": "role.polymodal", "other": "role.other",
             "unknown": "role.unknown"}

_lang = DEFAULT

def available() -> list[str]:
    """Какие языки есть в словаре (для выпадающего списка настроек)."""
    return list(LANGS)

def name(code: str) -> str:
    return LANG_NAMES.get(code, code)

def lang() -> str:
    return _lang

def set_lang(code: str | None) -> str:
    """Выбрать язык. Незнакомый код не принимаем — остаёмся на прежнем."""
    global _lang
    code = str(code or "").strip().lower()
    if code in LANGS:
        _lang = code
    return _lang

def sync_from_config(cfg: dict) -> str:
    """Взять язык из настроек (ui.lang). Зовётся на старте окна и консоли."""
    section = (cfg or {}).get(CONFIG_SECTION) or {}
    return set_lang(section.get(CONFIG_LANG) or DEFAULT)

def role_name(role: str) -> str:
    return t(ROLE_KEYS.get(str(role), "role.unknown"))

def zone_name(zone: str) -> str:
    zone = str(zone or "")
    if not zone:
        return t("dash")
    return t(ZONE_KEYS.get(zone, "dash"))

def has(key: str) -> bool:
    return key in CATALOG

def _reset_duplicates() -> list[str]:
    """Ключи, объявленные дважды. Тест зовёт это, чтобы словарь не «плыл»."""
    seen: set[str] = set()
    dupes: list[str] = []
    for key, _en, _ru in _STRINGS:
        if key in seen:
            dupes.append(key)
        seen.add(key)
    return dupes

def t_in(lang: str, key: str, **kw) -> str:
    """Строка на ЗАДАННОМ языке, не трогая текущий.

    Нужна отчётам (их можно нарисовать на любом языке, не переключая окно)
    и тестам: они держат оба языка рядом в одном процессе.
    """
    global _lang
    keep, _lang = _lang, DEFAULT
    try:
        set_lang(lang)
        return t(key, **kw)
    finally:
        _lang = keep

def t(key: str, **kw) -> str:
    """Строка интерфейса на текущем языке.

    Нет ключа — возвращаем сам ключ (ошибку надо видеть, а не прятать).
    Лишнее поле в подстановке не роняет окно: отдаём текст как есть.
    """
    rec = CATALOG.get(key)
    if rec is None:
        return key
    text = rec.get(_lang) or rec.get(DEFAULT) or key
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text
    return text

# --------------------------------------------------------------------------
# СЛОВАРЬ. Одна строка = один ключ: (ключ, по-английски, по-русски).
# Оба языка обязаны быть заполнены — это проверяет tests/test_i18n_theme.py,
# там же сверяется, что наборы полей {имя} в обоих языках совпадают.
# --------------------------------------------------------------------------
_STRINGS: list[tuple[str, str, str]] = [
    # --- окно, вкладки ---
    ("win.title", "Synapse — control panel", "Synapse — панель управления"),
    ("tab.missions", "Missions", "Миссии"),
    ("tab.journal", "Journal", "Журнал"),
    ("tab.thoughts", "Thoughts", "Мысли"),
    ("thoughts.diary", "Diary of the world — what changed in what it looked at:",
     "Дневник мира — что изменилось в том, на что оно смотрело:"),
    ("thoughts.hint",
     "Inner speech (💭) — a separate channel, not the action journal. The creature thinks here. "
     "A small brain does not put 💭 marks, and then everything it says lands here: in the flow its "
     "answer IS its thinking aloud.",
     "Внутренняя речь (💭) — отдельный канал, не журнал действий. Существо думает здесь. "
     "Малый мозг метки 💭 не ставит, и тогда сюда идёт всё, что он сказал: в потоке его ответ "
     "И ЕСТЬ думание вслух."),
    ("tab.reports", "Reports", "Отчёты"),
    ("tab.brain", "Brain", "Мозг"),
    ("tab.settings", "Settings", "Настройки"),
    ("dash", "—", "—"),

    # --- тело (левая половина окна) ---
    ("btn.live", "Live", "Жить"),
    ("btn.pause", "Pause", "Пауза"),
    ("btn.step", "Step", "Такт"),
    ("btn.pulse", "Pulse", "Импульс"),
    ("btn.reset_view", "Reset view", "Сброс вида"),
    ("color.roles", "colour: roles", "цвет: роли"),
    ("color.nt", "colour: transmitters", "цвет: медиаторы"),
    ("color.zone", "colour: zones", "цвет: зоны"),
    ("card.hub", " · hub", " · хаб"),
    ("body.line",
     "nodes {nodes} · edges {edges} · lit {lit} · energy {energy} · brain {brain} · "
     "ticks {ticks} · wakeups {wakeups} · queued {queued} · frames {frames}, {ms} ms/frame",
     "узлов {nodes} · связей {edges} · зажжено {lit} · энергия {energy} · мозг {brain} · "
     "тактов {ticks} · пробуждений {wakeups} · в очереди {queued} · кадров {frames}, {ms} мс/кадр"),

    # --- карточка нейрона ---
    ("card.base", "{name} · role: {role} · zone: {zone} · transmitter: {nt} · links: {links}",
     "{name} · роль: {role} · зона: {zone} · медиатор: {nt} · связей: {links}"),
    ("card.peers", " · peers: {peers}", " · соседи: {peers}"),
    ("card.receptors", " · receptors: {receptors}", " · рецепторы: {receptors}"),

    # --- вкладка «Миссии» ---
    ("col.num", "#", "№"),
    ("col.command", "command", "команда"),
    ("col.status", "status", "статус"),
    ("col.what", "what", "что"),
    ("col.when", "when", "когда"),
    ("col.result", "result", "итог"),
    ("ph.subnet", "subnet, e.g. 192.168.1.0/24", "подсеть, например 192.168.1.0/24"),
    ("ph.host", "host (optional — taken from the last audit)",
     "хост (необязательно — возьмёт из последнего аудита)"),
    ("ph.text", "task text", "текст задания"),
    ("btn.queue", "Queue", "В очередь"),
    ("btn.run_now", "Run now", "Выполнить сейчас"),
    ("btn.my_network", "My network", "Моя сеть"),
    ("btn.approve", "Approve", "Подтвердить"),
    ("tk.approved", "approved: {id} ({kind}) — it will be taken on the next tick",
     "подтверждена: {id} ({kind}) — возьмётся на следующем такте"),
    ("tk.approve_missing", "there is no such mission in the queue: {id}",
     "такой миссии нет в очереди: {id}"),
    ("tk.approve_not_proposed", "this mission is not waiting for approval: {id} ({status})",
     "эта миссия не ждёт подтверждения: {id} ({status})"),
    ("btn.delete_selected", "Delete selected", "Удалить выбранную"),
    ("msg.queued", "queued: {id} ({kind})", "в очереди: {id} ({kind})"),
    ("msg.running_now", "running now: {id} ({kind})", "выполняю сейчас: {id} ({kind})"),

    # --- вкладка «Отчёты» / «Журнал» ---
    ("btn.open_file", "Open as file", "Открыть файлом"),
    ("btn.refresh", "Refresh", "Обновить"),

    # --- вкладка «Мозг» ---
    ("btn.brain_on", "Brain on", "Мозг вкл"),
    ("btn.brain_off", "Brain off", "Мозг выкл"),
    ("brain.line", "brain: {mode} · server: {server} · model: {model}",
     "мозг: {mode} · сервер: {server} · модель: {model}"),
    ("brain.starting", "brain on: starting my own llama-server…",
     "включаю мозг: поднимаю свой llama-server…"),
    ("brain.silent", "brain is silent: the server is not up (press Brain on)",
     "мозг молчит: сервер не поднят (нажми «Мозг вкл»)"),
    ("brain.no_answer", "the brain did not answer", "мозг не ответил"),
    ("ph.ask", "ask the brain ({model}) — the answer appears here",
     "спросить мозг ({model}) — ответ появится здесь"),
    ("ask.brain", "brain:", "мозг:"),
    ("btn.ask", "Ask", "Спросить"),
    ("ask.me", "me:", "я:"),
    ("ask.default", "how do you feel?", "как ты себя чувствуешь?"),

    # --- вкладка «Настройки» ---
    ("set.section.view", "Window and language", "Окно и язык"),
    ("set.section.life", "Life and body", "Жизнь и тело"),
    ("set.section.net", "Network and permissions", "Сеть и разрешения"),
    ("set.language", "Interface language", "Язык интерфейса"),
    ("set.language.hint",
     "Reports and the creature's speech follow this language. Commands, statuses and "
     "journal events stay English — they are identifiers.",
     "Отчёты и речь существа идут на этом языке. Команды, статусы и события журнала "
     "остаются английскими — это идентификаторы."),
    ("set.theme", "Theme", "Тема"),
    ("set.theme.dark", "dark", "тёмная"),
    ("set.theme.light", "light", "светлая"),
    ("set.theme.hint", "The whole window and the body canvas.", "Всё окно и полотно тела."),
    ("set.fps", "Body frame interval, ms", "Кадр тела, мс"),
    ("set.fps.hint", "Lower is smoother. 16 = 60 fps, 33 = 30 fps (measured).",
     "Меньше — плавнее. 16 = 60 кадр/с, 33 = 30 кадр/с (замер)."),
    ("set.tick", "Tick interval, seconds", "Такт, секунды"),
    ("set.tick.hint",
     "0 = continuous flow: no pauses at all, a thought follows a thought at the model's own pace. "
     "A number = a pause of that many seconds between ticks, and exactly the same tempo of thoughts.",
     "0 = поток: пауз нет вовсе, мысль идёт за мыслью в темпе самой модели. Число = пауза столько "
     "секунд между тактами — и темп мыслей ровно такой же."),
    ("set.idle_thinking", "Think out loud between missions", "Думать вслух между миссиями"),
    ("set.life.stream", "Now: continuous flow — tick follows tick, no pauses.",
     "Сейчас: поток — такт за тактом, без пауз."),
    ("set.life.tick", "Now: a tick every {tick} s — so a thought comes at most once in {tick} s.",
     "Сейчас: такт {tick} с — значит и мысль не чаще, чем раз в {tick} с."),
    ("set.ports_from", "Ports from", "Порты от"),
    ("set.ports_to", "to", "до"),
    ("set.ports.hint", "The range is swept on every live host. 1-65535 = every port.",
     "Диапазон обходится на каждом живом хосте. 1-65535 = все порты."),
    ("set.ports.estimate", "{lo}-{hi}: {count} port(s) ≈ {time} per live host (measured).",
     "{lo}-{hi}: {count} порт(ов) ≈ {time} на живой хост (замер)."),
    ("set.time.sec", "{n} s", "{n} с"),
    ("set.time.min", "{n} min", "{n} мин"),
    ("set.time.hour", "{n} h", "{n} ч"),
    ("set.write_probe", "Allow the write probe in shares", "Разрешить тест записи в общих папках"),
    ("set.write_probe.hint",
     "One marker file: written, read back and removed at once. Off — the tool refuses.",
     "Один файл-маркер: записал, прочитал, сразу убрал. Выключено — инструмент откажет."),
    ("set.propose", "The brain may offer itself work", "Мозг может предлагать себе работу"),
    ("set.propose.hint",
     "The brain sees its own deeds — the last report, the changes in its world, its "
     "reserve and its targets — and may offer ONE read-only job with a line 🎯 tool. "
     "It runs nothing itself and cannot step outside the read-only list.",
     "Мозг видит свои дела — последний отчёт, изменения в своём мире, запас и цели — "
     "и может предложить ОДНУ работу только для чтения строкой 🎯 инструмент. Сам он "
     "ничего не запускает и за пределы списка «только чтение» не выходит."),
    ("set.propose_confirm", "…and wait for my confirmation",
     "…и ждать моего подтверждения"),
    ("set.propose_confirm.hint",
     "On: an offered job stands as «proposed» and goes to work only after the Approve "
     "button — you see what it wants before it touches anything. Off: it takes the "
     "offered job at once and only reports.",
     "Вкл: предложенная работа стоит как «proposed» и пойдёт в дело только после кнопки "
     "«Подтвердить» — ты видишь, чего оно хочет, пока оно ничего не трогало. Выкл: "
     "берёт предложенное сразу и только отчитывается."),
    # ОБЛАЧНЫЙ МОЗГ (v0.30.0). Слово Стаса 30.09.2026: «давай делать подключение
    # облачной модели, начнём с gemini — причём давай сделаем выбор режимов — только
    # облако, только локальный и локальный + облако… чтоб OpenAI совместимые в будущем
    # можно было тот же groq к примеру подключить». Строки интерфейса — парами.
    ("set.cloud", "Where the brain thinks", "Откуда мозг берёт мысль"),
    ("set.cloud.hint",
     "Local: my own server on this machine (nothing leaves the house). Online: the cloud "
     "only, and if it does not answer — the creature is silent too. Local + cloud: the "
     "cloud when it is ready and the daily ceiling is not reached, otherwise my own server.",
     "Локальный: свой сервер на этой машине (наружу не уходит ничего). Облако: только "
     "облако, и если оно не ответило — существо молчит тоже. Локальный + облако: облако, "
     "когда оно готово и суточный потолок не выбран, иначе свой сервер."),
    ("set.cloud.local", "only local", "только локальный"),
    ("set.cloud.online", "only cloud", "только облако"),
    ("set.cloud.hybrid", "local + cloud", "локальный + облако"),
    ("set.cloud_provider", "Provider", "Провайдер"),
    ("set.cloud.provider.hint",
     "One protocol for all of them (POST /chat/completions, Bearer key), so a provider is "
     "a line in the settings, not a branch in the code: Gemini, Groq, OpenRouter, NVIDIA, "
     "DeepSeek, Z.AI or «custom» with your own address.",
     "У всех один протокол (POST /chat/completions, ключ через Bearer), поэтому провайдер — "
     "строчка в настройках, а не ветка кода: Gemini, Groq, OpenRouter, NVIDIA, DeepSeek, "
     "Z.AI или «custom» со своим адресом."),
    ("set.cloud_model", "Model", "Модель"),
    ("set.cloud.model.hint",
     "A hint list is shown, but the field is editable: the provider's model names change "
     "(gemini-2.5-flash-lite was answered «no longer available to new users»), and the "
     "name must be fixable without touching the code.",
     "Список — подсказка, поле редактируемое: имена моделей у провайдера меняются "
     "(на gemini-2.5-flash-lite живой ответ был «no longer available to new users»), и "
     "имя надо уметь исправить, не трогая код."),
    ("set.cloud_budget", "Token ceiling per day", "Потолок токенов в сутки"),
    ("set.cloud.budget.hint",
     "Our own ceiling, not the provider's tariff: we do not know it and will not invent it. "
     "At the ceiling the request is not sent at all — the creature says «limit» honestly.",
     "Наш собственный потолок, а не тариф провайдера: тарифа мы не знаем и выдумывать не "
     "станем. На потолке запрос НЕ отправляется вовсе — существо честно говорит «лимит»."),
    ("set.cloud_mask", "Do not give public addresses to the cloud",
     "Не отдавать публичные адреса в облако"),
    ("set.cloud.mask.hint",
     "Public IPs leave as «внешний-IP» (the second one — «внешний-IP-2»), my own network "
     "stays as it is — without it «what is this, where from, is it harmful» makes no sense.",
     "Публичные адреса уезжают как «внешний-IP» (второй — «внешний-IP-2»), своя сеть "
     "остаётся как есть — без неё разбор «что это, откуда, вредное ли» теряет смысл."),
    ("set.cloud.key_yes", "key found in {path} (section «{provider}») — length {n}",
     "ключ найден в {path} (раздел «{provider}») — длина {n}"),
    ("set.cloud.key_no", "no key yet: add it to {path} under «{provider}» — nothing goes "
     "outwards until then",
     "ключа пока нет: положи в {path} в раздел «{provider}» — до этого наружу не уйдёт "
     "ничего"),
    ("set.changed.cloud_mode", "the brain thinks: {value}", "мозг берёт мысль: {value}"),
    ("set.changed.cloud_provider", "cloud provider: {value}", "провайдер облака: {value}"),
    ("set.changed.cloud_model", "cloud model: {value}", "модель облака: {value}"),
    ("set.changed.cloud_budget", "cloud ceiling: {value} tokens/day",
     "потолок облака: {value} токенов/сутки"),
    ("set.changed.cloud_mask", "public addresses in the cloud: {value}",
     "публичные адреса в облаке: {value}"),
    # Почему облако молчит — по КОДУ причины, а не «что-то пошло не так».
    ("cloud.why.nokey", "cloud: no key — add it in Settings (nothing was sent)",
     "облако: нет ключа — положи его в «Настройках» (наружу ничего не ушло)"),
    ("cloud.why.nourl", "cloud: no address — fill it in Settings for «custom»",
     "облако: нет адреса — впиши его в «Настройках» для «custom»"),
    ("cloud.why.nomodel", "cloud: no model name — pick or type one in Settings",
     "облако: не выбрана модель — выбери или впиши в «Настройках»"),
    ("cloud.why.limit", "cloud: the daily ceiling is spent — no request sent",
     "облако: суточный потолок выбран — запрос не отправлен"),
    ("cloud.why.calls", "cloud: the daily call ceiling is spent — no request sent",
     "облако: суточный потолок обращений выбран — запрос не отправлен"),
    ("brain.step.cloud", "brain on: thinking in the cloud ({provider} · {model})",
     "мозг включён: мысль из облака ({provider} · {model})"),
    ("brain.cloud.line", "cloud: {mode} · {provider} · {model} · today {spent}/{cap} tokens",
     "облако: {mode} · {provider} · {model} · за сутки {spent}/{cap} токенов"),
    ("btn.cloud_check", "Check the cloud", "Проверить облако"),
    ("cloud.checking", "asking the cloud…", "спрашиваю облако…"),
    ("ask.cloud", "cloud:", "облако:"),
    ("cloud.probe.ok", "the cloud answers: {provider} · {model} · {ms} ms · «{text}» · "
     "today {spent} tokens",
     "облако отвечает: {provider} · {model} · {ms} мс · «{text}» · за сутки {spent} токенов"),
    ("cloud.probe.fail", "the cloud did not answer: {error} (code {code}) {detail}",
     "облако не ответило: {error} (код {code}) {detail}"),
    ("btn.save_settings", "Save", "Сохранить"),
    ("btn.reload_settings", "Reload from file", "Перечитать из файла"),
    ("set.bad_number", "{field}: expected a number, got «{value}»",
     "{field}: нужно число, а получено «{value}»"),
    ("set.where", "settings file: {path}", "файл настроек: {path}"),

    # --- проверка миссии до постановки (дверь и поля) ---
    ("plan.no_tool", "no such tool: {kind}", "нет такого инструмента: {kind}"),
    ("plan.subnet_required", "subnet required — press «My network» or type it in",
     "нужна подсеть — нажми «Моя сеть» или впиши её"),
    ("plan.door_no", "the door says no: {why}", "дверь не пустит: {why}"),
    ("plan.host_optional", "ready: no host given — taken from the last audit",
     "можно давать: хост не указан — возьмём из последнего аудита"),
    ("plan.ready", "ready to queue", "можно давать"),
    ("plan.text_required", "task text required", "нужен текст задания"),

    # --- дверь (scope.py): причина приходит КОДОМ, слова живут здесь ---
    ("door.no_resolve", "name {host} does not resolve", "имя {host} не разрешается"),
    ("door.any", "{host} -> {ip}: any network is allowed by the operator's list",
     "{host} -> {ip}: любые сети разрешены списком оператора"),
    ("door.inside", "{host} -> {ip} is inside {net}",
     "{host} -> {ip} внутри {net}"),
    ("door.outside_nets", "{host} -> {ip} is outside the allowed networks ({allowed})",
     "{host} -> {ip} вне разрешённых сетей ({allowed})"),
    ("door.cidr_bad", "subnet {cidr} was not parsed: {error}",
     "подсеть {cidr} не разобралась: {error}"),
    ("door.cidr_outside", "subnet {cidr} is not inside the allowed ones ({allowed})",
     "подсеть {cidr} не внутри разрешённых ({allowed})"),
    ("door.cidr_big", "{cidr}: {hosts} addresses, but the cap is {limit} (split it)",
     "{cidr}: {hosts} адресов, а потолок {limit} (дели на части)"),
    ("door.cidr_ok", "{cidr}: {hosts} addresses, inside the allowed networks",
     "{cidr}: {hosts} адресов, внутри разрешённых сетей"),

    # --- инструменты: подпись и подсказка (то же, что в подписи отчётов) ---
    ("tool.net_audit.label", "network audit", "аудит сети"),
    ("tool.net_audit.hint",
     "sweep your own subnet: live hosts, open ports, services, share names.\n"
     "Read-only — write permissions are NOT tested.",
     "пройти по своей подсети: живые хосты, открытые порты, службы, имена общих папок.\n"
     "Только чтение — права на запись НЕ проверяются."),
    ("tool.ask.label", "ask the brain", "вопрос мозгу"),
    ("tool.ask.hint",
     "ask your own model. The answer lands in the result column and in the journal.",
     "спросить свою модель. Ответ ляжет в колонку «итог» и в журнал."),
    ("tool.stimulus.label", "stimulus to the body", "стимул телу"),
    ("tool.stimulus.hint",
     "inject an impulse into the sensory neurons — watch it spread along the connectome.",
     "впрыснуть импульс в сенсорные нейроны — видно, как он расходится по коннектому."),
    ("tool.note.label", "note", "заметка"),
    ("tool.note.hint", "leave a record in the mission history — just so that it stays.",
     "оставить запись в истории миссий — просто чтобы осталось."),
    # СЛУЖБЫ, КОТОРЫЕ ЖИВУТ В НАШЕМ ДОМЕ (v0.28.0, по живым снимкам 30.09.2026).
    # Имя службы — это и есть «понимать, что это»: без него отчёт читается как список
    # чисел, а с ним видно, что 5514 — приём логов роутера, 4500 — наш VPN, а 5228 —
    # служебные пуши Android, и ничего тревожного в них нет.
    ("svc.123", "NTP — clock sync", "NTP — сверка времени"),
    ("svc.500", "IPsec IKE — VPN setup", "IPsec IKE — установка VPN"),
    ("svc.631", "IPP — printing", "IPP — печать"),
    ("svc.1701", "L2TP — VPN tunnel", "L2TP — VPN-туннель"),
    ("svc.2121", "FTP (other port) — files", "FTP (другой порт) — файлы"),
    ("svc.4500", "IPsec NAT-T — VPN through NAT", "IPsec NAT-T — VPN через NAT"),
    ("svc.5222", "XMPP — messaging", "XMPP — обмен сообщениями"),
    ("svc.5228", "Google push — Android service", "Google push — служебные пуши Android"),
    ("svc.5353", "mDNS — devices on the LAN", "mDNS — поиск устройств в сети"),
    ("svc.5514", "rsyslog — takes the router's logs", "rsyslog — приём логов роутера"),
    ("svc.8291", "Winbox — MikroTik control", "Winbox — управление MikroTik"),
    ("svc.8728", "MikroTik API", "API MikroTik"),
    ("svc.5000", "Synology DSM", "Synology DSM — своё хранилище"),
    ("svc.5001", "Synology DSM (TLS)", "Synology DSM (TLS)"),
    ("svc.8883", "MQTT (TLS) — sensors", "MQTT (TLS) — обмен с датчиками"),
    ("svc.27015", "Steam game server", "игровой сервер Steam"),
    ("tool.write_probe.label", "write probe in shares", "тест записи в общих папках"),
    ("tool.write_probe.hint",
     "writes ONE marker file into a share, reads it back and removes it at once.\n"
     "Host is optional — takes the shares from the last network audit.",
     "пишет ОДИН файл-маркер в общую папку, читает обратно и сразу убирает.\n"
     "Хост необязателен — возьмёт папки из последнего аудита сети."),
    ("tool.certs.label", "certificate expiry", "сроки сертификатов"),
    ("tool.certs.hint",
     "TLS handshake and certificate lifetime: what expires and in how many days.\n"
     "Host is optional — takes the services from the configured ports of the last audit.",
     "рукопожатие TLS и срок жизни сертификата: что истекает и через сколько дней.\n"
     "Хост необязателен — возьмёт службы с настроенных портов из последнего аудита."),
    ("tool.diff.label", "what changed since the last audit", "что изменилось с прошлого аудита"),
    ("tool.diff.hint",
     "compare the two latest network audits: new hosts, gone hosts, new ports and shares.",
     "сравнить два последних аудита сети: новые хосты, пропавшие, новые порты и папки."),
    ("tool.remote.label", "remote control", "удалённое управление"),
    ("tool.remote.hint",
     "find a way to run a command on the given host: WinRM (5985/5986), WMI/DCOM\n"
     "(135+445 — the psexec path), SSH (22), SMB (445).\n"
     "The command goes into the task text; without a login and password it only\n"
     "reports which ways are open.",
     "найти способ выполнить команду на указанном хосте: WinRM (5985/5986), WMI/DCOM\n"
     "(135+445 — путь psexec), SSH (22), SMB (445).\n"
     "Команда — в тексте задания; без логина и пароля инструмент только сообщает,\n"
     "какие пути открыты."),

    # --- речь существа (то, что оно говорит само) ---
    ("ag.no_tool", "no such tool: {kind} (have: {kinds})",
     "нет такого инструмента: {kind} (есть: {kinds})"),
    ("ag.stopped_mission", "stopped by the operator before it finished",
     "остановлена оператором, не доработав"),
    ("ag.stimulus_done", "the body took the stimulus: {body}",
     "стимул принят телом: {body}"),
    ("ag.note", "note: {text}", "заметка: {text}"),
    ("ag.empty", "empty", "пусто"),
    ("ag.brain_silent", "the brain is silent (off, or the server is not up)",
     "мозг молчит (выключен или сервер не поднят)"),
    ("ag.alive", "alive: {body}", "живу: {body}"),
    # МОЗГ НЕ ОТВЕЧАЕТ (v0.25.0). Отдельная строка нужна, чтобы молчание мозга было
    # ОТЛИЧИМО от «просто живу»: 26.09.2026 оператор читал журнал и не мог понять,
    # почему существо ничего не делает, — а сервер в это время не отвечал.
    ("ag.nobrain",
     "the brain is not answering — I live with the body and will ask again: {body}",
     "мозг не отвечает — живу телом и спрошу снова: {body}"),
    ("ag.instinct_fed",
     "I was going quiet — I gave myself a small impulse to stay alive: {body}",
     "я затихала — дала себе слабый импульс, чтобы не умереть: {body}"),
    ("ag.instinct_overload",
     "overload: {lit} neurons are burning — I calm myself down: {body}",
     "перегруз: горят {lit} нейронов — гашу себя: {body}"),
    # ЖИЗНЬ: ЗАПАС (v0.27.0). Слово Стаса 30.09.2026: «живой киберорганизм ещё и
    # с пользой». Голод и истощение — состояния, которые существо называет само.
    ("ag.life_hungry",
     "I am hungry (reserve {reserve}) — I need work where something new turns up: {body}",
     "я голодна (запас {reserve}) — нужна работа, где найдётся новое: {body}"),
    ("ag.life_starving",
     "I am starving (reserve {reserve}) — the body is dimming, I need a find: {body}",
     "я истощена (запас {reserve}) — тело гаснет, нужна находка: {body}"),
    ("ag.life_feed", "found {new} new — fed {fed}, reserve {reserve}",
     "нашла нового: {new} — сыта {fed}, запас {reserve}"),
    ("ag.life_quiet", "nothing new — no food, reserve {reserve}",
     "нового нет — еды нет, запас {reserve}"),
    # --- САМОЗАДАНИЕ (M3): голод ставит себе работу, и это видно в журнале ---
    ("ag.self_work",
     "I am hungry — I set myself work: {kind} ({because})",
     "я голодна — дала себе работу: {kind} ({because})"),
    ("ag.self_work_note", "set by hunger: {because}", "поставлено голодом: {because}"),
    ("ag.leads_learned",
     "targets remembered: {new} new (open {open}, mine {mine})",
     "целей запомнила: {new} новых (открытых {open}, своих {mine})"),
    ("work.because.lead",
     "continue my own lead: {what} {target} ({why})",
     "продолжаю свою зацепку: {what} {target} ({why})"),
    ("work.because.peers",
     "the connection snapshot is stale ({age}) — the world changes there",
     "снимок соединений устарел ({age}) — там мир меняется"),
    ("work.because.net", "sweep my own subnet {cidr}", "обхожу свою подсеть {cidr}"),
    ("work.age.never", "never taken", "ни разу не снимала"),
    ("work.age.minutes", "{minutes} min ago", "{minutes} мин назад"),
    # --- МИР: СТАЛО ИНАЧЕ ИЛИ КАК БЫЛО (M5, 30.09.2026) ---
    # Слово Стаса: «я хочу сотворить живой киберорганизм ещё и с пользой» — а польза
    # видна только на СРАВНЕНИИ: порт открылся, узел ушёл, слушатель вылез наружу.
    # Без этого слоя существо смотрит и забывает, и «стало хуже» неотличимо от «как было».
    ("world.brief.none", "my world: I have not looked at it yet",
     "мой мир: я его ещё не смотрела"),
    ("world.brief",
     "My world: the last {kind} at {when}: came {plus}, left {minus}{items}; "
     "open targets {open} (mine {mine}).",
     "Мой мир: последний {kind} в {when}: появилось {plus}, ушло {minus}{items}; "
     "открытых целей {open} (своих {mine})."),
    ("world.items", " — {items}", " — {items}"),
    ("world.items.more", " and {n} more", " и ещё {n}"),
    ("ag.world_change", "the world changed: came {plus}, left {minus} ({items})",
     "мир изменился: появилось {plus}, ушло {minus} ({items})"),
    ("ag.world_same", "as before: the world did not change",
     "как было: мир не изменился"),
    ("world.diary.line", "- {when} · {kind} · came {plus}, left {minus} · {what}",
     "- {when} · {kind} · появилось {plus}, ушло {minus} · {what}"),
    ("world.diary.first", "- {when} · {kind} · first look (facts: {facts})",
     "- {when} · {kind} · первый взгляд (фактов: {facts})"),
    ("world.diary.same", "- {when} · {kind} · as before (facts: {facts})",
     "- {when} · {kind} · как было (фактов: {facts})"),
    # --- МОЗГ ПРЕДЛАГАЕТ РАБОТУ (M6, 30.09.2026) ---
    # Слово Стаса: «сначала посмотрим что он будет предлагать (и как я это кстати
    # апрувить? кнопка какая то что ли) — а потом … если его этим не тормозить».
    ("brain.propose_note", "offered by the brain", "предложено мозгом"),
    ("ag.propose_rule",
     "If one more look is worth it, add ONE line at the end: 🎯 tool params. "
     "Tools: peers (connections of our own nodes); net_audit cidr=…; remote host=…; "
     "certs host=… port=…. Our own nets: {mine}. One open target of mine: {target}. "
     "Take addresses from HERE or from what I have already seen — an invented one is "
     "refused. You only OFFER — the operator decides.",
     "Если стоит посмотреть ещё — добавь ОДНУ строку в конце: 🎯 инструмент параметры. "
     "Инструменты: peers (соединения своих узлов); net_audit cidr=…; remote host=…; "
     "certs host=… port=…. Свои сети: {mine}. Одна моя открытая цель: {target}. "
     "Адрес бери ОТСЮДА или из того, что я уже видела, — выдуманный не примут. "
     "Ты только ПРЕДЛАГАЕШЬ — решает оператор."),
    # v0.32.0: пустой материал называем прямо, а не оставляем прочерк — иначе в промпт
    # уходило «Свои сети: .» и модель считала, что сетей нет вовсе.
    ("ag.none", "none yet", "пока нет"),
    # СКУКА (v0.32.0): находок нет дольше life.bored_after — это НУЖДА, о которой
    # существо говорит само. Живой вечер 30.09.2026: запас стоял 0.99 шесть часов,
    # и мысли шли о нейронах, а не о деле.
    ("ag.bored",
     " I have found nothing new for a long time — boring, I want to look for something myself.",
     " Я давно ничего нового не находила — скучно, хочется посмотреть самой."),
    ("brain.propose_alien", "not our address and not one I have seen — refused",
     "не наш адрес и не виденный — отказ"),
    ("ag.propose_offered", "I offered work: {kind} {params} — waiting for your word",
     "предложила работу: {kind} {params} — жду твоего слова"),
    ("ag.propose_taken",
     "I took the offered work at once: {kind} {params} — the report will follow",
     "сразу взяла предложенную работу: {kind} {params} — отчёт будет"),
    ("ag.propose_blocked", "I wanted to offer work, but it was not taken: {why}",
     "хотела предложить работу, но её не взяли: {why}"),
    ("err.peers_off", "the connection look is switched off in settings (peers.enabled)",
     "взгляд на соединения выключен в настройках (peers.enabled)"),
    ("err.peers_none",
     "nothing to look at: fill peers.targets (name, host, os)",
     "не на что смотреть: заполни peers.targets (имя, адрес, род узла)"),
    ("err.peers_nologin",
     "no access: there is no login/password for this node in data/creds.json",
     "нет доступа: логина и пароля для этого узла нет в data/creds.json"),
    ("tool.peers.label", "connections of our own nodes", "соединения своих узлов"),
    ("tool.peers.hint",
     "read-only look at the connections of your router and your VPS: who goes where,\n"
     "who comes in, what listens, what is seen from both sides. The targets found\n"
     "become the creature's own next jobs. Commands live in code — nothing is\n"
     "written, blocked or changed on the node.",
     "взгляд только для чтения на соединения роутера и VPS: кто куда ходит, кто входит,\n"
     "что слушает, что видно с двух сторон. Найденные цели становятся своими\n"
     "следующими делами существа. Команды живут в коде — на узле ничего не пишется,\n"
     "не блокируется и не меняется."),
    ("rep.peers.head", "peers — connections of our own nodes",
     "peers — соединения своих узлов"),
    ("rep.peers.rule",
     "read-only: the commands are fixed in code, nothing is written or changed on the node",
     "только чтение: команды закреплены в коде, на узле ничего не пишется и не меняется"),
    ("rep.peers.nodes", "nodes:", "узлы:"),
    ("rep.peers.node_ok",
     "{name} ({host}) — read, {n} connections (unparsed lines: {skipped}), looked in as {who}",
     "{name} ({host}) — прочитан, соединений {n} (строк не разобрано: {skipped}), вошла как {who}"),
    ("rep.peers.node_bad", "{name} ({host}) — {state}: {error}",
     "{name} ({host}) — {state}: {error}"),
    ("rep.peers.mine", "our networks: {cidrs}", "наши сети: {cidrs}"),
    ("rep.peers.out_row",
     "{target}: {where}{svc} — {n} connection(s), {state}, from {clients} of our addresses",
     "{target}: {where}{svc} — соединений {n}, {state}, с {clients} наших адресов"),
    ("rep.peers.in_row", "{target}: {where}{svc} — {n} incoming connection(s), {state}",
     "{target}: {where}{svc} — входящих соединений {n}, {state}"),
    ("rep.peers.inner_row",
     "{target}: {frm} → {where}{svc} — {n} connection(s), {state}",
     "{target}: {frm} → {where}{svc} — соединений {n}, {state}"),
    ("rep.peers.listen_known", "{target}: {proto} {local} — listens: {svc}",
     "{target}: {proto} {local} — слушает: {svc}"),
    ("rep.peers.listen_unknown",
     "{target}: {proto} {local} — listens on port {port}, the service is not in our table",
     "{target}: {proto} {local} — слушает порт {port}, в таблице служб его нет"),
    ("rep.peers.listen_quiet",
     "…and {n} listener(s) on the host itself only (not visible from outside)",
     "…и ещё {n} слушателей только на самом узле (снаружи не видны)"),
    ("rep.peers.nothing", "nothing", "ничего нет"),
    ("rep.peers.more", "…and {n} more", "…и ещё {n}"),
    ("rep.peers.watch_both",
     "{peer} is seen from BOTH sides ({targets}) — not an accidental guest",
     "{peer} виден С ДВУХ СТОРОН ({targets}) — уже не случайный гость"),
    ("rep.peers.watch_freq", "{peer} — {n} connections from our side",
     "{peer} — соединений с нашей стороны {n}"),
    ("rep.peers.watch_port", "{target}: port {port}{svc} — not a usual service",
     "{target}: порт {port}{svc} — не обычная служба"),
    ("rep.peers.lead_row", "{target}{port} — {what}: {why} {mark}",
     "{target}{port} — {what}: {why} {mark}"),
    ("lead.why.listen_unusual",
     "a listener on our own node is not in the service table",
     "на своём узле слушает порт, которого нет в таблице служб"),
    ("lead.why.in_unusual",
     "someone came in to a port that is not a usual service",
     "к нам вошли на порт, который не обычная служба"),
    ("lead.why.both_sides", "seen from both sides at once", "виден сразу с двух сторон"),
    ("lead.why.frequent", "our people go there often, and the port is not a usual service",
     "наши ходят туда часто, а порт не обычная служба"),
    ("rep.peers.lead_alien", "(alien: I do not touch it without your word)",
     "(чужое: без твоего слова не трогаю)"),
    ("rep.section.out", "where our people go:", "куда ходят наши:"),
    ("rep.section.inner", "between our own nodes:", "между своими узлами:"),
    ("rep.section.in", "who comes in to us:", "кто входит к нам:"),
    ("rep.section.listen", "what listens:", "что слушает:"),
    ("rep.section.watch", "worth attention:", "стоит внимания:"),
    ("rep.section.leads", "targets for the next steps:", "цели для следующих шагов:"),
    ("rep.sum.peers", "peers: {bits}", "peers: {bits}"),
    ("rep.sum.peers_failed", "peers did not work: {error}", "peers не сработало: {error}"),
    ("rep.sum.peers_nodes", "nodes read: {ok}/{all}", "узлов прочитано: {ok}/{all}"),
    ("rep.sum.peers_conn", "connections {n}, listeners {listeners}",
     "соединений {n}, слушателей {listeners}"),
    ("rep.sum.peers_unusual", "not a usual service: {n}", "необычных портов: {n}"),
    ("rep.sum.peers_leads", "targets {n}", "целей {n}"),
    ("ag.cancelled_running",
     "the operator cancelled it while the mission was already running",
     "снята оператором уже во время работы"),
    ("ag.cap_none", "no cap set", "потолок не задан"),
    ("ag.cap_used", "the daily cap of {cap} wakeups is used up",
     "дневной потолок {cap} пробуждений исчерпан"),
    ("ag.ask_prompt",
     "Operator Stas asks: {question}\nAnswer in one or two short sentences.\n"
     "What the body is doing right now: {body}",
     "Оператор Стас просит: {question}\nОтветь одним-двумя короткими предложениями.\n"
     "Что сейчас с телом: {body}"),
    ("ag.think_prompt",
     "You are Synapse, a small organism with the nervous system of a worm. Tick {ticks} at {now}. "
     "Body: {body}; {live}, loudest {loud}. Reserve of strength "
     "{reserve} — hunger is a need, not a decoration.{bored} {diary} Intent: {intent}. "
     "{tail} Write your inner reasoning first as lines starting with 💭 (a separate channel, not for the operator). "
     "Then continue the SAME thought in ONE short sentence — your own words, not mine.",
     "Ты — Synapse, небольшой организм с нервной системой червя. Такт {ticks}, сейчас {now}. "
     "Тело: {body}; {live}, громче всех {loud}. Запас сил "
     "{reserve} — голод это нужда, а не украшение.{bored} {diary} Намерение: {intent}. "
     "{tail} Сначала напиши внутреннее рассуждение строками, начинающимися с 💭 (отдельный канал, не для оператора). "
     "Потом продолжи ТУ ЖЕ мысль одним коротким предложением — своими словами, не моими."),
    # РАКУРС — МЕТКА, А НЕ ФРАЗА (v0.17.0). Раньше здесь стояли предложения-команды
    # («Заметь, сколько прошло времени.»), и 0.5b возвращала их же вместо ответа:
    # живой журнал 25.09.2026, три записи подряд. Короткая метка копируется хуже,
    # а запрет копирования сказан прямо (ag.observe.noecho).
    ("ag.observe.body", "lens — your own body", "ракурс — своё тело"),
    ("ag.observe.time", "lens — time", "ракурс — время"),
    ("ag.observe.memory", "lens — memory", "ракурс — память"),
    ("ag.observe.noecho", "Do not copy my words: they are not an answer.",
     "Не копируй мои слова: они не ответ."),
    ("ag.repeat",
     "the same thought again ({streak}x): {text}",
     "одна и та же мысль опять ({streak}-й раз): {text}"),
    # КОПИЯ ПРОМПТА — НЕ МЫСЛЬ (v0.17.0). Оператор видел «Заметь, сколько прошло
    # времени.» — это была моя строка промпта. Теперь копия названа копией.
    ("ag.echo",
     "the model copied my own words instead of answering: «{text}» ({streak}x in a row)",
     "модель скопировала мои слова вместо ответа: «{text}» ({streak}-й раз подряд)"),
    ("ag.muted",
     "the brain copies my words, not answers — I live with the body for now, not asking",
     "мозг копирует мои слова, а не отвечает — живу телом, не спрашиваю"),
    # МУСОР ВМЕСТО МЫСЛИ (v0.20.2). Живой журнал 26.09.2026: мозг ответил 2000
    # знаков одних «---», муСор ушёл в нить и поток зациклился. Оператор видел
    # «ничего не отвечает». Теперь это названо честно и видно строкой ответа.
    ("ag.junk",
     "the brain answered with separators instead of words ({streak}x in a row) — living with the body",
     "мозг ответил разделителями, а не словами ({streak}-й раз подряд) — живу телом"),
    ("ag.observe.repeat",
     "You have already repeated yourself {n} time(s). Say something DIFFERENT: name one "
     "concrete neuron and what is happening with it right now.",
     "Ты уже повторился {n} раз(а). Скажи ДРУГОЕ: назови один конкретный нейрон и что с "
     "ним сейчас происходит."),
    ("ag.observe.last",
     "Your previous note was «{last}» — do not repeat it.",
     "Твоя прошлая заметка была «{last}» — не повторяй её."),

    # --- тело: как оно себя описывает ---
    ("body.describe", "lit {lit}/{size} neurons, energy {energy}; loudest {names}",
     "зажжено {lit}/{size} нейронов, энергия {energy}; громче всех {names}"),
    # ЖИВОСТЬ ЗА ПРОМЕЖУТОК (v0.32.0): снимок мгновения говорил «энергия 0.0000», хотя
    # тело дышало — импульс гаснет за секунду, а такт идёт раз в 30. Модель читала это
    # как «я умираю» и 16 раз подряд писала «энергия на исходе, ползу искать еду».
    # «СЕЙЧАС» И «ЗА ТАКТ» — РАЗНЫЕ ВЕЩИ (v0.32.0). describe() несёт мгновенную
    # энергию (среднее возбуждение в ЭТО мгновение), и рядом с живостью она только
    # мешала: модель видела «energy 0.0000» и говорила «энергия на исходе». Поэтому
    # телу в промпте мысли дана отдельная строка без энергии — «сейчас» и «за такт».
    ("body.now", "right now {lit}/{size} neurons are lit",
     "сейчас зажжено {lit} из {size} нейронов"),
    ("body.live",
     "this tick: {n} impulses, peak {peak}/{size} neurons lit, over {sec} s",
     "за этот такт: импульсов {n}, пик {peak} из {size} нейронов, за {sec} с"),
    ("body.quiet", "quiet", "тихо"),
    ("body.intent.idle", "the body is calm", "тело спокойно"),
    ("body.intent.act", "motor neurons are excited", "возбуждены мотонейроны"),
    ("body.intent.listen", "sensors are excited, motor neurons are silent",
     "возбуждены сенсоры, мотонейроны молчат"),

    # --- роли и зоны ---
    ("role.sensory", "sensory", "сенсорный"),
    ("role.inter", "interneuron", "вставочный"),
    ("role.motor", "motor", "мотонейрон"),
    ("role.polymodal", "polymodal", "полимодальный"),
    ("role.other", "other", "прочее"),
    ("role.unknown", "unknown", "неизвестно"),
    ("zone.head", "head", "голова"),
    ("zone.mid", "middle", "середина"),
    ("zone.tail", "tail", "хвост"),

    # --- отчёт: общая рамка ---
    ("rep.when", "when: {when}", "когда: {when}"),
    ("rep.mission_id", " · mission {id}", " · миссия {id}"),
    ("rep.failed", "FAILED: {error}", "НЕ ВЫШЛО: {error}"),
    ("rep.error_line", "{kind}: {error}", "{kind}: {error}"),
    ("rep.rule", "─" * 66, "─" * 66),
    ("rep.read_failed", "cannot read the report: {name}", "отчёт не прочитать: {name}"),
    ("rep.section.found", "IN SHORT, WHAT WAS FOUND", "КОРОТКО, ЧТО НАШЛОСЬ"),
    ("rep.section.shares", "SHARES (names only, permissions NOT tested)",
     "ОБЩИЕ ПАПКИ (видны именами, права НЕ проверялись)"),
    ("rep.section.hosts", "HOSTS ONE BY ONE", "ХОСТЫ ПО ОДНОМУ"),
    ("rep.section.meaning", "WHAT THIS MEANS", "ЧТО ЭТО ЗНАЧИТ"),

    # --- отчёт: аудит сети ---
    ("rep.audit.head", "NETWORK AUDIT {cidr}", "АУДИТ СЕТИ {cidr}"),
    ("rep.audit.alive_own", "live hosts: {count} (own network — the door let it through)",
     "живых хостов: {count} (сеть своя — дверь пропустила)"),
    ("rep.audit.alive", "live hosts: {count}", "живых хостов: {count}"),
    ("rep.audit.nobody",
     "Nobody answered: either the network is empty, or ICMP and ports are closed.",
     "Никто не отозвался: либо сеть пустая, либо ICMP и порты прикрыты."),
    ("rep.audit.nothing_open", "  nothing open", "  ничего открытого"),
    ("rep.hosts_line", "{port} {count} host(s): {where}", "{port} {count} хост(ов): {where}"),
    ("rep.host_open", "{ip}   open: {ports}", "{ip}   открыто: {ports}"),
    ("rep.nothing", "nothing", "ничего"),
    ("rep.shares_line", "      shares: {shares}", "      общие папки: {shares}"),
    ("rep.nothing_noteworthy", "  nothing noteworthy", "  ничего примечательного"),
    ("rep.port_unknown", "{port} (service not recognised)", "{port} (служба не опознана)"),
    ("rep.no_write_probe",
     "Write permissions were NOT tested in this audit: that is a separate mission "
     "write_probe — it writes its marker file into shares, reads it back and removes it at once.",
     "Права на запись в этом аудите НЕ проверялись: это отдельная миссия "
     "write_probe — она пишет свой файл-маркер в общих папках, читает его "
     "обратно и сразу убирает."),
    ("rep.banner.http_answer", "answer: {code}", "ответ: {code}"),
    ("rep.banner.http_server", "introduced itself: {server}", "представился: {server}"),
    ("rep.banner.http_silent", "the web answered, but did not name itself in human words",
     "веб ответил, но по-человечески не назвался"),
    ("rep.banner.ssh", "SSH named itself: {name}", "SSH назвался: {name}"),
    ("rep.banner.smb", "SMB stays silent to a greeting (that is normal)",
     "SMB молчит в ответ на приветствие (это нормально)"),
    ("rep.banner.other", "named itself: {text}", "назвался: {text}"),

    # --- отчёт: службы и их смысл ---
    ("svc.21", "FTP — file transfer", "FTP — передача файлов"),
    ("svc.22", "SSH — remote console", "SSH — удалённая консоль"),
    ("svc.23", "Telnet — console without encryption", "Telnet — консоль без шифра"),
    ("svc.25", "SMTP — mail (sending)", "SMTP — почта (отправка)"),
    ("svc.53", "DNS — names", "DNS — имена"),
    ("svc.80", "HTTP — web", "HTTP — веб"),
    ("svc.110", "POP3 — mail (receiving)", "POP3 — почта (получение)"),
    ("svc.135", "RPC — Windows services", "RPC — службы Windows"),
    ("svc.139", "NetBIOS — Windows networking", "NetBIOS — сеть Windows"),
    ("svc.143", "IMAP — mail", "IMAP — почта"),
    ("svc.443", "HTTPS — web with encryption", "HTTPS — веб с шифром"),
    ("svc.445", "SMB — Windows shares", "SMB — общие папки Windows"),
    ("svc.1433", "MSSQL — database", "MSSQL — база данных"),
    ("svc.3306", "MySQL — database", "MySQL — база данных"),
    ("svc.3389", "RDP — remote desktop", "RDP — удалённый рабочий стол"),
    ("svc.5432", "PostgreSQL — database", "PostgreSQL — база данных"),
    ("svc.5900", "VNC — remote screen", "VNC — удалённый экран"),
    ("svc.8080", "HTTP — web (other port)", "HTTP — веб (другой порт)"),
    ("mean.22", "console: only with a login and a password (or a key)",
     "консоль: зайдёт только с логином и паролем (или ключом)"),
    ("mean.445", "shares: this is where a copy lands, IF writing is allowed",
     "общие папки: тут и лежит копия, ЕСЛИ пустят на запись"),
    ("mean.139", "the same Windows network over the old protocol",
     "та же сеть Windows по старому протоколу"),
    ("mean.443", "web with encryption: a settings page or a site",
     "веб с шифром: страница настроек или сайт"),
    ("mean.80", "web: a settings page or a site", "веб: страница настроек или сайт"),
    ("mean.8080", "web on a non-standard port", "веб на нестандартном порту"),
    ("mean.3389", "remote desktop: they log in with a username and a password",
     "удалённый стол: подключаются с логином и паролем"),
    ("mean.21", "files over FTP: often without encryption", "файлы по FTP: часто без шифра"),
    ("mean.23", "console without encryption — outdated and dangerous",
     "консоль без шифра — устарело и опасно"),

    # --- отчёт: сроки сертификатов ---
    ("rep.certs.head", "CERTIFICATE EXPIRY", "СРОКИ СЕРТИФИКАТОВ"),
    ("rep.certs.nothing", "Nothing was checked: no encrypted services in the list.",
     "Ничего не проверено: служб с шифром в списке не нашлось."),
    ("rep.certs.summary", "services checked: {checked} · expiring within {days} days: {bad}",
     "проверено служб: {checked} · истекают в ближайшие {days} дней: {bad}"),
    ("rep.certs.by_date", "BY DATE, THE NEAREST FIRST", "ПО СРОКУ, БЛИЖНИЕ ПЕРВЫМИ"),
    ("rep.certs.noreply", "  {where}  no answer: {error}", "  {where}  не ответил: {error}"),
    ("rep.certs.line", "  {where}  {flag}days left: {days}  (until {until})",
     "  {where}  {flag}осталось дней: {days}  (до {until})"),
    ("rep.certs.flag", "ATTENTION ", "ВНИМАНИЕ "),
    ("rep.certs.subject", "        issued to: {value}", "        выдан кому: {value}"),
    ("rep.certs.issuer", "        issued by: {value}", "        кем:        {value}"),
    ("rep.certs.names", "        names: {value}", "        имена: {value}"),
    ("rep.certs.meaning",
     "  A certificate expires silently: first the browser starts to complain, then the\n"
     "  service simply stops opening. This is the one tool worth running on a schedule —\n"
     "  and comparing with the previous run.\n"
     "  Read-only: a TLS handshake, not a single write to someone else's machine.",
     "  Сертификат истекает молча: сначала браузер начинает ругаться, потом служба\n"
     "  просто перестаёт открываться. Это единственный инструмент, который стоит\n"
     "  запускать по расписанию — и сравнивать с прошлым разом.\n"
     "  Только чтение: рукопожатие TLS, ни одной записи на чужую машину."),

    # --- отчёт: тест записи ---
    ("rep.wp.head", "WRITE PROBE IN SHARES", "ТЕСТ ЗАПИСИ В ОБЩИХ ПАПКАХ"),
    ("rep.wp.nothing", "No shares found — there was nothing to check.",
     "Общих папок не нашлось — проверять было нечего."),
    ("rep.wp.summary", "folders checked: {checked} · allow writing: {writable}",
     "проверено папок: {checked} · пускают на запись: {writable}"),
    ("rep.wp.by_folder", "FOLDER BY FOLDER", "ПО ПАПКАМ"),
    ("rep.wp.line", "  {path}  -> {mark}", "  {path}  -> {mark}"),
    ("rep.wp.state.writable", "ALLOWS WRITING", "ПУСКАЕТ ПИСАТЬ"),
    ("rep.wp.state.read_only", "read only", "только чтение"),
    ("rep.wp.state.denied", "refuses", "не пускает"),
    ("rep.wp.state.missing", "no such folder", "папки нет"),
    ("rep.wp.state.refused", "the door did not let it through", "дверь не пустила"),
    ("rep.wp.state.off", "the probe is off in the settings", "тест выключен в настройках"),
    # Пишет, но обратное чтение не совпало: состояние есть в коде write_probe, а
    # в словаре его не было — в отчёте торчала сырая строка (находка QA Ореста).
    ("rep.wp.state.writable_unverified",
     "writes, but the read-back did not match (unverified)",
     "пишет, но обратное чтение не совпало (не подтверждено)"),
    ("rep.wp.why", "        why: {detail}", "        почему: {detail}"),
    ("rep.wp.meaning",
     "  The probe wrote ONE small marker file, read it back and deleted it at once.\n"
     "  Every attempt is in the journal: host, folder, full path, how it ended.\n"
     "  «Allows writing» is not a verdict, it is a place where a copy can be put.",
     "  Тест писал ОДИН маленький файл-маркер, читал его обратно и сразу удалял.\n"
     "  Каждая попытка записана в журнал: хост, папка, полный путь, чем кончилось.\n"
     "  «Пускает писать» — не приговор, а место, куда копию можно положить."),

    # --- отчёт: что изменилось ---
    ("rep.diff.head", "WHAT CHANGED SINCE THE LAST AUDIT", "ЧТО ИЗМЕНИЛОСЬ С ПРОШЛОГО АУДИТА"),
    ("rep.diff.was", "was:  {cidr} at {when} (live {alive})",
     "было:  {cidr} в {when} (живых {alive})"),
    ("rep.diff.now", "now:  {cidr} at {when} (live {alive})",
     "стало: {cidr} в {when} (живых {alive})"),
    ("rep.diff.none", "No changes: the network looks the same as last time.",
     "Изменений нет: сеть выглядит так же, как в прошлый раз."),
    ("rep.diff.appeared", "APPEARED", "ПОЯВИЛИСЬ"),
    ("rep.diff.gone", "GONE (did not answer)", "ПРОПАЛИ (не отозвались)"),
    ("rep.diff.changed", "CHANGED", "ИЗМЕНИЛИСЬ"),
    ("rep.diff.open", "  {ip}  open: {ports}", "  {ip}  открыто: {ports}"),
    ("rep.diff.was_open", "  {ip}  was open: {ports}", "  {ip}  было открыто: {ports}"),
    ("rep.diff.opened", "opened: {ports}", "открылось: {ports}"),
    ("rep.diff.closed", "closed: {ports}", "закрылось: {ports}"),
    ("rep.diff.shares_new", "new shares: {shares}", "новые общие папки: {shares}"),
    ("rep.diff.shares_gone", "shares gone: {shares}", "пропали общие папки: {shares}"),
    ("rep.diff.meaning",
     "  This is the difference a script notices only if somebody runs it twice and\n"
     "  compares the output by eye. A creature lives between the runs and compares itself.",
     "  Так выглядит разница, которую скрипт замечает только если его запустят дважды\n"
     "  и кто-то сравнит глазами. Существо живёт между запусками и сравнивает само."),

    # --- итог миссии одной строкой ---
    ("rep.sum.audit", "audit {cidr}: {bits}", "аудит {cidr}: {bits}"),
    ("rep.sum.live", "live hosts {count}", "живых хостов {count}"),
    ("rep.sum.shares_at", "shares at {hosts}", "общие папки у {hosts}"),
    ("rep.sum.audit_failed", "the audit did not go through: {error}",
     "аудит не вышел: {error}"),
    ("rep.sum.certs_none", "certs: no encrypted services found",
     "certs: служб с шифром не нашлось"),
    ("rep.sum.certs", "certs: checked {ok}/{total}", "certs: проверено {ok}/{total}"),
    ("rep.sum.certs_soon", "; expiring soon: {list}", "; истекают скоро: {list}"),
    ("rep.sum.certs_far", "; the nearest deadline is more than a month away",
     "; ближайший срок дальше, чем за месяц"),
    ("rep.sum.certs_days", "{host} ({days} days)", "{host} (дней {days})"),
    ("rep.sum.wp_none", "write_probe: no shares found", "write_probe: общих папок не нашлось"),
    ("rep.sum.wp_nowhere", "write_probe: checked {count} folders — writing is allowed nowhere",
     "write_probe: проверил {count} папок — писать никуда нельзя"),
    ("rep.sum.wp_ok", "write_probe: checked {count} folders, writing is allowed in {list}",
     "write_probe: проверил {count} папок, пускают писать {list}"),
    ("rep.sum.diff_same", "diff: network {cidr} is unchanged since the last audit",
     "diff: сеть {cidr} не изменилась с прошлого аудита"),
    ("rep.sum.diff", "diff: appeared {new}, gone {gone}, changed {changed}",
     "diff: появились {new}, пропали {gone}, изменились {changed}"),
    ("rep.sum.diff_new", "; new: {list}", "; новые: {list}"),
    ("rep.sum.diff_failed", "diff did not go through: {error}", "diff не вышел: {error}"),
    ("rep.sum.certs_failed", "certs did not go through: {error}", "certs не вышел: {error}"),
    ("rep.sum.wp_failed", "write_probe did not go through: {error}",
     "write_probe не вышел: {error}"),
    ("rep.title.alive", " · live {count}", " · живых {count}"),
    ("rep.title.certs", " · checked {checked} · expiring {bad}",
     " · проверено {checked} · истекают {bad}"),
    ("rep.title.wp", " · folders {count} · writable {writable}",
     " · папок {count} · пишет в {writable}"),
    ("rep.title.diff", " · new hosts {new} · gone {gone}",
     " · новых хостов {new} · пропало {gone}"),
    ("rep.title.remote", " · ways open {ways} · worked {worked}",
     " · путей открыто {ways} · сработал {worked}"),
    ("rep.remote.target", "host: {host}   command: {command}",
     "хост: {host}   команда: {command}"),
    ("rep.remote.no_command", "(no command — only looking for ways)",
     "(команды нет — только поиск путей)"),
    ("rep.remote.ways", "ways found:", "найденные пути:"),
    ("rep.remote.row", "  port {port} · {method} — {state}",
     "  порт {port} · {method} — {state}"),
    ("rep.remote.open", "open", "открыт"),
    ("rep.remote.closed", "closed", "закрыт"),
    ("rep.remote.attempts", "attempts made:", "сделанные попытки:"),
    ("rep.remote.attempt_row", "  {method} — {state}: {output}",
     "  {method} — {state}: {output}"),
    ("rep.remote.next", "what to do: {hint}", "что делать: {hint}"),
    ("rep.sum.remote_failed", "remote control failed: {error}",
     "удалённое управление не вышло: {error}"),
    ("rep.sum.remote_none", "remote control {host}: no way in — none of the known ways is open",
     "удалённое управление {host}: войти нечем — ни один из известных путей не открыт"),
    ("rep.sum.remote_probe", "remote control {host}: ways open {ways}; give a login with the command",
     "удалённое управление {host}: открыты пути {ways}; дай логин и команду"),
    ("rep.sum.remote_ok", "remote control {host}: {method} worked — {out}",
     "удалённое управление {host}: сработал {method} — {out}"),
    ("rep.sum.remote_denied", "remote control {host}: {method} did not let us in — see the report",
     "удалённое управление {host}: {method} не пустил — смотри отчёт"),
    ("rep.sum.remote_stopped", "remote control {host}: stopped by the operator",
     "удалённое управление {host}: остановлено оператором"),
    ("rep.sum.audit_stopped", "network audit {cidr}: stopped by the operator, hosts passed {done}",
     "аудит сети {cidr}: остановлен оператором, хостов пройдено {done}"),

    # --- консоль (run.py): то, что читает человек ---
    ("cli.no_reports", "no reports yet — they are written by the net_audit mission",
     "отчётов пока нет — их пишет миссия net_audit"),
    ("cli.rebuilt", "human reports rebuilt: {count}", "человеческих отчётов пересобрано: {count}"),
    ("cli.no_roles_map", "no map yet — build it: python run.py roles --build",
     "карты нет — собери: python run.py roles --build"),
    ("cli.copied", "  copied {name} ({mb} MB)", "  скопировано {name} ({mb} МБ)"),
    ("cli.extra_servers", "extra servers removed: {killed}", "лишних серверов убрано: {killed}"),
    ("cli.listening", "listening right now: {ports}", "слушают сейчас: {ports}"),
    # отказ консоли по незнакомому имени инструмента: команда — техническое слово,
    # поэтому сама она английская, а фраза вокруг переводится
    ("cli.no_tool", "no such tool: {kind} — have: {kinds}",
     "нет такого инструмента: {kind} — есть: {kinds}"),

    # --- шаги включения мозга (их показывает вкладка «Мозг») ---
    ("brain.step.extra_server", "removed an extra server on my port: {list}",
     "убрала лишний сервер на своём порту: {list}"),
    ("brain.step.copying", "copying my own brain (one-time)…",
     "копирую свой мозг к себе (один раз)…"),
    ("brain.step.provision", "provision: copied {copied}, skipped {skipped}, {mb} MB",
     "копир: скопировано {copied}, пропущено {skipped}, {mb} МБ"),
    ("brain.step.source_missing", "source not found: {list}", "источник не найден: {list}"),
    ("brain.step.copy_errors", "copy errors: {list}", "ошибки копирования: {list}"),
    ("brain.step.starting", "starting my own llama-server…", "поднимаю свой llama-server…"),
    ("brain.step.up", "server is up", "сервер поднялся"),
    ("brain.step.down", "server did NOT come up — see the journal",
     "сервер НЕ поднялся — смотри журнал"),
    ("brain.step.alive", "brain alive: {endpoint}", "мозг жив: {endpoint}"),
    ("brain.step.off", "brain off, server stopped", "мозг выключен, сервер погашен"),

    # --- что изменилось в настройках (строки для вкладки «Настройки») ---
    ("set.bad_choice", "{field}: unknown value «{value}» — left as it was",
     "{field}: неизвестное значение «{value}» — оставила как было"),
    ("set.on", "on", "включено"),
    ("set.off", "off", "выключено"),
    ("set.changed.lang", "language: {value}", "язык: {value}"),
    ("set.changed.theme", "theme: {value}", "тема: {value}"),
    ("set.changed.tick", "tick: {value} s", "такт: {value} с"),
    ("set.changed.fps", "body frame: {value} ms", "кадр тела: {value} мс"),
    ("set.changed.idle", "thinking out loud: {value}", "думать вслух: {value}"),
    ("set.changed.propose", "brain offers work: {value}", "мозг предлагает работу: {value}"),
    ("set.changed.propose_confirm", "confirmation of the brain's work: {value}",
     "подтверждение работ мозга: {value}"),
    ("set.changed.write_probe", "write probe: {value}", "тест записи: {value}"),
    ("set.changed.ports", "ports: {value}", "порты: {value}"),
    ("set.section.brain", "Brain — own model server", "Мозг — свой сервер модели"),
    ("set.section.colors", "Body colours", "Цвета тела"),
    ("set.brain_model", "Model", "Модель"),
    ("set.brain_device", "Compute", "Счёт"),
    ("set.brain_ngl", "Layers on GPU", "Слоёв на видеокарте"),
    ("set.brain_port", "Server port", "Порт сервера"),
    ("set.brain.hint",
     "Models are read from the creature's own folder (models/brain) — drop a file there and it "
     "appears in the list. GPU counts fast but takes video memory; CPU does not touch the GPU at "
     "all and is slower. A change takes effect after the brain is stopped and started again "
     "(buttons on the Brain tab).",
     "Модели берутся из папки существа (models/brain) — положил туда файл, он появился в "
     "списке. Видеокарта считает быстро, но занимает видеопамять; процессор не трогает "
     "видеокарту вовсе и медленнее. Правка вступает в силу после того, как мозг погасить и "
     "поднять заново (кнопки на вкладке «Мозг»)."),
    ("device.gpu", "GPU", "Видеокарта"),
    ("device.cpu", "CPU (does not touch the GPU)", "Процессор (не трогает видеокарту)"),
    ("set.legend.hint",
     "The legend lives here, not under the body: it is not needed on screen all the time. It "
     "follows the colouring picked under the body (roles / transmitters / zones).",
     "Легенда живёт здесь, а не под телом: смотреть на неё постоянно незачем. Она следует за "
     "раскраской, выбранной под телом (роли / медиаторы / зоны)."),
    ("set.changed.brain_model", "model: {value}", "модель: {value}"),
    ("set.changed.brain_device", "compute: {value}", "счёт: {value}"),
    ("set.changed.brain_ngl", "layers on GPU: {value}", "слоёв на видеокарте: {value}"),
    ("set.changed.brain_port", "server port: {value}", "порт сервера: {value}"),
    ("set.changed.brain_restart",
     "the brain was stopped: model or compute changed — start it again on the Brain tab",
     "мозг погашен: сменились модель или счёт — подними его заново на вкладке «Мозг»"),
    ("set.changed.none", "nothing changed", "изменений нет"),

    # --- консоль: подсказки команд (это интерфейс, значит переводится) ---
    ("cli.desc", "Synapse — an organism with a connectome and a brain",
     "Synapse — организм с коннектомом и мозгом"),
    ("cli.help.init", "first build", "первая сборка"),
    ("cli.help.status", "what the organism feels right now", "что организм чувствует"),
    ("cli.help.roles", "the exact neuron map", "точная карта нейронов"),
    ("cli.help.roles_build", "build the map from the atlas", "собрать карту из атласа"),
    ("cli.help.panel", "graphical control panel (window)", "графическая панель (окно)"),
    ("cli.help.seconds", "how many seconds to run in --selftest",
     "сколько секунд работать в --selftest"),
    ("cli.help.tick", "live a few ticks", "прожить такты"),
    ("cli.help.run", "live autonomously (Ctrl+C stops)", "жить автономно (Ctrl+C — стоп)"),
    ("cli.help.mission", "mission queue", "очередь миссий"),
    ("cli.help.kind", "tool name (net_audit, write_probe, certs, diff, remote, stimulus, note, ask)",
     "имя инструмента (net_audit, write_probe, certs, diff, remote, stimulus, note, ask)"),
    ("cli.help.host", "host for write_probe / certs", "хост для write_probe / certs"),
    ("cli.help.ports", "comma-separated ports (certs)", "порты через запятую (certs)"),
    ("cli.help.note", "note for the mission", "заметка к миссии"),
    ("cli.help.command", "command to run on the remote host (remote)",
     "команда для выполнения на удалённом хосте (remote)"),
    ("cli.help.user", "login for remote control", "логин для удалённого управления"),
    ("cli.help.password", "password for remote control", "пароль для удалённого управления"),
    ("cli.help.scan", "a one-off network audit, past the queue", "разовый аудит сети мимо очереди"),
    ("cli.help.reports", "reports in human language", "отчёты человеческим языком"),
    ("cli.help.show", "show report N in full (1 = latest)", "показать отчёт N целиком (1 = последний)"),
    ("cli.help.limit", "how many to list", "сколько показать в списке"),
    ("cli.help.rebuild", "rebuild the .txt files from JSON", "пересобрать .txt из JSON"),
    ("cli.help.brain", "brain: state and my own copy", "мозг: состояние и своя копия"),
    ("cli.help.action", "what to do with the brain", "что сделать с мозгом"),

    # --- запасная панель: подробные строки (нейрон, мозг, состояние) ---
    ("tab.neurons", "Neurons", "Нейроны"),
    ("tk.state.awake", "alive", "живёт"),
    ("tk.state.asleep", "asleep", "спит"),
    ("tk.status_line",
     "energy {energy} · lit {lit}/{size} · ticks {ticks} · wakeups today {wakeups}/{cap} · "
     "{fps} fps ({ms} ms/frame) · {engine}, edges {edges}{extra}",
     "энергия {energy} · зажжено {lit}/{size} · тактов {ticks} · пробуждений сегодня "
     "{wakeups}/{cap} · {fps} кадр/с ({ms} мс/кадр) · {engine}, связей {edges}{extra}"),
    ("tk.renderer_extra",
     " · picture {w}x{h} in window {cw}x{ch}, rebuilds {rebuilds}, sprites {sprites}, "
     "frames skipped {skipped}",
     " · картинка {w}x{h} в окне {cw}x{ch}, пересборок {rebuilds}, спрайтов {sprites}, "
     "замерло кадров {skipped}"),
    ("tk.engine.picture", "picture", "картинкой"),
    ("tk.engine.canvas", "canvas", "canvas"),
    ("tk.tick_mission", "mission {id}: {status} — {text}", "миссия {id}: {status} — {text}"),
    ("tk.tick_line", "tick {tick}: {text}", "такт {tick}: {text}"),
    ("tk.bytes", "{title}   ({size} B)", "{title}   ({size} б)"),
    ("tk.neuron_head", "{name}  —  {role} ({role_label})", "{name}  —  {role} ({role_label})"),
    ("tk.neuron_types", "atlas types : {value}", "типы атласа : {value}"),
    ("tk.neuron_nt", "transmitter : {nt}   receptors: {receptors}",
     "медиатор    : {nt}   рецепторов: {receptors}"),
    ("tk.neuron_zone", "body zone   : {zone}   links: {deg} (in {w_in}, out {w_out})",
     "зона тела   : {zone}   связей: {deg} (вход {w_in}, выход {w_out})"),
    ("tk.neuron_hub", "  · HUB", "  · ХАБ"),
    ("tk.neuron_activity", "activity    : {activity}   coordinates: {xyz}",
     "активность  : {activity}   координаты: {xyz}"),
    ("tk.neuron_atlas", "atlas       : {value}", "атлас       : {value}"),
    ("tk.neuron_source", "source      : {value}", "источник    : {value}"),
    ("tk.brain_mode_line", "mode        : {mode}   server answers: {answer}",
     "режим        : {mode}   сервер отвечает: {answer}"),
    ("tk.brain_endpoint", "endpoint    : {value}", "endpoint     : {value}"),
    ("tk.brain_model_line", "model       : {value}", "модель       : {value}"),
    ("tk.brain_model_local", "model is my own: {value}", "модель у себя: {value}"),
    ("tk.brain_runtime_local", "runtime is my own: {value}", "рантайм у себя: {value}"),
    ("tk.brain_self_line", "my own copy : {value}", "своя копия   : {value}"),
    ("tk.brain_dir_line", "folder      : {value}", "каталог      : {value}"),

    ("brain.hint.provision", "run provision first: no local copy yet",
     "сначала provision: нет своей копии"),
    ("brain.note.no_copy", "no local copy of the runtime/model — provision is needed",
     "нет своей копии рантайма/модели — нужен provision"),
    ("brain.note.extra", "removed an extra server on my port: {list}",
     "убрала лишний сервер на порту: {list}"),
    ("brain.note.start_failed", "cannot start the server: {error}", "не поднять сервер: {error}"),
    ("brain.note.crashed", "the server died with code {code}", "сервер упал с кодом {code}"),
    ("brain.note.no_answer", "the server did not answer in time",
     "сервер не ответил за отведённое время"),
    ("tk.body_pulse", "body: {text}", "тело: {text}"),

    # --- запасная панель (tkinter) ---
    ("tk.title", "Synapse — {name} · connectome and brain", "Synapse — {name} · коннектом и мозг"),
    ("tk.asleep", "· asleep", "· спит"),
    ("tk.brain_off", "brain: off", "мозг: выкл"),
    ("tk.brain_on", "brain: on", "мозг: вкл"),
    ("tk.brain_alive", " · alive", " · жив"),
    ("tk.body_log", "body: {nodes} neurons, {edges} edges", "тело: {nodes} нейронов, {edges} связей"),
    ("tk.roles_log", "roles: {roles}", "роли: {roles}"),
    ("tk.brain_was_on", "the brain was on last time — I will bring the server up",
     "мозг был включён в прошлый раз — подниму сервер"),
    ("tk.ready", "ready", "готов"),
    ("tk.label.what", "What to do", "Что сделать"),
    ("tk.label.subnet", "Subnet", "Подсеть"),
    ("tk.label.task", "Task text", "Текст задания"),
    ("tk.col.id", "mission", "миссия"),
    ("tk.result_full", "full result of the selected mission", "итог выбранной миссии целиком"),
    ("tk.neuron.name", "Name:", "Имя:"),
    ("tk.btn.find", "Find", "Найти"),
    ("tk.col.role", "role", "роль"),
    ("tk.col.count", "n", "шт"),
    ("tk.col.hubs", "hubs", "хабы"),
    ("tk.col.examples", "examples", "примеры"),
    ("tk.btn.provision", "Copy the model to me", "Копия модели к себе"),
    ("tk.head_label", "head", "голова"),
    ("tk.tail_label", "tail", "хвост"),
    ("tk.live_log", "the organism went to live on its own", "организм пошёл жить сам"),
    ("tk.pause_log", "the organism is paused (the body still breathes)",
     "организм на паузе (тело всё равно дышит)"),
    ("tk.busy", "busy with something else — the tick will wait", "занята другим делом — такт подождёт"),
    ("tk.pulse_default", "operator pulse", "пульс оператора"),
    ("tk.colouring", "colouring: {how}", "раскраска: {how}"),
    ("tk.plan_ok", "you can give it: {why}", "можно давать: {why}"),
    ("tk.plan_no", "not yet: {why}", "пока нельзя: {why}"),
    ("tk.not_queued", "mission not queued ({kind}): look at the check line",
     "миссия не поставлена ({kind}): смотри строку проверки"),
    ("tk.queued_log", "mission {id} ({what}) is queued — it will take it on the next tick",
     "миссия {id} ({what}) в очереди — возьмёт на ближайшем такте"),
    ("tk.queued_hint", "queued: {id} ({what})", "поставлена: {id} ({what})"),
    ("tk.my_net", "my subnet: {cidr}", "моя подсеть: {cidr}"),
    ("tk.not_found", "not found", "не нашла"),
    ("tk.deleted", "mission deleted: {id}", "миссия удалена: {id}"),
    ("tk.deleted_running",
     "mission {id}: the work is being stopped and the record deleted — a partial report stays",
     "миссия {id}: останавливаю работу и удаляю запись — частичный отчёт останется"),
    ("tk.result_missing", "{id}: the record is gone (it may have been dropped)",
     "{id}: запись не нашлась (её могли снять)"),
    ("tk.created", "created: {created}   finished: {finished}",
     "создана: {created}   окончена: {finished}"),
    ("tk.task_line", "task: {task}", "задание: {task}"),
    ("tk.no_result_yet", "no result yet", "итога ещё нет"),
    ("tk.report_log", "report: {title}", "отчёт: {title}"),
    ("tk.pick_report", "pick a report in the list first", "сначала выбери отчёт в списке"),
    ("tk.no_text_file", "no human-readable file: {name} — open the JSON version",
     "человеческого файла нет: {name} — открой JSON-версию"),
    ("tk.opened", "opened {name}", "открыт {name}"),
    ("tk.open_failed", "cannot open the file: {error}", "не открыть файл: {error}"),
    ("tk.no_neuron", "no such neuron: {name}", "нет такого нейрона: {name}"),
    ("tk.ask_default", "how do you feel?", "как ты себя чувствуешь?"),
    ("tk.asking", "asking…", "спрашиваю…"),
    ("tk.brain_self_yes", "yes (the creature is autonomous)", "есть (существо автономно)"),
    ("tk.brain_self_no", "NO — provisioning is needed", "НЕТ — нужен копир"),
    ("tk.yes", "yes", "да"),
    ("tk.no", "no", "нет"),
    ("tk.busy_other", "already busy with another job, wait", "уже занята другим делом, подожди"),
    ("tk.started_bg", "started in the background…", "начато в фоне…"),
    ("tk.copied", "copied {name} ({mb} MB)", "скопировано {name} ({mb} МБ)"),
    ("tk.error", "error: {error}", "ошибка: {error}"),

    # --- ПРОЗА ИНСТРУМЕНТОВ: то, что они пишут В ОТЧЁТ.
    # До 25.09.2026 эти строки лежали жёстко в коде инструментов (certs/diff/
    # write_probe/net_scan) — и в отчёте при английском интерфейсе оставалась
    # русская фраза. Теперь и они идут через словарь, как всякий интерфейсный текст.
    ("err.door", "the door refused", "дверь не пустила"),
    ("err.no_host", "no host given", "хост не указан"),
    ("err.remote_off", "remote control is switched off in the settings",
     "удалённое управление выключено в настройках"),
    # Состояния удалённого управления. Печатаются и в отчёт, и в строку ответа:
    # слово «denied» оператору ничего не говорит, а «отказ в доступе» — говорит.
    ("rm.state.found", "available", "доступен"),
    ("rm.state.ok", "worked", "сработал"),
    ("rm.state.denied", "refused access", "отказ в доступе"),
    ("rm.state.unreachable", "no answer", "не ответил"),
    ("rm.state.skipped", "not tried", "не пробовала"),
    ("rm.hint", "give a login and a password with the command — and the being will run it",
     "дай логин с паролем и команду — и существо её выполнит"),
    ("rm.bad_host", "unexpected characters in the address — the command was not built",
     "в адресе недопустимые символы — команду не собрала"),
    ("rm.need_windows", "this way needs a Windows machine to start from",
     "этот способ требует Windows-машину как отправителя"),
    ("rm.no_ssh", "no ssh client on this machine", "клиент ssh на этой машине не найден"),
    ("rm.ssh_password", "ssh lets in by key only: a password cannot be passed this way",
     "ssh пускает только по ключу: пароль так не передать"),
    ("rm.not_a_runner", "this way only reports about itself, a command cannot ride it",
     "этот путь только сообщает о себе, команду им не выполнить"),
    ("rm.timed_out", "did not answer within the timeout", "не ответил за отведённое время"),
    ("rm.wmi_started", "the process was created on the remote machine",
     "процесс на удалённой машине создан"),
    ("err.no_audit", "no network audit yet — run net_audit first",
     "нет аудита сети — сначала net_audit"),
    ("err.no_subnet", "could not find my own subnet", "не нашла свою подсеть"),
    ("err.certs.no_cert", "the service gave out no certificate",
     "служба не отдала сертификат"),
    ("err.certs.decode", "cannot parse the certificate: {reason}",
     "сертификат не разобрать: {reason}"),
    ("err.certs.no_tls", "no service answered a handshake",
     "ни одна служба не ответила рукопожатием"),
    ("err.diff.need_two", "no two audits to compare — run net_audit first",
     "нет двух аудитов для сравнения — сначала net_audit"),
    ("err.diff.have", "two network audits are needed: only {count} so far",
     "нужны два аудита сети: пока их {count}"),
    ("err.wp.no_shares", "the last audit shows no shared folders",
     "в последнем аудите общих папок не видно"),
    ("src.from_mission", "from the mission", "из миссии"),
    ("src.from_mission_host", "from the mission: {host}", "из миссии: {host}"),
    ("src.from_audit", "from the last audit {cidr}", "из последнего аудита {cidr}"),
    ("src.no_tls", "no services with encryption found", "служб с шифром не нашлось"),
    ("wp.off", "the write test is switched off in settings",
     "тест записи выключен в настройках"),
    ("wp.reserved", "a service share — we do not touch those",
     "служебная папка — не трогаем"),
    ("wp.unverified", "the write went through but read back differently",
     "запись прошла, но прочиталось иначе"),
    ("wp.not_removed", " | the marker cannot be removed: {error}",
     " | маркер не убрать: {error}"),
    ("ag.report_tail", "; report: {path}", "; отчёт: {path}"),

    # --- ОТВЕТ НА НАЖАТИЕ. Найдено 25.09.2026 по жалобе Стаса «нажимаю на них и
    # ничего не происходит»: в запасной tkinter-панели каждое действие писалось в
    # лог («тело: …», «раскраска: …», «такт N: …»), а в Qt-панели этого не было
    # вовсе — кнопки работали, но молчали. Теперь каждое нажатие отвечает строкой.
    ("act.live", "live: a tick every {seconds} s — it takes missions and thinks. The body breathes all the time.",
     "живу: такт каждые {seconds} с — беру миссии и думаю. Тело дышит всё время."),
    ("act.live_stream",
     "live: continuous flow — tick flows into tick, no sleep between them. The body breathes all "
     "the time; the mind speaks as often as the pause in the settings allows — 0 means no pause "
     "at all, a stream.",
     "живу: непрерывный поток — такт перетекает в такт, без сна между ними. Тело дышит всё "
     "время, а ум говорит так часто, как разрешает пауза в настройках — 0 значит без пауз "
     "вовсе, потоком."),
    ("act.pause", "paused: no ticks and no missions. The body goes on breathing.",
     "пауза: тактов и миссий нет. Тело продолжает дышать."),
    ("act.ticking", "tick: the organism is thinking…", "такт: организм думает…"),
    ("act.pulse", "pulse to the body: {text}", "импульс телу: {text}"),
    ("act.coloring", "colouring: {how}", "раскраска: {how}"),
    ("act.reset", "view reset: angle and scale are back to the start",
     "вид сброшен: угол и масштаб как в начале"),
    ("act.busy", "already busy with the previous tick — wait for it to finish",
     "уже занята прошлым тактом — дождись, пока закончит"),

    # --- подсказки на кнопках (наведение мышью) ---
    ("btn.live.tip",
     "Live: the organism ticks by schedule — it takes missions from the queue and thinks. "
     "The body breathes all the time anyway.",
     "Жить: организм тактует по расписанию — берёт миссии из очереди и думает. "
     "Тело всё равно дышит постоянно."),
    ("btn.pause.tip",
     "Pause: no more ticks, no missions are taken. The body keeps breathing — it cannot "
     "stop being alive.",
     "Пауза: тактов больше нет, миссии не берутся. Тело продолжает дышать — "
     "живым оно быть не перестаёт."),
    ("btn.step.tip", "Step: one tick right now. What came of it appears in the line under the buttons.",
     "Такт: один шаг прямо сейчас. Что вышло — в строке под кнопками."),
    ("btn.pulse.tip",
     "Pulse: inject an impulse into the sensory neurons. The touched ones glow white for a "
     "second — you can see where it landed.",
     "Импульс: впрыснуть импульс в сенсорные нейроны. Задетые секунду светятся белым — "
     "видно, куда он попал."),
    ("btn.reset_view.tip",
     "Reset view: back to the starting angle and scale (turn with the mouse, zoom with the wheel).",
     "Сброс вида: вернуть угол и масштаб как были (крутить — мышью, приближать — колесом)."),
    ("color.tip", "What the colours of the neurons mean. The legend under the body follows this choice.",
     "Что означают цвета нейронов. Легенда под телом меняется вместе с выбором."),

    # --- легенда цветов и названия раскрасок ---
    ("legend.title", "colour legend", "легенда цветов"),
    ("legend.unknown_nt", "(no transmitter)", "(без медиатора)"),
    ("coloring.role", "roles", "роли"),
    ("coloring.nt", "transmitters", "медиаторы"),
    ("coloring.zone", "zones", "зоны"),

    # --- ФИЗИОЛОГИЯ ПЕРЕДАЧИ (25.09.2026) ---
    # Технический код передачи (exc/inh/mod) в словарь НЕ идёт — он живёт в
    # synapse/pharmacology.py. Здесь только подписи для человека.
    ("nt.kind.exc", "excitatory", "возбуждающий"),
    ("nt.kind.inh", "inhibitory", "тормозной"),
    ("nt.kind.mod", "modulatory", "модулирующий"),
    ("nt.kind.unknown", "unknown", "неизвестно"),
    ("legend.physiology",
     "transmission: excitatory {exc} · inhibitory {inh} · modulatory {mod} (GABA inhibits, monoamines set tone)",
     "передача: возбуждающих {exc} · тормозных {inh} · модулирующих {mod} "
     "(GABA тормозит, моноамины задают тонус)"),
    ("legend.physiology_off",
     "transmission: without sign — physiology is off in settings",
     "передача: без знака — физиология выключена в настройках"),
    ("card.transmission", " · transmission: {kind}", " · передача: {kind}"),
]

CATALOG: dict[str, dict[str, str]] = {k: {"en": en, "ru": ru} for k, en, ru in _STRINGS}
