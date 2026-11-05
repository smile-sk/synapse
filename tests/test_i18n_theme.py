"""v0.10: один словарь на весь интерфейс, две темы, вкладка настроек.

    python tests/test_i18n_theme.py        (запускать из корня проекта)

Что здесь проверяется по-настоящему (то, что код ДЕЛАЕТ):

  * КОНТРОЛЬ первым: без ключа t() возвращает САМ КЛЮЧ, а не пустоту и не
    английский текст — иначе «всё переведено» ничего не значит;
  * словарь не пустышка: английский и русский РАЗНЫЕ, ключи не повторяются,
    наборы полей {имя} в обоих языках совпадают;
  * КАЖДЫЙ ключ, который проект реально зовёт, есть в словаре (скан исходников,
    включая f-строки вида tool.<kind>.label — их проверяем по списку команд);
  * отчёт на английском не содержит НИ ОДНОЙ кириллической буквы (ловит
    забытую русскую строку), а русский — содержит;
  * тема: QSS собирается целиком (ни одного незамещённого $имя), тёмная и
    светлая различаются, на светлой цвета тел приглушаются;
  * настройки: сохраняются в файл и применяются на ходу; плохое число не
    роняет, а возвращается ошибкой с прежним значением.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import config, i18n, missions, reports, theme   # noqa: E402
from live_data import temp_live                              # noqa: E402
from synapse.panel_core import MISSION_KIND_IDS, PanelCore    # noqa: E402

CYRILLIC = re.compile("[А-Яа-яЁё]")
#: Ключи, которые проект зовёт СОБРАННЫМИ из частей — скан видит f-строку,
#: а не имя. Их перечисляем здесь и проверяем отдельно.
BUILT_KEYS = ([f"tool.{k}.label" for k in MISSION_KIND_IDS]
              + [f"tool.{k}.hint" for k in MISSION_KIND_IDS]
              + [f"svc.{p}" for p in reports.KNOWN_PORTS]
              + [f"rep.wp.state.{s}" for s in
                 ("writable", "read_only", "denied", "missing", "refused", "off")])

FAKE_AUDIT = {
    "at": "2026-09-24T16:26:52", "cidr": "192.168.1.0/24", "mission": "m-1",
    "scanned": 254, "alive_count": 2,
    "hosts": [
        {"ip": "192.168.1.88", "open": [22, 445], "shares": ["Install", "Juli"],
         "banners": {"22": "SSH-2.0-OpenSSH_7.4", "445": ""}},
        {"ip": "192.168.1.43", "open": [3389], "shares": [], "banners": {}},
    ],
}


def sources() -> list[Path]:
    """Все исходники проекта: словарь должен покрывать их целиком."""
    out = [ROOT / "run.py"]
    for folder in (ROOT / "synapse", ROOT / "tools"):
        out += sorted(folder.rglob("*.py"))
    return [p for p in out if "__pycache__" not in str(p)]


class TestControl(unittest.TestCase):
    """Без этих проверок остальные ничего не доказывают."""

    def test_missing_key_is_visible_not_silent(self):
        """Нет ключа — на экране будет сам ключ: ошибку надо ВИДЕТЬ."""
        self.assertEqual(i18n.t("btn.такого.нет"), "btn.такого.нет")

    def test_catalog_is_not_a_noop(self):
        """Словарь правда переводит: английский и русский различаются."""
        keys = list(i18n.CATALOG)
        different = [k for k in keys if i18n.CATALOG[k]["en"] != i18n.CATALOG[k]["ru"]]
        self.assertGreater(len(different), len(keys) * 0.9,
                           f"переведено всего {len(different)} из {len(keys)}")

    def test_language_actually_switches(self):
        keep = i18n.lang()
        try:
            i18n.set_lang("ru")
            ru = i18n.t("btn.live")
            i18n.set_lang("en")
            self.assertNotEqual(ru, i18n.t("btn.live"))
        finally:
            i18n.set_lang(keep)


class TestCatalog(unittest.TestCase):

    def test_no_duplicate_keys(self):
        self.assertEqual(i18n._reset_duplicates(), [], "ключ объявлен дважды")

    def test_both_languages_are_filled(self):
        empty = [k for k, v in i18n.CATALOG.items()
                 if not (v.get("en") or "").strip() or not (v.get("ru") or "").strip()]
        self.assertEqual(empty, [], f"пустые строки: {empty[:5]}")

    def test_placeholder_fields_match(self):
        """Поля {имя} обязаны совпадать: иначе подстановка сломается в одном языке."""
        bad = []
        for key, rec in i18n.CATALOG.items():
            f_en = sorted(re.findall(r"\{(\w+)\}", rec["en"]))
            f_ru = sorted(re.findall(r"\{(\w+)\}", rec["ru"]))
            if f_en != f_ru:
                bad.append((key, f_en, f_ru))
        self.assertEqual(bad, [], f"разные поля: {bad[:3]}")

    def test_every_key_used_in_code_exists(self):
        """Каждый ключ, который проект зовёт, есть в словаре.

        Это и есть лекарство от «хауса с языком»: забытый ключ тут же виден
        (на кнопке вместо подписи оказался бы сам ключ).
        """
        used: dict[str, str] = {}
        pattern = re.compile(r"""(?:i18n\.)?\bt\(\s*"([a-zA-Z][\w.]*)"\s*[,)]""")
        for path in sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                for key in pattern.findall(line):
                    if key in ("en", "ru"):          # это языки, а не ключи словаря
                        continue
                    used.setdefault(key, f"{path.name}:{i}")
        missing = {k: v for k, v in used.items() if not i18n.has(k)}
        self.assertEqual(missing, {}, f"нет в словаре: {missing}")
        self.assertGreater(len(used), 60, "скан нашёл слишком мало ключей — он сломан")
        built_missing = [k for k in BUILT_KEYS if not i18n.has(k)]
        self.assertEqual(built_missing, [], f"нет в словаре (сборные): {built_missing}")

    def test_technical_words_are_not_translated(self):
        """Команды и статусы — идентификаторы: и в словаре их нет, и ASCII они.

        Раньше здесь была таблица подмены (failed -> error, refused -> cancelled),
        и она же была источником разнобоя: Qt показывал слово из файла, а tkinter —
        подпись, то есть две панели говорили о миссии РАЗНОЕ (находка QA Ореста
        25.09.2026). Теперь подмены нет вовсе: статус везде ровно тот, что в очереди.
        """
        for kind in MISSION_KIND_IDS:
            self.assertTrue(kind.isascii(), kind)
            self.assertFalse(i18n.has(f"status.{kind}"))
        for status in missions.STATUSES:
            self.assertTrue(status.isascii(), status)
            self.assertFalse(i18n.has(status), f"статус {status} попал в словарь переводов")
            self.assertFalse(i18n.has(f"status.{status}"))
        self.assertTrue(all(c.isascii() or c.isdigit() or c == "_" for c in "net_audit"))

    def test_roles_and_zones_are_translated_on_display_only(self):
        """Русские слова зон лежат в КАРТЕ (файл не переписываем) — переводим на показ."""
        i18n.set_lang("ru")
        self.assertEqual(i18n.zone_name("голова"), "голова")
        i18n.set_lang("en")
        self.assertEqual(i18n.zone_name("голова"), "head")
        self.assertEqual(i18n.role_name("motor"), "motor")
        i18n.set_lang("ru")
        self.assertEqual(i18n.role_name("motor"), "мотонейрон")
        i18n.set_lang("en")


class TestLanguageSwitch(unittest.TestCase):

    def test_unknown_language_is_refused(self):
        keep = i18n.set_lang("en")
        self.assertEqual(i18n.set_lang("klingon"), "en")
        self.assertEqual(i18n.set_lang(""), "en")
        i18n.set_lang(keep)

    def test_config_tells_the_language(self):
        self.assertEqual(i18n.sync_from_config({"ui": {"lang": "ru"}}), "ru")
        self.assertEqual(i18n.sync_from_config({"ui": {"lang": "en"}}), "en")
        self.assertEqual(i18n.sync_from_config({"ui": {}}), "en")
        self.assertEqual(i18n.sync_from_config({}), "en")

    def test_t_in_does_not_disturb_current_language(self):
        """Отчёт можно нарисовать на любом языке, не переключая всё окно."""
        i18n.set_lang("ru")
        self.assertEqual(i18n.t_in("en", "btn.live"), "Live")
        self.assertEqual(i18n.lang(), "ru")
        self.assertEqual(i18n.t("btn.live"), "Жить")


class TestTheme(unittest.TestCase):

    def test_qss_is_fully_substituted(self):
        for name in theme.available():
            theme.set_theme(name)
            css = theme.qss()
            self.assertNotIn("$", css, f"{name}: в QSS остался незамещённый токен")
            self.assertIn("QPushButton", css)
            self.assertGreater(len(css), 500)
        theme.set_theme("dark")

    def test_themes_really_differ(self):
        theme.set_theme("dark")
        dark = (theme.qss(), theme.bg(), theme.edge_dim(), theme.hot())
        theme.set_theme("light")
        light = (theme.qss(), theme.bg(), theme.edge_dim(), theme.hot())
        for i, pair in enumerate(zip(dark, light)):
            self.assertNotEqual(pair[0], pair[1], f"темы совпали в поле #{i}")
        theme.set_theme("dark")

    def test_unknown_theme_is_refused(self):
        theme.set_theme("dark")
        self.assertEqual(theme.set_theme("neon"), "dark")

    def test_light_theme_dims_body_colours(self):
        """Палитра медиаторов приходит из файла тела и настроена на тёмный фон."""
        theme.set_theme("dark")
        self.assertEqual(theme.adapt("#4ea1ff"), "#4ea1ff", "на тёмной теме цвет не трогаем")
        theme.set_theme("light")
        dimmed = theme.adapt("#4ea1ff")
        self.assertNotEqual(dimmed, "#4ea1ff")
        self.assertLess(int(dimmed[1:3], 16), 0x4e, "светлая тема обязана приглушить")
        theme.set_theme("dark")


class TestReportsLanguage(unittest.TestCase):

    def test_same_report_reads_in_both_languages(self):
        ru = reports.render(FAKE_AUDIT, "ru")
        en = reports.render(FAKE_AUDIT, "en")
        self.assertNotEqual(ru, en)
        self.assertRegex(ru, CYRILLIC)
        self.assertNotRegex(en, CYRILLIC, "в английском отчёте осталась кириллица")
        for text in (ru, en):
            self.assertIn("192.168.1.0/24", text)
            self.assertIn(".88", text)
            self.assertIn("445", text)
            self.assertIn("Install, Juli", text)

    def test_title_and_summary_follow_the_language(self):
        ru = reports.title(FAKE_AUDIT, "x", "ru")
        en = reports.title(FAKE_AUDIT, "x", "en")
        self.assertIn("живых 2", ru)
        self.assertIn("live 2", en)
        self.assertNotEqual(reports.summary_line(FAKE_AUDIT, "ru"),
                            reports.summary_line(FAKE_AUDIT, "en"))

    def test_all_kinds_render_in_english_without_cyrillic(self):
        """Каждый инструмент говорит по-английски: забытая строка тут же видна."""
        reps = [
            {"kind": "net_audit", **FAKE_AUDIT},
            {"kind": "certs", "at": "2026-09-24T16:26:52", "targets": [
                {"host": "192.168.1.88", "port": 443, "ok": True, "days_left": 6658,
                 "not_after": "16.12.2044", "subject": "synology.com", "issuer": "Synology Inc. CA",
                 "names": ["nas"]}], "expiring": []},
            {"kind": "write_probe", "at": "2026-09-24T16:26:52", "shares": [
                {"path": "\\\\192.168.1.88\\Install", "state": "writable"},
                {"path": "\\\\192.168.1.88\\Stas", "state": "denied", "detail": "access denied"}]},
            {"kind": "diff", "at": "2026-09-24T16:26:52", "older_cidr": "192.168.1.0/24",
             "newer_cidr": "192.168.1.0/24", "older_at": "2026-09-23T10:00:00",
             "newer_at": "2026-09-24T16:26:52", "older_alive": 6, "newer_alive": 7,
             "new_hosts": [{"ip": "192.168.1.99", "open": [80]}], "gone_hosts": [], "changed": []},
        ]
        for rep in reps:
            en = reports.render(rep, "en")
            self.assertNotRegex(en, CYRILLIC, f"{rep['kind']}: кириллица в английском отчёте")
            ru = reports.render(rep, "ru")
            self.assertRegex(ru, CYRILLIC, f"{rep['kind']}: русский отчёт без кириллицы")

    def test_read_text_rerenders_from_json_in_the_asked_language(self):
        """Смена языка не оставляет старые отчёты на прежнем языке."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "r.json"
            p.write_text(json.dumps(FAKE_AUDIT, ensure_ascii=False), encoding="utf-8")
            i18n.set_lang("ru")
            self.assertRegex(reports.read_text(p, lang="en"), re.compile("NETWORK AUDIT"))
            self.assertRegex(reports.read_text(p, lang="ru"), re.compile("АУДИД|АУДИТ СЕТИ"))
        i18n.set_lang("en")


class TestBodyColoursFollowTheme(unittest.TestCase):
    """Поломка, найденная ЖИВЫМ прогоном 25.09.2026, а не тестом.

    Таблица цветов горячих связей собиралась ОДИН раз при рождении окна: после
    смены темы вспышки оставались в цветах прежней темы (на светлой палитре это
    выглядело как оливковые и бирюзовые пятна). Теперь таблица пересобирается.
    """

    def test_heat_table_is_rebuilt_for_the_new_theme(self):
        try:
            from synapse.qt_panel import BodyView        # Qt нужен только на импорт
        except Exception as e:                           # noqa: BLE001
            self.skipTest(f"PySide6 недоступен: {type(e).__name__}")
        import types
        core = PanelCore.__new__(PanelCore)              # ядро без организма
        stub = types.SimpleNamespace(core=core, _heat=[])
        theme.set_theme("dark")
        BodyView._build_heat(stub)
        dark = list(stub._heat)
        theme.set_theme("light")
        BodyView._build_heat(stub)
        light = list(stub._heat)
        theme.set_theme("dark")
        self.assertNotEqual(dark, light, "цвета вспышек не поспели за темой")
        self.assertEqual(len(dark), 33, "ступеней должно быть 33, как в кадре")


class TestSettings(unittest.TestCase):
    """Настройки: то, что оператор крутит во вкладке, а не правит руками в файле."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "config.json"
        self._keep = config.CONFIG_PATH
        config.CONFIG_PATH = self._path          # тест НЕ трогает живой config.json
        config.forget_cache()
        # apply_settings пишет событие в журнал — уводим и его: живые data/ не трогаем
        self._live = temp_live()
        self._live.__enter__()

    def tearDown(self):
        self._live.__exit__(None, None, None)
        config.CONFIG_PATH = self._keep
        config.forget_cache()
        i18n.set_lang("en")
        theme.set_theme("dark")
        self._tmp.cleanup()

    def core(self) -> PanelCore:
        """Ядро без организма: проверяем настройки, а не тело и мозг."""
        core = PanelCore.__new__(PanelCore)
        core.cfg = config.load()
        return core

    def test_saved_to_file_and_applied_at_once(self):
        core = self.core()
        res = core.apply_settings({"lang": "ru", "theme": "light", "tick_seconds": "45",
                                   "fps_ms": "33", "idle_thinking": False,
                                   "allow_write_probe": False,
                                   "port_from": "22", "port_to": "8080"})
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["changed"], "не сказано, что изменилось")
        self.assertEqual(i18n.lang(), "ru", "язык не применился на ходу")
        self.assertEqual(theme.theme(), "light", "тема не применилась на ходу")
        disk = json.loads(self._path.read_text(encoding="utf-8"))
        self.assertEqual(disk["ui"]["lang"], "ru")
        self.assertEqual(disk["ui"]["theme"], "light")
        self.assertEqual(disk["tick_seconds"], 45)
        self.assertEqual((disk["net"]["port_from"], disk["net"]["port_to"]), (22, 8080))
        self.assertFalse(disk["net"]["allow_write_probe"])

    def test_bad_number_is_reported_and_old_value_kept(self):
        core = self.core()
        res = core.apply_settings({"tick_seconds": "много", "fps_ms": "16"})
        self.assertFalse(res["ok"])
        self.assertTrue(res["errors"], "ошибка не сказана оператору")
        disk = json.loads(self._path.read_text(encoding="utf-8"))
        self.assertEqual(disk["tick_seconds"], 60, "прежнее значение обязано остаться")

    def test_unknown_language_and_theme_are_refused(self):
        core = self.core()
        res = core.apply_settings({"lang": "klingon", "theme": "neon"})
        self.assertEqual(res["settings"]["lang"], "en")
        self.assertEqual(res["settings"]["theme"], "dark")
        self.assertEqual(len(res["errors"]), 2)

    def test_port_range_is_kept(self):
        """Диапазон портов, а не список (v0.22.0): два числа и всё."""
        core = self.core()
        self.assertEqual(core.parse_port_range("22", "8080"), (22, 8080))

    def test_settings_path_is_shown_not_hidden(self):
        self.assertTrue(self.core().settings_path().endswith("config.json"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
