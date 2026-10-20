from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .config import FONT_DISPLAY, FONT_UI, PALETTE

_ICONS = {
    "reader": (
        '<path d="M14 5 L7 12 L14 19" />'
        '<path d="M17.5 9.5 A4 4 0 0 1 17.5 14.5" />'
        '<path d="M20 7 A7.5 7.5 0 0 1 20 17" />'
    ),
    "history": (
        '<path d="M4 12 A8 8 0 1 0 6.6 6.1" />'
        '<path d="M4 4 L4 9 L9 9" />'
        '<path d="M12 8 L12 12 L15 14" />'
    ),
    "voices": (
        '<rect x="9" y="3" width="6" height="11" rx="3" />'
        '<path d="M5.5 12 A6.5 6.5 0 0 0 18.5 12" />'
        '<path d="M12 18.5 L12 21" />'
    ),
    "settings": (
        '<circle cx="12" cy="12" r="3.2" />'
        '<path d="M12 2.5 L12 5 M12 19 L12 21.5 M2.5 12 L5 12 M19 12 L21.5 12'
        ' M5.3 5.3 L7 7 M17 17 L18.7 18.7 M18.7 5.3 L17 7 M7 17 L5.3 18.7" />'
    ),
    "play": '<path d="M5 2 L21 12 L5 22 Z" fill="CURRENT" stroke="none" />',
    "pause": (
        '<rect x="5" y="2" width="4.5" height="20" rx="1" fill="CURRENT" stroke="none" />'
        '<rect x="14.5" y="2" width="4.5" height="20" rx="1" fill="CURRENT" stroke="none" />'
    ),
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="CURRENT" stroke="none" />',
    "back10": (
        '<path d="M12 4 A8 8 0 1 1 4.5 9.5" />'
        '<path d="M4 3 L4 9.5 L10.5 9.5" />'
        '<text x="12" y="17.5" text-anchor="middle" font-size="7.5" '
        'font-weight="800" fill="CURRENT" stroke="none" '
        'font-family="Arial,sans-serif">10</text>'
    ),
    "fwd10": (
        '<path d="M12 4 A8 8 0 1 0 19.5 9.5" />'
        '<path d="M20 3 L20 9.5 L13.5 9.5" />'
        '<text x="12" y="17.5" text-anchor="middle" font-size="7.5" '
        'font-weight="800" fill="CURRENT" stroke="none" '
        'font-family="Arial,sans-serif">10</text>'
    ),
    "save": (
        '<path d="M5 4 h11 l3 3 v13 a1 1 0 0 1 -1 1 H6 a1 1 0 0 1 -1 -1 Z" />'
        '<path d="M8 4 v5 h8 V4" />'
    ),
    "clear": '<path d="M6 6 L18 18 M18 6 L6 18" />',
    "chevron_right": '<path d="M9.5 5 L16.5 12 L9.5 19" />',
    "chevron_down": '<path d="M5 9.5 L12 16.5 L19 9.5" />',
    "chevron_up": '<path d="M5 14.5 L12 7.5 L19 14.5" />',
    "tick_on": (
        '<rect x="4" y="4" width="16" height="16" rx="4" fill="CURRENT" stroke="none" />'
        '<path d="M8 12.2 L11 15.2 L16.2 9" stroke="WELL" stroke-width="2.2" />'
    ),
    "tick_off": (
        '<rect x="4" y="4" width="16" height="16" rx="4" />'
    ),
    "minimise": '<path d="M6 12 L18 12" />',
    "power": '<path d="M12 3 L12 11" /><path d="M7 6.5 A7 7 0 1 0 17 6.5" />',
    "refresh": (
        '<path d="M19 9 A7.5 7.5 0 0 0 5 9" />'
        '<path d="M8 6 L5 9 L8 12" />'
        '<path d="M5 15 A7.5 7.5 0 0 0 19 15" />'
        '<path d="M16 12 L19 15 L16 18" />'
    ),
    "expand": (
        '<rect x="4" y="4" width="16" height="16" rx="2" />'
        '<path d="M9 4 L9 20" />'
    ),
    "menu": (
        '<rect x="3" y="4" width="18" height="16" rx="2" />'
        '<path d="M9 4 L9 20" />'
    ),
    "star": (
        '<path d="M12 3.2 l2.7 5.6 6.1 0.85 -4.45 4.3 1.06 6.05 '
        '-5.41 -2.87 -5.41 2.87 1.06 -6.05 -4.45 -4.3 6.1 -0.85 Z" />'
    ),
    "star_full": (
        '<path fill="CURRENT" d="M12 3.2 l2.7 5.6 6.1 0.85 -4.45 4.3 1.06 6.05 '
        '-5.41 -2.87 -5.41 2.87 1.06 -6.05 -4.45 -4.3 6.1 -0.85 Z" />'
    ),
    "highlight": (
        '<path d="M3.2 5 H20.8" />'
        '<rect x="3.2" y="8.6" width="15.5" height="6.2" rx="1.6" '
        'fill="CURRENT" stroke="none" />'
        '<path d="M3.2 19 H17.5" />'
    ),
    "translate": (
        '<path d="M1.6 14.2 L5.3 4.4 L9 14.2" />'
        '<path d="M3.2 10.9 H7.4" />'
        '<path d="M12 3.2 V20.8" opacity="0.4" />'
        '<path d="M14.9 8.2 H22.4" />'
        '<path d="M18.65 5.4 V8.2" />'
        '<path d="M21.1 10.9 H16.2" />'
        '<path d="M16.2 10.9 Q16.2 16.4 22.4 19.2" />'
        '<path d="M21.1 10.9 Q21.1 16.4 15.1 19.2" />'
    ),
    "swap": (
        '<path d="M4 8.4 H18" />'
        '<path d="M15.2 5.4 L18.4 8.4 L15.2 11.4" />'
        '<path d="M20 15.6 H6" />'
        '<path d="M8.8 12.6 L5.6 15.6 L8.8 18.6" />'
    ),
}


def wave_pixmap(
    color: str,
    arc1: float = 1.0,
    arc2: float = 1.0,
    size: int = 26,
    stroke: float = 1.9,
) -> QPixmap:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{size}" height="{size}" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="M14 5 L7 12 L14 19" />'
        f'<path d="M17.5 9.5 A4 4 0 0 1 17.5 14.5" stroke-opacity="{arc1:.2f}" />'
        f'<path d="M20 7 A7.5 7.5 0 0 1 20 17" stroke-opacity="{arc2:.2f}" />'
        f"</svg>"
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    image = QImage(QSize(size, size), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return QPixmap.fromImage(image)


_CHECKMARK_PATH: str | None = None


def _checkmark_svg_path() -> str:
    global _CHECKMARK_PATH
    if _CHECKMARK_PATH and os.path.exists(_CHECKMARK_PATH):
        return _CHECKMARK_PATH
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 19 19">'
        '<path d="M5 9.5 L8.5 13 L14 7" fill="none" stroke="white" '
        'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )
    path = os.path.join(tempfile.gettempdir(), "backtalk_checkmark.svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    _CHECKMARK_PATH = path
    return path


def svg_icon(name: str, color: str, size: int = 40, *, bg: str = "") -> QIcon:
    body = _ICONS[name].replace("CURRENT", color).replace("WELL", PALETTE["surface"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{size}" height="{size}" fill="none" stroke="{color}" '
        f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</svg>"
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    image = QImage(QSize(size, size), QImage.Format_ARGB32)
    image.fill(QColor(bg) if bg else Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return QIcon(QPixmap.fromImage(image))


def stylesheet() -> str:
    C = PALETTE
    return f"""
QWidget {{
    background: {C['bg']};
    color: {C['text']};
    font-family: {FONT_UI};
    font-size: 14px;
}}

QLabel, QCheckBox {{ background: transparent; }}

#Sidebar {{
    background: {C['sidebar']};
    border-right: 1px solid {C['border']};
}}
#AppName {{
    font-family: {FONT_DISPLAY};
    font-size: 25px;
    font-weight: 400;
    color: {C['text']};
    letter-spacing: -0.2px;
}}
#NavButton {{
    background: transparent;
    border: none;
    border-radius: 9px;
    padding: 11px 14px;
    text-align: left;
    font-size: 15px;
    color: {C['text_muted']};
}}
#NavButton:hover {{
    background: rgba(250, 142, 82, 0.13);
    color: {C['text']};
}}
#NavButton:checked {{
    background: rgba(250, 142, 82, 0.20);
    color: {C['text']};
    font-weight: 600;
}}

#TabBar {{ background: transparent; }}
#TabButton {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 9px 4px;
    margin-right: 22px;
    font-size: 15px;
    color: {C['text_muted']};
}}
#TabButton:hover {{ color: {C['text']}; }}
#TabButton:checked {{
    color: {C['text']};
    font-weight: 600;
    border-bottom: 2px solid {C['primary']};
}}

#TopBar {{
    background: {C['surface']};
    border-bottom: 1px solid {C['border']};
}}

#FieldLabel {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.1px;
    color: {C['text_muted']};
}}

QTextEdit, QPlainTextEdit {{
    background: {C['input']};
    border: 1px solid {C['border']};
    border-radius: 11px;
    padding: 14px;
    font-size: 15px;
    line-height: 152%;
    selection-background-color: {C['primary']};
    selection-color: #FFFFFF;
}}
QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {C['primary']}; }}

#Card {{
    background: {C['sidebar']};
    border: 1px solid {C['border']};
    border-radius: 13px;
}}

QComboBox {{
    background: {C['input']};
    border: 1px solid {C['border']};
    border-radius: 9px;
    padding: 9px 12px;
    min-height: 20px;
    color: {C['text']};
}}
QComboBox:hover {{ border: 1px solid {C['secondary']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {C['input']};
    border: 1px solid {C['border']};
    border-radius: 9px;
    padding: 5px;
    selection-background-color: {C['primary']};
    selection-color: #FFFFFF;
    outline: none;
}}

QSlider::groove:horizontal {{
    height: 5px;
    background: {C['border']};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {C['primary']};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {C['surface']};
    border: 2px solid {C['primary']};
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 9px;
}}
QSlider::handle:horizontal:hover {{ background: {C['primary']}; }}

#TransportButton {{
    background-color: {C['sidebar']};
    border: none;
    padding: 0px;
    color: {C['text']};
}}
#TransportButton:hover {{
    background-color: rgba(250, 142, 82, 0.15);
    border-radius: 14px;
}}
#TransportButton:disabled {{ background-color: {C['sidebar']}; }}

#GhostButton {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 7px 15px;
    color: {C['text_muted']};
}}
#GhostButton:hover {{
    border: 1px solid {C['primary']};
    color: {C['text']};
}}
#GhostButton:checked {{
    background: {C['primary']};
    border: 1px solid {C['primary']};
    color: #FFFFFF;
    font-weight: 600;
}}

/* Sidebar toggles read as flat white on tan until they are given a fill of
   their own, so the active state carries the accent colour. */
#SidebarToggle {{
    background: transparent;
    border: 1px solid {C['border']};
    border-radius: 9px;
    padding: 10px 14px;
    text-align: left;
    font-size: 15px;
    color: {C['text_muted']};
}}
#SidebarToggle:hover {{
    border: 1px solid {C['primary']};
    color: {C['text']};
}}
#SidebarToggle:checked {{
    background: {C['primary']};
    border: 1px solid {C['primary']};
    color: #FFFFFF;
    font-weight: 600;
}}

/* Translate gets its OWN accent, not the brand coral.

   It reuses #SidebarToggle for shape and spacing but overrides every colour.
   Without these rules the checked state inherits the coral fill above, which
   makes the app's most distinctive feature look like just another toggle --
   and that was the one thing this control was not allowed to look like. */
#TranslateToggle {{
    background: transparent;
    border: 1px solid {C['border']};
    border-radius: 9px;
    padding: 10px 14px;
    text-align: left;
    font-size: 15px;
    color: {C['text_muted']};
}}
#TranslateToggle:hover {{
    border: 1px solid {C['translate']};
    color: {C['text']};
}}
#TranslateToggle:checked {{
    background: {C['translate_fill']};
    border: 1px solid {C['translate']};
    color: {C['translate_on']};
    font-weight: 600;
}}

#PrimaryButton {{
    background: {C['primary']};
    border: none;
    border-radius: 9px;
    padding: 10px 20px;
    color: #FFFFFF;
    font-weight: 600;
}}
#PrimaryButton:hover {{ background: {C['primary_dim']}; }}
#PrimaryButton:disabled {{ background: {C['border']}; color: {C['text_muted']}; }}

#StatusPill {{
    border-radius: 11px;
    padding: 4px 12px;
    font-size: 12px;
    font-weight: 600;
}}

#TimeLabel {{
    color: {C['text_muted']};
    font-size: 12px;
}}

/* The swap control sits BETWEEN the two voice pickers, so it is deliberately
   quiet: no fill, no border until hovered. A third heavy control between two
   dropdowns would read as a fourth field rather than a gesture. */
/* The voice slots are plain QWidgets used purely to group a label with its
   combo. Without this they inherit the global QWidget background and paint a
   grey block behind the label. */
#VoiceSlot {{ background: transparent; }}

#SwapButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 7px;
    min-height: 20px;
}}
#SwapButton:hover {{ border: 1px solid {C['translate']}; }}

#CaptureBanner {{
    background: rgba(250, 142, 82, 0.14);
    border: 1px solid {C['primary']};
    border-radius: 11px;
    padding: 12px 15px;
}}

/* The engine is broken and the app cannot speak. Deliberately NOT the coral
   used by CaptureBanner: coral is the brand and marks a normal, wanted state.
   A user whose install is broken has to be able to tell the two apart at a
   glance, so this one takes the danger colour and a heavier border. */
#EngineBanner {{
    /* An explicit rgba, NOT "{{danger}}22". Qt reads 8-digit hex as #AARRGGBB,
       so appending an alpha pair to a #RRGGBB value silently reinterprets every
       channel: #D4574A22 came out olive, and a screenshot of the real window is
       what caught it. One literal works for both palettes, the same way
       CaptureBanner uses one coral for both. */
    background: rgba(200, 82, 66, 0.16);
    border: 1px solid {C['danger']};
    border-radius: 11px;
    padding: 12px 15px;
}}

#EngineBannerText {{
    color: {C['danger']};
    font-size: 14px;
    font-weight: 600;
}}

/* The engine is running and speaking, but it may not be the build the user
   thinks it is. This is softer than EngineBanner on purpose: audio still
   works, so a red, alarming banner would be a false alarm every time it fires.
   It exists because a stale developer engine served the app silently for 35
   hours before anyone noticed - nothing was broken, so nothing complained. */
#EngineNotice {{
    /* Same explicit-rgba rule as EngineBanner above: never "{{working}}22".
       Qt reads 8-digit hex as #AARRGGBB, not #RRGGBBAA, so appending an alpha
       pair silently reinterprets every channel and produces the wrong colour. */
    background: rgba(184, 134, 11, 0.14);
    border: 1px solid {C['working']};
    border-radius: 11px;
    padding: 12px 15px;
}}

#EngineNoticeText {{
    /* NOT {C['working']}. The palette comment elsewhere in this file already
       records that yellow text on the cream background is close to unreadable.
       The amber marks the frame; the text stays the normal readable colour. */
    color: {C['text']};
    font-size: 14px;
    font-weight: 600;
}}

#SpokenText {{
    color: {C['text_muted']};
    font-size: 13px;
    font-style: italic;
}}

QListWidget {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 11px;
    padding: 6px;
    outline: none;
}}
QListWidget::item {{
    padding: 11px 13px;
    border-radius: 8px;
    color: {C['text']};
}}
QListWidget::item:hover {{ background: rgba(185, 147, 115, 0.14); }}
QListWidget::item:selected {{
    background: rgba(250, 142, 82, 0.22);
    color: {C['text']};
}}

#PathLabel {{
    color: {C['text_muted']};
    font-size: 12px;
    background: transparent;
}}
#VoiceList::item {{
    padding: 0px;
    border-radius: 8px;
}}
#VoiceExplainer {{
    color: {C['text_muted']};
    font-size: 12px;
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 10px 14px;
}}
#VoiceName {{
    color: {C['text']};
    font-size: 14px;
    background: transparent;
}}
#VoiceDetail {{
    color: {C['text_muted']};
    font-size: 12px;
    background: transparent;
}}
#VoiceRow {{
    background: transparent;
    border-radius: 8px;
}}
#VoiceRow:hover {{ background: rgba(217, 119, 87, 0.10); }}
#VoiceRow[selected="true"] {{ background: rgba(217, 119, 87, 0.22); }}
#StarButton, #TickButton {{
    background: transparent;
    border: none;
    padding: 0px;
    margin: 0px;
}}
#StarButton:hover, #TickButton:hover {{
    background: rgba(217, 119, 87, 0.18);
    border-radius: 5px;
}}
#TickButton:disabled {{ background: transparent; }}

/* Voices page: one collapsible section per language. */
#LangHeader {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    padding: 11px 14px;
    text-align: left;
    color: {C['text']};
    font-size: 14px;
}}
#LangHeader:hover {{ border-color: {C['primary']}; }}
#LangHeader:checked {{
    border-color: {C['primary']};
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
}}
#LangCount {{
    color: {C['text_muted']};
    font-size: 12px;
    background: transparent;
}}
#LangBody {{
    background: {C['surface']};
    border: 1px solid {C['primary']};
    border-top: none;
    border-bottom-left-radius: 10px;
    border-bottom-right-radius: 10px;
}}
#CurrentVoice {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-left: 3px solid {C['primary']};
    border-radius: 8px;
    padding: 9px 14px;
    color: {C['text']};
    font-size: 13px;
}}
/* The voice table's heading row. The headings double as the sort control:
   click one to sort by that column. */
#VoiceHeader {{
    background: transparent;
    border-bottom: 1px solid {C['border']};
}}
#HeaderSpacer {{ background: transparent; }}
#ColRule {{
    background: {C['border']};
    border: none;
    color: {C['border']};
}}
#ColHeader {{ background: transparent; }}
#ColHeaderText {{
    background: transparent;
    color: {C['text_muted']};
    font-size: 12px;
    font-weight: 600;
}}
#ColHeader:hover #ColHeaderText {{ color: {C['text']}; }}
#ColHeader[active="true"] #ColHeaderText {{ color: {C['primary']}; }}
#TickAllBox::indicator {{
    width: 17px;
    height: 17px;
    border: 1px solid {C['text_muted']};
    border-radius: 4px;
    background: transparent;
}}
#TickAllBox::indicator:checked {{
    background: {C['primary']};
    border-color: {C['primary']};
}}

QTableWidget {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 11px;
    gridline-color: {C['border']};
    outline: none;
}}
QTableWidget::item {{
    padding: 8px 10px;
    color: {C['text']};
}}
QTableWidget::item:selected {{
    background: rgba(250, 142, 82, 0.22);
    color: {C['text']};
}}
QHeaderView::section {{
    background: {C['sidebar']};
    color: {C['text_muted']};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    padding: 8px 10px;
    border: none;
    border-bottom: 1px solid {C['border']};
    border-right: 1px solid {C['border']};
}}
QHeaderView::section:last {{ border-right: none; }}

QCheckBox {{ spacing: 9px; }}
QCheckBox::indicator {{
    width: 19px; height: 19px;
    border: 1px solid {C['border']};
    border-radius: 5px;
    background: {C['input']};
}}
QCheckBox::indicator:checked {{
    background: {C['primary']};
    border: 1px solid {C['primary']};
    image: url({_checkmark_svg_path().replace(os.sep, '/')});
}}

QScrollBar:vertical {{
    background: transparent; width: 11px; margin: 3px;
}}
QScrollBar::handle:vertical {{
    background: {C['border']}; border-radius: 5px; min-height: 34px;
}}
QScrollBar::handle:vertical:hover {{ background: {C['secondary']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background: {C['text']};
    color: {C['bg']};
    border: none;
    border-radius: 6px;
    padding: 6px 9px;
}}

/* The floating widget sits on top of other applications, so it is drawn as a
   soft capsule rather than a second square window competing for attention. */
#CompactCard {{
    background: {C['bg']};
    border: 1px solid {C['border']};
    border-radius: 26px;
}}
#CompactPlay {{
    background: {C['text']};
    border: none;
    border-radius: 20px;
    min-width: 40px;
    max-width: 40px;
    min-height: 40px;
    max-height: 40px;
}}
#CompactPlay:hover {{ background: {C['primary']}; }}
#CompactPlay:disabled {{ background: {C['border']}; }}
#CompactButton {{
    background: transparent;
    border: none;
    border-radius: 13px;
    min-width: 26px;
    max-width: 26px;
    min-height: 26px;
    max-height: 26px;
    color: {C['text_muted']};
}}
#CompactButton:hover {{ background: rgba(217, 119, 87, 0.20); }}
"""
