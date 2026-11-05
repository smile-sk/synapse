"""Тесты гибридного тела (LiveBody): запечённая картинка + мелкие предметы Tk.

Запуск из корня проекта:  python tests/test_live_body.py

ПЕРВЫМ идёт КОНТРОЛЬ — меряем ОБА пути на одном кадре одного размера и убеждаемся,
что гибрид действительно дешевле целой картинки. Без этого «стало быстрее» ничего
не значит: можно было просто уменьшить картинку и радоваться.
"""
import statistics
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synapse import config                                   # noqa: E402
from synapse.agent import Synapse                            # noqa: E402
from synapse.panel_core import PanelCore                     # noqa: E402
from synapse.render import ImageRenderer, LiveBody, picture_size   # noqa: E402


def quiet_synapse():
    """Организм для тестов БЕЗ живого мозга (тест не имеет права поднимать llama-server)."""
    cfg = config.load()
    cfg["brain"] = dict(cfg.get("brain", {}), mode="off")
    return Synapse(cfg=cfg)


class TestPictureSize(unittest.TestCase):
    """Бюджет площади: цена кадра растёт с пикселями, поэтому окно картинку не растягивает."""

    def test_small_window_not_stretched(self):
        self.assertEqual(picture_size(700, 500), (700, 500))

    def test_big_window_capped(self):
        w, h = picture_size(1920, 1080)
        self.assertLessEqual(w * h, 620_000)
        self.assertLessEqual(abs(w / h - 1920 / 1080), 0.02)      # пропорции тела целы
        self.assertGreater(w, 600)

    def test_absurd_size_has_floor(self):
        w, h = picture_size(10, 10)                              # окно не бывает нулевым
        self.assertGreaterEqual(min(w, h), 200)


class TestLiveBody(unittest.TestCase):
    """Гибрид в настоящем (скрытом) окне: проверяем и сборку, и порядок слоёв."""

    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        cls.tk = tk
        cls.root = tk.Tk()
        cls.root.geometry("1920x1080+0+0")
        cls.root.withdraw()                     # окно есть, но на экране его нет
        cls.canvas = tk.Canvas(cls.root, width=1920, height=1080, bg="#0a0e13",
                               highlightthickness=0)
        cls.canvas.pack(fill="both", expand=True)
        cls.syn = quiet_synapse()
        cls.core = PanelCore(cls.syn)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.body = LiveBody(self.core)
        self.body.attach(self.canvas)
        self.body.resize(1920, 1080)
        self.body.offset = (200, 150)
        self.body.rebuild()

    def tearDown(self):
        self.canvas.delete("all")
        self.body.forget_items()

    # ---- контроль: оба пути на одном кадре ----
    def test_control_hybrid_cheaper_than_whole_picture(self):
        """КОНТРОЛЬ. Гибрид обязан быть дешевле, чем перерисовка всей картинки.

        Мерим одну и ту же сцену: кадр гибрида (сдвинуть живое) против кадра
        «нарисовать всё заново и отдать в Tk». Если гибрид не дешевле — вся
        затея с разделением слоёв не имеет смысла.
        """
        ir = ImageRenderer(self.core)
        ir.resize(1920, 1080)
        pts = self.core.screen(ir.w, ir.h)
        ir._build(pts)

        def frame_image():
            img = ir.render_image(pts)
            photo = getattr(ir, "_photo", None)
            if photo is None:
                from PIL import ImageTk
                ir._photo = ImageTk.PhotoImage(img)
            else:
                ir._photo.paste(img)
            self.root.update()

        def frame_hybrid():
            self.body.step()
            self.root.update()

        for f in (frame_image, frame_hybrid):            # прогрев
            f(); f()

        t_img = []
        for _ in range(12):
            t0 = time.perf_counter(); frame_image(); t_img.append((time.perf_counter() - t0) * 1000)
        t_hyb = []
        for _ in range(12):
            t0 = time.perf_counter(); frame_hybrid(); t_hyb.append((time.perf_counter() - t0) * 1000)
        m_img, m_hyb = statistics.median(t_img), statistics.median(t_hyb)
        print(f"   контроль: картинка целиком {m_img:.1f} мс · гибрид {m_hyb:.1f} мс")
        self.assertLess(m_hyb, m_img, "гибрид не дешевле целой картинки")

    # ---- сборка ----
    def test_picture_is_one_item_and_static(self):
        """Неподвижное лежит ОДНОЙ картинкой: 20 кадров без поворота — одна сборка."""
        before = self.body.rebuilds
        for _ in range(20):
            self.body.step()
        self.assertEqual(self.body.rebuilds, before, "неподвижное пересобиралось без причины")
        self.assertEqual(self.body.picture_box(), (self.body.w, self.body.h))

    def test_rotating_marks_static_dirty(self):
        """Так панель помечает поворот/зум/раскраску: следующая сборка — ровно одна."""
        before = self.body.rebuilds
        self.body._reset()
        self.body.step()
        self.assertEqual(self.body.rebuilds, before + 1)

    def test_glow_is_above_picture(self):
        """Свечение обязано лежать ПОВЕРХ картинки — иначе возбуждённое тело тусклее спокойного."""
        order = list(self.canvas.find_all())
        self.assertIn(self.body._image_item, order)
        pic = order.index(self.body._image_item)
        self.assertGreater(order.index(self.body._glow_items[0]), pic)
        # горячих связей в спокойном теле может не быть вовсе — тогда проверять нечего
        # (пул досоздаётся в кадре, это отдельный тест), но которая есть — обязана быть выше
        for it in self.body._hot_items:
            self.assertGreater(order.index(it), pic)

    def test_glow_follows_lit_node(self):
        """Зажгли узел — спрайт стоит там, где узел (в координатах картинки плюс сдвиг)."""
        self.core.body.activity[:] = [0.0] * self.core.body.size
        i = self.core.body.ids.index("AVAL") if "AVAL" in self.core.body.ids else 0
        self.core.body.activity[i] = 0.9
        self.body.step()
        x, y = self.canvas.coords(self.body._glow_items[0])
        px, py, _d = self.core.screen(self.body.w, self.body.h)[i]
        self.assertAlmostEqual(x, px + self.body.offset[0], delta=1.5)
        self.assertAlmostEqual(y, py + self.body.offset[1], delta=1.5)
        self.assertEqual(self.body.last_lit, 1)

    def test_sprite_cache_reused(self):
        """Тот же свет — тот же спрайт: кэш не пухнет на каждом кадре."""
        self.core.body.activity[:] = [0.0] * self.core.body.size
        i = self.core.body.ids.index("AVAL") if "AVAL" in self.core.body.ids else 0
        self.core.body.activity[i] = 0.9
        self.body.step()
        n1 = len(self.body._sprites)
        for _ in range(5):
            self.body.step()
        self.assertEqual(len(self.body._sprites), n1)
        self.assertGreaterEqual(n1, 1)

    def test_calm_body_hides_glow(self):
        """Тело погасло — спрайты скрыты, а не висят светлячками."""
        self.core.body.activity[:] = [0.0] * self.core.body.size
        self.body.step()
        shown = [i for i, st in enumerate(self.body._glow_state)
                 if st and st[1]]
        self.assertEqual(shown, [])
        self.assertEqual(self.body.last_lit, 0)

    def test_lit_activity_never_leaks_into_static_photo(self):
        """Неподвижная картинка не знает про возбуждение — иначе её пришлось бы пересобирать каждый кадр."""
        self.core.body.activity[:] = [0.0] * self.core.body.size
        before = self.body._photo
        self.core.body.activity[0] = 1.0
        self.body.step()
        self.assertIs(self.body._photo, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestLiveliness(unittest.TestCase):
    """v0.32.0: живость ЗА ПРОМЕЖУТОК, а не снимок мгновения.

    Найдено живым журналом 30.09.2026 (22:11-22:19): тело дышит — окно гонит вдох
    каждые 90 кадров, рефлекс бьёт импульсом каждые 20-30 секунд, — но energy()
    считает среднее МГНОВЕННОЙ активности, а импульс гаснет за секунду. Такт раз в
    30 секунд попадал ровно в паузу, и в промпт мысли уходило «зажжено 0/302 нейрона,
    энергия 0.0000; громче всех тихо». Модель читала это как «я умираю»: шестнадцать
    мыслей подряд про «энергия на исходе, ползу искать еду».
    """

    def test_control_a_quiet_body_reports_nothing(self):
        """КОНТРОЛЬ: молчащее тело честно говорит «ноль» — иначе счётчик был бы слепым."""
        b = quiet_synapse().body
        b.soothe(0.0)
        live = b.liveliness(reset=True)
        self.assertEqual(live["impulses"], 0)
        self.assertEqual(live["peak"], 0.0)

    def test_impulses_and_peak_are_counted(self):
        b = quiet_synapse().body
        b.liveliness(reset=True)
        b.stimulus("проба живости", amp=1.0, k=5)
        for _ in range(3):
            b.step()
        time.sleep(0.12)                    # отрезок меряется секундами: даём им пройти
        live = b.liveliness()
        self.assertGreaterEqual(live["impulses"], 5, "импульс не посчитан")
        self.assertGreater(live["peak"], 0.0, "пик не записан")
        self.assertGreater(live["seconds"], 0.0, "отрезок не измерен")

    def test_reset_starts_a_new_window(self):
        b = quiet_synapse().body
        b.stimulus("проба живости", amp=1.0, k=5)
        b.step()
        first = b.liveliness(reset=True)
        self.assertGreater(first["impulses"], 0)
        self.assertEqual(b.liveliness()["impulses"], 0, "окно не сбросилось")

    def test_the_living_body_is_not_a_dead_snapshot(self):
        """Смысл правки: в тот же миг, когда energia 0, за отрезок тело жило."""
        b = quiet_synapse().body
        b.liveliness(reset=True)
        b.stimulus("вдох", amp=0.55, k=2)
        for _ in range(60):                     # секунда жизни: импульс уже погас
            b.step()
        self.assertEqual(b.lit(), 0, "контроль: снимок мгновения пуст")
        self.assertGreater(b.liveliness()["impulses"], 0,
                           "за отрезок тело молчало — вот это и уходило в промпт")
