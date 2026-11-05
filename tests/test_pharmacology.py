"""v0.11: физиология передачи — знак синапса, а не только цвет узла.

    python tests/test_pharmacology.py          (запускать из корня проекта)

Слово Стаса 25.09.2026: «конечно делай все по настоящему, мы же не для декораций
делаем». До этой правки нейромедиатор влиял ТОЛЬКО на краску: волна шла по весам
связей, и тормозной нейрон вёл себя как возбуждающий. Теперь знак связи берётся из
данных — из медиатора самого нейрона (synapse/pharmacology.py), и это ЗАМЕРЕНО:

  * торможение (26 GABA-узлов: DD/VD, RME, AVL/DVB, RIS). Бьём импульсом только в
    них: суммарная активность их целей падает 11.64 -> 1.99 (замер ниже);
  * порог срыва (максимальный k, при котором волна ещё гаснет): без знака 14,
    с одним торможением 16, торможение + тонус 14;
  * моноамины (17 узлов) не проводят быстрый ток вовсе: их 325 связей уходят в
    путь ТОНУСА, а тонус меняет ВОЗБУДИМОСТЬ, а не подаёт ток. Первая версия
    добавляла ток — тело залипало (серотониновые NSM держали петлю вечно: k=3 не
    гас НИКОГДА). Это тоже замер, и он записан в body.step;
  * направление тонуса видно на k=6: базовый контур гаснет за 44 такта, после
    серотонина за 46 (бодрость держит), после дофамина за 41 (замедление гасит
    раньше).

КОНТРОЛЬ ИДЁТ ПЕРВЫМ: с nt.enabled=false тело обязано воспроизвести ПРЕЖНИЙ закон
до единицы. Без этого «физиология работает» ничего не доказывает — вдруг я просто
переписала старый результат.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from synapse import config, i18n, pharmacology as pharm, roles as roles_mod   # noqa: E402
from synapse.body import Body                                                 # noqa: E402
from synapse.panel_core import PanelCore                                      # noqa: E402
from live_data import nt_mode, temp_live                                      # noqa: E402


def wave(body: Body, k: int, steps: int = 800, seed: int = 7, pre: set | None = None):
    """Пик зажжённых и такт, когда тело погасло (None — не гаснет)."""
    with body.lock:
        body.activity = [0.0] * body.size
        body.tone = [0.0] * body.size
    if pre:
        body.poke([i for i, n in enumerate(body.ids) if n in pre], 1.0)
        for _ in range(2):          # модулятор успел выбросить тонус
            body.step()
    body.poke(body.pick("sensory", k=k, seed=seed), 1.0)
    peak, gone = 0, None
    for t in range(steps):
        body.step()
        lit = body.lit()
        peak = max(peak, lit)
        if lit == 0 and gone is None and t > 2:
            gone = t + 1
    return peak, gone


class TestControlTheOldLawStillHolds(unittest.TestCase):
    """КОНТРОЛЬ: физиология выключена — тело ведёт себя ровно как до неё."""

    def test_control_off_reproduces_the_previous_law_exactly(self):
        with nt_mode(enabled=False):
            b = Body.load()
            b.stimulus("alpha", amp=1.0, k=3)
            peak, gone = 0, None
            for i in range(600):
                b.step(decay=0.70, gain=0.30, threshold=0.02, sustain=1.2)
                lit = b.lit(0.02)
                peak = max(peak, lit)
                if lit == 0:
                    gone = i + 1
                    break
        self.assertEqual((peak, gone), (16, 29),
                         "выключенная физиология не воспроизвела прежний закон")

    def test_control_on_differs_from_off(self):
        """Если включённая физиология даёт ровно то же — значит она не работает."""
        with nt_mode(enabled=False):
            off = wave(Body.load(), 8)
        with nt_mode(enabled=True):
            on = wave(Body.load(), 8)
        self.assertNotEqual(off, on, "знак медиатора ни на что не влияет")
        self.assertLessEqual(on[0], off[0], "торможение обязано не увеличивать пик")


class TestDataTheSignComesFromTheAtlas(unittest.TestCase):
    """Знак — из данных, а не из моей головы: сверяем с атласом и с телом."""

    def test_twenty_six_gaba_neurons_are_the_real_inhibitory_ones(self):
        atlas = roles_mod.load(ROOT / "data/body/neuron_roles.json")
        expect = {n for n, r in atlas.items() if r.get("nt") == "GABA"}
        b = Body.load()
        got = {n for i, n in enumerate(b.ids) if b.sign[i] < 0}
        self.assertEqual(len(expect), 26, "GABA-узлов в атласе должно быть 26")
        self.assertEqual(got, expect, "тормозными помечены не те нейроны")
        # Это не «какие-то» нейроны, а конкретная анатомия: тормозные мотонейроны
        # мускулатуры тела (DD/VD), мышц головы (RME), кишки (AVL/DVB) и нейрон сна.
        self.assertTrue({"DD1", "VD13", "RMED", "AVL", "DVB", "RIS"} <= got)

    def test_every_transmitter_has_a_declared_kind(self):
        b = Body.load()
        seen = sorted({k for k in b.kind})
        self.assertEqual(seen, [pharm.EXC, pharm.INH, pharm.MOD], "типов передачи три")
        self.assertEqual(b.physiology(), {pharm.EXC: 259, pharm.INH: 26, pharm.MOD: 17})
        self.assertEqual(sum(b.physiology().values()), b.size, "покрытие обязано быть полным")

    def test_unknown_transmitter_is_not_invented(self):
        """59 узлов без медиатора — нейтральный знак, а не выдуманное торможение."""
        b = Body.load()
        blank = [i for i, n in enumerate(b.nt) if not n]
        self.assertEqual(len(blank), 59)
        for i in blank:
            self.assertEqual(b.sign[i], 1.0)
            self.assertEqual(b.kind[i], pharm.EXC)
            self.assertFalse(pharm.known(b.nt[i]), "пустой медиатор не «известен»")

    def test_glutamate_sign_is_a_setting_not_a_guess(self):
        """В графе нейрон-нейрон глутамат возбуждает; у НМС тормозит, но мышц нет."""
        self.assertEqual(pharm.sign("Glutamate"), 1.0)
        self.assertEqual(pharm.sign("Glutamate", glutamate_inhibitory=True), -1.0)
        self.assertEqual(pharm.kind("Glutamate", glutamate_inhibitory=True), pharm.INH)


class TestInhibitionWorks(unittest.TestCase):
    """Торможение — это ВЫЧИТАНИЕ из возбуждения цели, а не «слабое возбуждение»."""

    GABA = {"DD%d" % i for i in range(1, 7)} | {"VD%d" % i for i in range(1, 14)} | \
           {"RMED", "RMEL", "RMER", "RMEV", "AVL", "DVB", "RIS"}

    def _target_activity(self, enabled: bool) -> float:
        with nt_mode(enabled=enabled):
            b = Body.load()
            idx = [i for i, n in enumerate(b.ids) if n in self.GABA]
            self.assertEqual(len(idx), 26, "GABA-узлов в теле должно быть 26")
            targets = sorted({j for i in idx for j, _ in b._out[i]})
            with b.lock:
                b.activity = [0.0] * b.size
                b.tone = [0.0] * b.size
            b.poke(idx, 1.0)
            for _ in range(4):
                b.step()
            return sum(b.activity[j] for j in targets)

    def test_gaba_silences_its_targets(self):
        off = self._target_activity(enabled=False)      # те же связи, но БЕЗ знака
        self.assertGreater(off, 5.0, "контроль: без торможения цели обязаны возбудиться")
        on = self._target_activity(enabled=True)
        self.assertLess(on, off * 0.5,
                        f"торможение не работает: цели {on:.2f} против контроля {off:.2f}")

    def test_inhibition_raises_the_seizure_threshold(self):
        """Настоящая GABA держит тело: k=16 с торможением гаснет, без него — срыв."""
        with nt_mode(enabled=False):
            self.assertIsNone(wave(Body.load(), 16)[1], "контроль: без знака k=16 обязан срываться")
        with nt_mode(enabled=True, modulation=False):
            self.assertIsNotNone(wave(Body.load(), 16)[1], "с торможением GABA k=16 обязано гаснуть")


class TestModulationWorks(unittest.TestCase):
    """Моноамины: объёмная передача — меняют возбудимость, а не подают ток."""

    def test_modulators_do_not_carry_fast_current(self):
        b = Body.load()
        mods = [i for i, k in enumerate(b.kind) if k == pharm.MOD]
        self.assertEqual(len(mods), 17)
        self.assertEqual(sum(len(b._mod_out[i]) for i in mods), 325, "связей модуляторов 325")
        for i in mods:
            self.assertEqual(b.sign[i], 0.0)
            self.assertEqual(b._out[i], [], "быстрый путь модулятора обязан быть пуст")
            self.assertTrue(b._mod_out[i], "у модулятора обязаны быть цели тонуса")

    def test_tone_alone_cannot_keep_the_body_alive(self):
        """Структурная гарантия: тонус множит СОХРАНЕНИЕ, а оно остаётся < 1."""
        with nt_mode(enabled=True):
            b = Body.load()
            with b.lock:
                b.activity = [0.0] * b.size
                b.tone = [0.4] * b.size        # насильно залили тонус всем узлам
            for _ in range(30):
                b.step()
            self.assertEqual(b.lit(), 0, "тонус держит тело живым — контур залипает")
        cfg = config.load()["body"]["nt"]
        self.assertLess(cfg["tone_decay"], 1.0, "тонус обязан слабеть, иначе он держит контур")
        self.assertLessEqual(cfg["tone_cap"], 0.5, "потолок тонуса подобран замером, а не на глаз")

    def test_serotonin_lingers_and_dopamine_brakes(self):
        """Направление тонуса видно замером: бодрость держит контур, замедление гасит."""
        sero = {"ADFL", "ADFR", "HSNL", "HSNR", "NSML", "NSMR", "RIH"}
        dopa = {"ADEL", "ADER", "CEPDL", "CEPDR", "CEPVL", "CEPVR", "PDEL", "PDER"}
        with nt_mode(enabled=True, modulation=True):
            b = Body.load()
            base = wave(b, 6)[1]
            s = wave(b, 6, pre=sero)[1]
            d = wave(b, 6, pre=dopa)[1]
        self.assertIsNotNone(base)
        self.assertGreater(s, base, f"серотонин не продлил контур: {s} против {base}")
        self.assertLess(d, base, f"дофамин не погасил раньше: {d} против {base}")

    def test_shape_of_the_wave_is_still_civilized(self):
        """Знак не должен сломать прежний закон: обычный стимул расходится и гаснет."""
        with nt_mode(enabled=True):
            peak, gone = wave(Body.load(), 3)
        self.assertIsNotNone(gone, "тело перестало гаснуть — физиология ушла в срыв")
        self.assertGreater(peak, 5)


class TestPhysiologyIsVisible(unittest.TestCase):
    """Работать мало: оператор обязан видеть, «что это за названия и на что влияют»."""

    def setUp(self):
        self._live = temp_live()
        self._live.__enter__()

    def tearDown(self):
        self._live.__exit__(None, None, None)

    def test_legend_says_what_the_transmitter_does(self):
        core = PanelCore()
        core.coloring = "nt"
        rows = core.legend()
        labels = {r["label"] for r in rows}
        self.assertEqual(len(rows), 7, "шесть медиаторов + «без медиатора»")
        self.assertEqual(sum(r["count"] for r in rows), core.body.size, "легенда покрывает всё тело")
        gaba = [l for l in labels if l.startswith("GABA")]
        self.assertTrue(gaba, "GABA потерялся в легенде")
        self.assertIn(i18n.t("nt.kind.inh"), gaba[0], "у GABA не написано, что он тормозит")
        self.assertTrue(any("Acetylcholine" in l for l in labels))

    def test_physiology_line_counts_match_the_body(self):
        core = PanelCore()
        line = core.physiology_line()
        p = core.body.physiology()
        for value in (p[pharm.EXC], p[pharm.INH], p[pharm.MOD]):
            self.assertIn(str(value), line, f"в строке физиологии нет числа {value}")
        self.assertNotEqual(line, "legend.physiology", "вернулся сам ключ — нет текста")

    def test_physiology_line_is_honest_when_it_is_off(self):
        with nt_mode(enabled=False):
            core = PanelCore()
            self.assertEqual(core.physiology_line(), i18n.t("legend.physiology_off"))

    def test_neuron_card_names_the_transmission(self):
        core = PanelCore()
        gaba = core.neuron("DD1")
        self.assertEqual(gaba.get("kind"), pharm.INH)
        self.assertEqual(gaba.get("kind_label"), i18n.t("nt.kind.inh"))
        ac = core.neuron("VB1")
        self.assertIn(ac.get("kind"), (pharm.EXC, pharm.INH, pharm.MOD))
        self.assertTrue(ac.get("kind_label"))

    def test_new_words_exist_in_both_languages(self):
        keys = ("nt.kind.exc", "nt.kind.inh", "nt.kind.mod", "nt.kind.unknown",
                "legend.physiology", "legend.physiology_off", "card.transmission")
        keep = i18n.lang()
        try:
            for key in keys:
                i18n.set_lang("en")
                en = i18n.t(key, exc=1, inh=2, mod=3, kind="inhibitory")
                i18n.set_lang("ru")
                ru = i18n.t(key, exc=1, inh=2, mod=3, kind="тормозной")
                self.assertNotEqual(en, key, f"{key}: нет английского текста")
                self.assertNotEqual(ru, key, f"{key}: нет русского текста")
                self.assertNotEqual(en, ru, f"{key}: переводы совпали")
                self.assertFalse(any("а" <= c.lower() <= "я" for c in en),
                                 f"{key}: в английском тексте кириллица: {en}")
        finally:
            i18n.set_lang(keep)

    def test_colouring_by_transmitter_is_translated_in_both_languages(self):
        core = PanelCore()
        core.coloring = "nt"
        keep = i18n.lang()
        try:
            i18n.set_lang("en")
            en_word = i18n.t("nt.kind.inh")
            en = [r["label"] for r in core.legend()]
            i18n.set_lang("ru")
            ru_word = i18n.t("nt.kind.inh")
            ru = [r["label"] for r in core.legend()]
        finally:
            i18n.set_lang(keep)
        self.assertNotEqual(en, ru, "подписи передачи не переводятся")
        self.assertNotEqual(en_word, ru_word, "слово о передаче не переведено")
        self.assertIn(en_word, [l for l in en if l.startswith("GABA")][0])
        self.assertIn(ru_word, [l for l in ru if l.startswith("GABA")][0])
