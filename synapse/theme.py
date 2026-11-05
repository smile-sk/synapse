"""Synapse — тема окна: тёмная и светлая, одним источником правды.

ЗАЧЕМ (слово Стаса 25.09.2026): «сделай тёмную тему». Смысл был не в украшении:
окно Qt по умолчанию светлое, а полотно тела — тёмное, поэтому панель выглядела
сборной из двух разных программ. Здесь палитра одна на всё: и на виджеты (QSS),
и на само тело (фон полотна, цвет связей, цвет узлов).

ЧТО ГДЕ ЛЕЖИТ:
  * PALETTE — числа палитры на каждую тему (одна таблица, ничего не разбросано);
  * qss() — стиль всех виджетов, собранный из палитры подстановкой $имя
    (не .format: в QSS живут фигурные скобки, и подстановка их съела бы);
  * bg() / edge_dim() / hot() / role_colors() — то, что читает отрисовка тела;
  * adapt() — приглушить цвет под светлый фон (палитра медиаторов приходит из
    файла тела и настроена на тёмный фон).

Тема — состояние процесса, а не файла: кто её выбрал, тот и зовёт set_theme().
Значение по умолчанию берётся из настроек (ui.theme) — см. sync_from_config().
"""
from __future__ import annotations

from string import Template

THEMES = ("dark", "light")
DEFAULT = "dark"
_current = DEFAULT

#: Палитра. Одна таблица на обе темы — чтобы светлая не отставала от тёмной.
PALETTE: dict[str, dict] = {
    "dark": {
        "win_bg": "#0e141b",
        "panel_bg": "#151d26",
        "alt_bg": "#111820",
        "input_bg": "#0c1116",
        "text": "#dbe6f2",
        "dim": "#8fb6d9",
        "accent": "#4ea1ff",
        "accent_text": "#08121c",
        "border": "#24313f",
        "hover": "#1b2836",
        "sel_bg": "#1d3550",
        "disabled": "#5a6b7d",
        # --- тело (OpenGL-полотно) ---
        "body_bg": "#0a0e13",
        "edge_dim": "#16202b",
        "hot": "#ffe9a8",
        "roles": {"sensory": "#4ea1ff", "inter": "#a475ff", "motor": "#ff8a4c",
                  "polymodal": "#ffd166", "other": "#7f8c99", "unknown": "#7f8c99"},
        "zones": {"голова": "#ff7ab8", "середина": "#7ad8ff", "хвост": "#c8ff7a"},
        "nt_fallback": "#55637a",
    },
    "light": {
        "win_bg": "#eef2f6",
        "panel_bg": "#ffffff",
        "alt_bg": "#f7fafc",
        "input_bg": "#ffffff",
        "text": "#1b2430",
        "dim": "#4a5b6d",
        "accent": "#1d6fd0",
        "accent_text": "#ffffff",
        "border": "#c9d3dd",
        "hover": "#e2eaf3",
        "sel_bg": "#cfe2f7",
        "disabled": "#94a3b3",
        # --- тело: светлое полотно, узлы темнее (иначе не видно) ---
        "body_bg": "#f4f7fa",
        "edge_dim": "#c3ccd6",
        "hot": "#c2410c",
        "roles": {"sensory": "#1565c0", "inter": "#6a3fc0", "motor": "#d84315",
                  "polymodal": "#a8710a", "other": "#607080", "unknown": "#607080"},
        "zones": {"голова": "#c2185b", "середина": "#0277bd", "хвост": "#558b2f"},
        "nt_fallback": "#4a5b6d",
    },
}

_QSS = Template("""
QWidget { background: $win_bg; color: $text; font-size: 12px; }
QMainWindow, QSplitter { background: $win_bg; }
QLabel { background: transparent; }
QLabel#dim { color: $dim; }
QTabWidget::pane { border: 1px solid $border; top: -1px; background: $panel_bg; }
QTabBar::tab { background: $alt_bg; color: $dim; padding: 6px 12px; border: 1px solid $border;
               border-bottom: none; }
QTabBar::tab:selected { background: $panel_bg; color: $text; }
QTabBar::tab:hover { color: $text; }
QPushButton { background: $alt_bg; color: $text; border: 1px solid $border;
              border-radius: 4px; padding: 5px 10px; }
QPushButton:hover { background: $hover; }
QPushButton:pressed { background: $sel_bg; }
QPushButton:checked { background: $accent; color: $accent_text; border-color: $accent; }
QPushButton:disabled { color: $disabled; }
QLineEdit, QSpinBox, QComboBox { background: $input_bg; color: $text;
              border: 1px solid $border; border-radius: 4px; padding: 4px 6px; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: $accent; }
QLineEdit:disabled, QComboBox:disabled { color: $disabled; }
QComboBox QAbstractItemView { background: $panel_bg; color: $text;
              selection-background-color: $sel_bg; border: 1px solid $border; }
QCheckBox { background: transparent; }
QTableWidget, QListWidget { background: $input_bg; alternate-background-color: $alt_bg;
              gridline-color: $border; border: 1px solid $border; }
QTableWidget::item:selected, QListWidget::item:selected { background: $sel_bg; color: $text; }
QHeaderView::section { background: $alt_bg; color: $dim; border: none;
              border-right: 1px solid $border; border-bottom: 1px solid $border; padding: 4px; }
QTextEdit { background: $input_bg; color: $text; border: 1px solid $border; }
QScrollBar:vertical, QScrollBar:horizontal { background: $alt_bg; border: none; margin: 0; }
QScrollBar:vertical { width: 12px; }
QScrollBar:horizontal { height: 12px; }
QScrollBar::handle { background: $border; border-radius: 5px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: $dim; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QGroupBox { border: 1px solid $border; border-radius: 6px; margin-top: 12px; padding: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: $dim; }
QSplitter::handle { background: $border; }
""")


def available() -> list[str]:
    return list(THEMES)


def theme() -> str:
    return _current


def set_theme(code: str | None) -> str:
    """Выбрать тему. Незнакомое имя не принимаем — остаёмся на прежней."""
    global _current
    code = str(code or "").strip().lower()
    if code in THEMES:
        _current = code
    return _current


def sync_from_config(cfg: dict) -> str:
    section = (cfg or {}).get("ui") or {}
    return set_theme(section.get("theme") or DEFAULT)


def pal() -> dict:
    return PALETTE.get(_current, PALETTE[DEFAULT])


def qss() -> str:
    """Стиль виджетов для текущей темы (в QSS живут скобки — потому Template)."""
    return _QSS.substitute(pal())


def bg() -> str:
    """Фон полотна тела — им же чистит кадр видеокарта."""
    return pal()["body_bg"]


def edge_dim() -> str:
    """Цвет тусклой сетки связей — тёмный на тёмной теме, светло-серый на светлой."""
    return pal()["edge_dim"]


def edge_dim_f() -> tuple[float, float, float]:
    """Тот же цвет долями 0..1 — так его ждёт буфер вершин видеокарты."""
    r, g, b = rgb(edge_dim())
    return (r / 255.0, g / 255.0, b / 255.0)


def hot() -> str:
    return pal()["hot"]


def role_colors() -> dict:
    return dict(pal()["roles"])


def zone_colors() -> dict:
    return dict(pal()["zones"])


def nt_fallback() -> str:
    return pal()["nt_fallback"]


def dim() -> str:
    """Приглушённый цвет текста темы — им подписываем числа в легенде цветов."""
    return pal()["dim"]


def rgb(c: str) -> tuple[int, int, int]:
    """'#rrggbb' -> (r, g, b)."""
    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))


def adapt(color: str) -> str:
    """Приглушить цвет под текущий фон.

    Нужно для палитры медиаторов: она приходит ИЗ ФАЙЛА тела и настроена на
    тёмный фон — на светлом такие цвета слепнут. На тёмной теме цвет не трогаем.
    """
    if _current != "light":
        return color
    try:
        r, g, b = rgb(color)
    except (ValueError, IndexError):
        return color
    k = 0.72
    return "#%02x%02x%02x" % (int(r * k), int(g * k), int(b * k))
