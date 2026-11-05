"""Тесты v0.2: точная карта ролей + ядро панели (окно проверяем отдельно, selftest).

    python tests/test_roles_panel.py        (запускать из корня проекта)

Здесь нет ни одного «is not None»: каждая проверка утверждает то, что код ДЕЛАЕТ.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import config, roles as roles_mod              # noqa: E402
from synapse.body import Body                               # noqa: E402
from synapse.brain import Brain                             # noqa: E402
from synapse.panel_core import PanelCore, View3D, heat, mix  # noqa: E402
from live_data import temp_live                              # noqa: E402


import pytest                                          # noqa: E402

from synapse import i18n                               # noqa: E402


@pytest.fixture(autouse=True)
def _russian_language():
    """Эти тесты проверяют РУССКИЙ текст — тот, который читает Стас.

    Так и надо: язык по умолчанию теперь английский (ui.lang в настройках), а
    английский путь отдельно проверяет tests/test_i18n_theme.py. Иначе проверка
    «отчёт читается» зависела бы от того, какой язык случайно стоит в конфиге.
    """
    i18n.set_lang("ru")
    yield
    i18n.set_lang("en")


def quiet_synapse():
    """Организм для тестов БЕЗ живого мозга.

    Иначе тест поднимает llama-server на 11435 и оставляет его сиротой — без владельца.
    """
    cfg = config.load()
    cfg["brain"] = dict(cfg.get("brain", {}), mode="off")
    from synapse.agent import Synapse
    return Synapse(cfg=cfg)


class TestRoleRules(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(roles_mod.resolve_role(["sensory", "motor"]), "polymodal")
        self.assertEqual(roles_mod.resolve_role(["motor"]), "motor")
        self.assertEqual(roles_mod.resolve_role(["sensory"]), "sensory")
        self.assertEqual(roles_mod.resolve_role(["interneuron", "sensory"]), "sensory")
        self.assertEqual(roles_mod.resolve_role(["interneuron", "motor"]), "motor")
        self.assertEqual(roles_mod.resolve_role(["interneuron"]), "inter")
        self.assertEqual(roles_mod.resolve_role(["unknown"]), "other")
        self.assertEqual(roles_mod.resolve_role([]), "other")
        self.assertEqual(roles_mod.resolve_role(["Interneuron", " MOTOR "]), "motor")

    def test_roles_module_does_not_invent(self):
        """Ни один тип атласа не выдуман: роль всегда из закрытого списка."""
        for t in (["foo"], ["sensory", "bar"], ["INTERNEURON"]):
            self.assertIn(roles_mod.resolve_role(t), roles_mod.ROLES)


class TestExactMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = Body.load()
        cls.map = roles_mod.load(config.ROOT / config.load()["body"]["roles_file"])

    def test_map_covers_every_neuron(self):
        self.assertEqual(len(self.map), 302)
        missing = [n for n in self.body.ids if n not in self.map]
        self.assertEqual(missing, [], "карта обязана покрывать все узлы тела")

    def test_every_entry_has_source_and_role(self):
        for name, rec in self.map.items():
            self.assertIn(rec["role"], roles_mod.ROLES, name)
            self.assertTrue(rec.get("source"), name)
            self.assertTrue(rec.get("in_atlas"), f"{name}: нет в выгрузке атласа")

    def test_no_unknown_left(self):
        """Точная карта — значит «unknown» не осталось (CAN — не синапс, это 'other')."""
        unknown = [n for n in self.body.ids if self.body.neuron(n).get("role") == "unknown"]
        self.assertEqual(unknown, [], f"остались неопознанные: {unknown[:10]}")
        self.assertEqual(self.body.neuron("CANL")["role"], "other")

    def test_body_uses_map_not_heuristic(self):
        # PMC/PVW и прочие, которых эвристика по именам не знает, теперь названы атласом
        self.assertEqual(self.body.neuron("VC6")["role"], "motor")
        self.assertEqual(self.body.neuron("IL1DL")["role"], "polymodal")
        self.assertEqual(self.body.neuron("AWAL")["role"], "sensory")
        self.assertTrue(self.body.neuron("IL1DL")["types"])

    def test_axis_head_tail(self):
        """Проверка знака оси: амфидные (голова) и хвостовые — по атласу, а не на глаз."""
        self.assertEqual(self.body.neuron("AWAL")["zone"], "голова")
        self.assertEqual(self.body.neuron("ASEL")["zone"], "голова")
        self.assertEqual(self.body.neuron("PLML")["zone"], "хвост")
        self.assertEqual(self.body.neuron("PHCL")["zone"], "хвост")

    def test_hubs_are_command_interneurons(self):
        """известная биология: командные вставочные — самые связные узлы коннектома"""
        names = [n for n, _d in self.body.hubs(10)]
        self.assertIn("AVAL", names)
        self.assertIn("AVAR", names)
        self.assertIn("AVDR", names)

    def test_polymodal_pool_joins_both(self):
        """IL1/M3/MC/NSM/URA/VB и слушают, и двигают — значит в обоих наборах."""
        sensory, motor = set(self.body.pool("sensory")), set(self.body.pool("motor"))
        il1 = self.body._index["IL1DL"]
        self.assertIn(il1, sensory)
        self.assertIn(il1, motor)
        self.assertNotIn(self.body._index["AWAL"], motor)      # чистый сенсор не «рука»

    def test_receptors_counted_from_atlas(self):
        self.assertGreater(self.body.neuron("AVAL")["receptors"], 3)


class TestView3D(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = Body.load()
        cls.view = View3D(cls.body.xyz)

    def test_all_points_on_screen(self):
        w, h = 800, 600
        pts = self.view.project(0.0, 0.15, 1.0, w, h)
        self.assertEqual(len(pts), self.body.size)
        off = [p for p in pts if not (-5 <= p[0] <= w + 5 and -5 <= p[1] <= h + 5)]
        self.assertEqual(off, [], "точки уехали за экран — масштаб посчитан неверно")

    def test_rotation_moves_points_but_keeps_them_on_screen(self):
        w, h = 800, 600
        a = self.view.project(0.0, 0.0, 1.0, w, h)
        b = self.view.project(1.2, 0.4, 1.0, w, h)
        moved = sum(1 for i in range(len(a)) if abs(a[i][0] - b[i][0]) > 1)
        self.assertGreater(moved, self.body.size // 2, "поворот не меняет вид")
        off = [p for p in b if not (-5 <= p[0] <= w + 5 and -5 <= p[1] <= h + 5)]
        self.assertEqual(off, [], "при повороте тело вылезло за экран")

    def test_zoom_changes_size(self):
        w, h = 800, 600
        near = max(p[0] for p in self.view.project(0.0, 0.0, 2.0, w, h))
        far = max(p[0] for p in self.view.project(0.0, 0.0, 0.5, w, h))
        self.assertGreater(near, far, "приближение не приближает")


class TestColors(unittest.TestCase):
    def test_mix_and_heat(self):
        self.assertEqual(mix("#000000", "#ffffff", 0.0), "#000000")
        self.assertEqual(mix("#000000", "#ffffff", 1.0), "#ffffff")
        self.assertEqual(mix("#000000", "#ffffff", 9.0), "#ffffff")      # зажим
        self.assertEqual(heat("#4ea1ff", 0.0), "#4ea1ff")                # спокойный — свой
        self.assertNotEqual(heat("#4ea1ff", 1.0), "#4ea1ff")             # разогретый — светлее
        self.assertTrue(heat("#4ea1ff", 0.5).startswith("#"))


class TestPanelCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = PanelCore()

    def test_status_shape(self):
        st = self.core.status()
        for key in ("energy", "lit", "size", "roles", "brain", "queued", "zones"):
            self.assertIn(key, st)
        self.assertEqual(st["size"], 302)

    def test_coloring_modes_give_colors(self):
        for how in ("role", "nt", "zone"):
            self.core.coloring = how
            c = self.core.node_color(0)
            self.assertRegex(c, r"^#[0-9a-f]{6}$", f"режим {how} вернул не цвет: {c}")

    def test_hot_edges_only_active_and_capped(self):
        self.assertEqual(self.core.hot_edges(), [], "спокойное тело не должно светить связями")
        self.core.body.stimulus("проверка панели", amp=1.0, k=6)
        self.core.body.step()
        hot = self.core.hot_edges(50)
        self.assertTrue(hot, "после стимула связи обязаны загореться")
        self.assertLessEqual(len(hot), 50)
        vals = [v for _k, v in hot]
        self.assertEqual(vals, sorted(vals, reverse=True), "горячие не отсортированы")

    def test_breathe_moves_body_and_dies_down(self):
        self.core.body.poke(self.core.body.pick("sensory", k=4, seed=7), 1.0)
        first = self.core.body.energy()
        self.core.breathe()
        self.assertGreater(first, 0.0)
        for _ in range(60):
            self.core.body.step()
        self.assertEqual(self.core.body.lit(), 0, "тело не гаснет — это припадок")

    def test_role_table_covers_all_302(self):
        rows = self.core.role_table()
        self.assertEqual(sum(r["count"] for r in rows), 302)
        self.assertTrue(all(r["ru"] for r in rows))

    def test_pulse_and_neuron(self):
        # Импульс теперь ПИШЕТ событие в журнал (panel.pulse), а журнал — живой:
        # значит проба обязана жить в песочнице. Найдено 25.09.2026 — новый лог
        # импульса утёк в журнал оператора именно из этой пробы.
        with temp_live():
            self.core.pulse("панель проверяет рефлекс", amp=1.0)
        self.assertIn("зажжено", self.core.body.describe())
        rec = self.core.neuron("AVAL")
        self.assertEqual(rec["name"], "AVAL")
        self.assertIn(rec["role"], ("inter",))
        self.assertEqual(self.core.neuron("нет_такого"), {})

    def test_reports_and_journal_readable(self):
        self.assertIsInstance(self.core.reports(), list)
        self.assertIsInstance(self.core.audit_tail(5), list)


class TestBrainLocal(unittest.TestCase):
    """Мозг СВОЙ: пути внутри проекта, ничего из AI_UI не запускаем без слова Стаса."""

    def test_brain_mode_is_called_not_passed(self):
        """Живой прогон 24.09: агент отдавал в JSON сам метод brain.mode, а не его
        значение — тесты были зелёные, а печать такта падала. Сторож на это."""
        import json
        from synapse.agent import Synapse
        with temp_live():                # такт живого существа — не в живые данные
            out = quiet_synapse().tick(with_mission=False, step_body=False)
        json.dumps(out, ensure_ascii=False)          # упадёт, если в словаре метод
        self.assertIsInstance(out["brain"], str)

    def test_state_paths_are_local_first(self):
        st = Brain().state()
        self.assertIn("local_dir", st)
        self.assertTrue(str(st["local_dir"]).endswith("brain"))
        self.assertTrue(Path(st["local_dir"]).is_absolute() or "models" in str(st["local_dir"]))
        for key in ("exe", "model", "self_contained", "mode", "available"):
            self.assertIn(key, st)

    def test_mode_default_server_and_think_silent_when_off(self):
        """Умолчание — mode: server (тело и мозг вместе), а в off мозг молчит.

        Было наоборот: умолчание off, а живой config.json стоял на server, то есть
        «по умолчанию» и «как у оператора» говорили разное — расхождение нашёл QA
        Орест 25.09.2026. Само поведение отказа проверяем отдельно: off — молчит."""
        self.assertEqual(config.DEFAULTS["brain"]["mode"], "server")
        b = Brain({"brain": {"mode": "off", "endpoint": "http://127.0.0.1:9/v1/chat/completions"}})
        self.assertEqual(b.mode(), "off")
        self.assertIsNone(b.think("ты тут?"), "в режиме off мозг обязан молчать, а не выдумывать")
        self.assertFalse(b.available(timeout=0.3))

    def test_set_mode_rejects_junk(self):
        with self.assertRaises(ValueError):
            Brain().set_mode("какой-то-режим")



class TestRetiredKeys(unittest.TestCase):
    def test_old_brain_keys_dropped(self):
        cfg = {"brain": {"mode": "off", "server_exe": "старый", "model_path": "старый",
                         "local_dir": "models/brain"}}
        clean, dropped = config.drop_retired(cfg)
        self.assertNotIn("server_exe", clean["brain"])
        self.assertNotIn("model_path", clean["brain"])
        self.assertIn("brain.server_exe", dropped)      # в списке выведенных — то, что убрали
        self.assertNotIn("brain.local_dir", dropped)
        self.assertEqual(clean["brain"]["local_dir"], "models/brain")

    def test_load_never_returns_retired(self):
        cfg = config.load()
        self.assertNotIn("server_exe", cfg["brain"])
        self.assertNotIn("model_path", cfg["brain"])


class TestPanelModule(unittest.TestCase):
    def test_panel_imports_without_opening_window(self):
        """Импорт окна не должен требовать дисплея и не должен ничего запускать."""
        import synapse.panel as panel
        self.assertTrue(hasattr(panel, "Panel"))
        self.assertTrue(hasattr(panel, "main"))

    def test_panel_source_has_no_obvious_typos(self):
        src = (ROOT / "synapse" / "panel.py").read_text(encoding="utf-8")
        for needle in ("def animate", "def _relayout", "def toggle_brain", "def refresh_missions"):
            self.assertIn(needle, src)


class TestImageRenderer(unittest.TestCase):
    """Быстрый путь отрисовки: проверяем то, что видно глазом в кадре, а не факт вызова."""

    @classmethod
    def setUpClass(cls):
        try:
            from synapse.render import ImageRenderer
        except Exception as e:                       # noqa: BLE001
            raise unittest.SkipTest(f"Pillow нет: {e}")
        cls.ImageRenderer = ImageRenderer
        cls.core = PanelCore()
        cls.r = ImageRenderer(cls.core)
        cls.r.resize(400, 300)

    def _bright(self, img):
        """Сколько пикселей реально светятся (ярче фона с сеткой)."""
        return sum(1 for px in img.getdata() if px[0] + px[1] + px[2] > 260)

    def test_frame_size(self):
        img = self.r.render_image()
        self.assertEqual(img.size, (400, 300))

    def test_calm_vs_excited(self):
        body = self.core.body
        body.activity = [0.0] * body.size
        calm = self._bright(self.r.render_image())
        self.assertGreater(calm, 100, "даже спокойное тело должно быть видно (узлы)")
        body.poke(body.pick("sensory", k=8, seed=3), 1.0)
        body.step()
        excited = self._bright(self.r.render_image())
        self.assertGreater(excited, calm, "возбуждённое тело обязано светиться ярче спокойного")

    def test_static_layer_is_cached(self):
        pts = self.core.screen(400, 300)
        a = self.r._static_layer(pts)
        b = self.r._static_layer(pts)
        self.assertIs(a, b, "сети связей незачем перерисовываться, пока тело не повернулось")
        self.core.yaw += 0.3
        c = self.r._static_layer(self.core.screen(400, 300))
        self.assertIsNot(c, a, "после поворота слой обязан пересобраться")

    def test_renderer_uses_core_budget(self):
        self.assertLessEqual(len(self.core.edge_keep), self.core.edge_budget)
        self.assertTrue(self.core.edge_keep, "нельзя показать тело без связей")


if __name__ == "__main__":
    unittest.main(verbosity=2)
