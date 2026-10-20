from __future__ import annotations

import atexit
import ctypes
import ctypes.wintypes as _wt
import datetime as _dt
import faulthandler
import shutil
from html import escape as _esc
from pathlib import Path

from PySide6.QtCore import (
    Qt, QEasingCurve, QPropertyAnimation, QRect, QSize, QThread, QTimer,
    Signal, QtMsgType, qInstallMessageHandler,
)
from PySide6.QtGui import (
    QColor, QFontMetrics, QGuiApplication, QIcon, QPainter, QPainterPath,
    QPen, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QProgressBar, QSpinBox, QStackedWidget, QStyle, QStyleOption,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from . import config, engine_identity, hwinfo, stallwatch, translate as translatelib, voices as voicelib
from .config import PALETTE as C
from .capture import SelectionCapture
from .engine import KokoroClient, Player
from .theme import stylesheet, svg_icon, wave_pixmap


def _mmss(seconds: float) -> str:
    if seconds <= 0:
        return "0:00"
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _pair_lines(original: str, translated: str) -> list[tuple[str, str]]:
    src = [ln for ln in original.splitlines() if ln.strip()]
    dst = [ln for ln in translated.splitlines() if ln.strip()]
    if src and len(src) == len(dst):
        return list(zip(src, dst))
    return [(original.strip(), translated.strip())]


def _pair_html(original: str, translated: str, heading: str = "") -> str:
    parts: list[str] = []
    if heading:
        parts.append(
            f'<p style="margin:0 0 14px 0; font-size:11px;'
            f' letter-spacing:1px; color:{C["primary"]};">'
            f'{_esc(heading.upper())}</p>'
        )
    for src, dst in _pair_lines(original, translated):
        parts.append(
            f'<p style="margin:0 0 3px 0; color:{C["text_muted"]};">'
            f'{_esc(src)}</p>'
            f'<p style="margin:0 0 16px 0; color:{C["text"]};">'
            f'{_esc(dst)}</p>'
        )
    return f'<div style="font-family:{config.FONT_UI};">{"".join(parts)}</div>'


class TranslateToggle(QPushButton):

    STEP = 9
    SEGMENT = 74
    MIN_VISIBLE_MS = 700

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setCheckable(True)
        self._working = False
        self._stop_pending = False
        self._elapsed = 0
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def set_working(self, working: bool) -> None:
        working = bool(working) and self.isChecked()
        if working == self._working:
            return
        if working:
            self._working = True
            self._phase = 0.0
            self._elapsed = 0
            self._timer.start()
            self.update()
            return
        if self._elapsed < self.MIN_VISIBLE_MS:
            self._stop_pending = True
            return
        self._finish()

    def _finish(self) -> None:
        self._working = False
        self._stop_pending = False
        self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase += self.STEP
        self._elapsed += self._timer.interval()
        if self._stop_pending and self._elapsed >= self.MIN_VISIBLE_MS:
            self._finish()
            return
        self.update()

    def _outline(self) -> QPainterPath:
        path = QPainterPath()
        inset = 1.5
        path.addRoundedRect(
            inset, inset,
            max(1.0, self.width() - inset * 2),
            max(1.0, self.height() - inset * 2),
            9, 9,
        )
        return path

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.isChecked():
            return
        if not self._working:
            return
        path = self._outline()
        length = path.length()
        if length <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        moving = QPen(QColor(C["working"]))
        moving.setWidthF(2.6)
        moving.setCapStyle(Qt.RoundCap)
        w = moving.widthF()
        moving.setDashPattern([self.SEGMENT / w, (length - self.SEGMENT) / w])
        moving.setDashOffset((self._phase % length) / w)
        painter.setPen(moving)
        painter.drawPath(path)
        painter.end()


_GRAB_STRIP_H = 32
_GRAB_MIN_W = 120
_GRAB_MIN_H = 20

_ICON_PLAY = 36
_ICON_SKIP = 28
_ICON_SAVE = 24

_ENGINE_REFRESH_MINUTES: dict[str, int] = {
    "5m": 5,
    "30m": 30,
    "2h": 120,
    "5h": 300,
}
_IDLE_WARN_FIRST_MS = 5 * 60_000
_IDLE_WARN_LAST_MS = 60_000


def _reachable(rect: QRect, screens=None) -> bool:
    if screens is None:
        screens = QGuiApplication.screens()
    handle = QRect(rect.left(), rect.top(), rect.width(), _GRAB_STRIP_H)
    for screen in screens:
        overlap = screen.availableGeometry().intersected(handle)
        if overlap.width() >= _GRAB_MIN_W and overlap.height() >= _GRAB_MIN_H:
            return True
    return False


def _clip(text: str, limit: int) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= limit:
        return one_line
    cut = one_line[:limit]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(" ,;:.!?-") + "..."


def _label(text: str, object_name: str = "") -> QLabel:
    lab = QLabel(text)
    if object_name:
        lab.setObjectName(object_name)
    return lab


class _ElidedLabel(QLabel):

    def __init__(self, text: str = "", mode=Qt.ElideRight,
                 object_name: str = "") -> None:
        super().__init__(text)
        self._full = text
        self._mode = mode
        if object_name:
            self.setObjectName(object_name)
        self.setMinimumWidth(0)
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Ignored)
        self.setSizePolicy(policy)

    def setText(self, text: str) -> None:
        self._full = text or ""
        self.setToolTip(self._full)
        self._render()

    def text(self) -> str:
        return self._full

    def _render(self) -> None:
        available = max(0, self.width() - 2)
        if not available:
            QLabel.setText(self, self._full)
            return
        metrics = QFontMetrics(self.font())
        QLabel.setText(
            self, metrics.elidedText(self._full, self._mode, available))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, QLabel.minimumSizeHint(self).height())


def _wrapping_check(text: str) -> tuple[QHBoxLayout, QCheckBox]:
    row = QHBoxLayout()
    row.setSpacing(8)
    box = QCheckBox()
    box.setCursor(Qt.PointingHandCursor)
    box.setAccessibleName(text)
    row.addWidget(box, 0, Qt.AlignTop)
    label = QLabel(text)
    label.setWordWrap(True)
    label.setCursor(Qt.PointingHandCursor)
    label.mousePressEvent = lambda _event, b=box: b.toggle()
    row.addWidget(label, 1)
    return row, box


def _fit_popup(combo: QComboBox) -> None:
    from PySide6.QtGui import QFontMetrics
    metrics = QFontMetrics(combo.font())
    widest = max(
        (metrics.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())),
        default=0,
    )
    if widest:
        combo.view().setMinimumWidth(widest + 40)


def _scrollable(page: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    area.setWidget(page)
    return area


class _ServerWarmup(QThread):
    ready = Signal(bool)
    progress = Signal(str, int)
    identified = Signal(object)

    def __init__(self, client: KokoroClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        import time
        if self._client.health():
            self.progress.emit("Connected", 100)
            try:
                self.identified.emit(self._client.identify())
            except Exception:
                pass
            self.ready.emit(True)
            return
        self.progress.emit("Starting voice engine", 5)
        self._client.start_server()
        self.progress.emit("Loading voice model", 10)
        elapsed = 0
        while elapsed < 240:
            if self.isInterruptionRequested():
                return
            if self._client.health(timeout=1.5):
                self.progress.emit("Ready", 100)
                try:
                    self.identified.emit(self._client.identify())
                except Exception:
                    pass
                self.ready.emit(True)
                return
            elapsed += 1
            pct = min(95, 10 + int(85 * elapsed / config.COLD_START_SECONDS))
            if elapsed < 10:
                msg = "Starting voice engine"
            elif elapsed < 30:
                msg = "Loading voice model"
            elif elapsed < 60:
                msg = "Preparing voices"
            else:
                msg = "Almost ready"
            self.progress.emit(msg, pct)
            time.sleep(1.0)
        self.progress.emit("Could not connect", 0)
        self.ready.emit(False)


class _RefreshWorker(QThread):
    checked = Signal(bool)

    def __init__(self, client: KokoroClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            if self._client.unloaded:
                self._client.ensure_loaded()
            ok = bool(self._client.health(timeout=2.0))
        except Exception:
            ok = False
        self.checked.emit(ok)


class _TranslateWorker(QThread):
    done = Signal(object)

    def __init__(self, text: str, target: str, source: str | None) -> None:
        super().__init__()
        self._text = text
        self._target = target
        self._source = source

    def run(self) -> None:
        from .translate import translator
        self.done.emit(translator.translate_text(self._text, self._target))


_LIVE_THREADS: list[QThread] = []


def _sweep_live_threads() -> None:
    _LIVE_THREADS[:] = [t for t in _LIVE_THREADS if not t.isFinished()]


@atexit.register
def _wait_for_translate_threads() -> None:
    for thread in list(_LIVE_THREADS):
        if thread.isRunning():
            thread.wait(20000)


class _VoicePlanWorker(QThread):

    done = Signal(list, list)

    def __init__(self, text: str, target: str, voice: str,
                 available: list[str], switch_latin: bool = False) -> None:
        super().__init__()
        self._text = text
        self._target = target
        self._voice = voice
        self._available = available
        self._switch_latin = switch_latin

    def run(self) -> None:
        from .translate import translator
        skipped: list[str] = []
        plan = translator.voice_plan(
            self._text, self._target, self._voice,
            lambda lang: voicelib.default_voice(self._available, lang),
            on_skip=lambda chunk, lang: skipped.append(
                translatelib.language_name(lang) if lang else "that text"
            ),
            switch_latin=self._switch_latin,
        )
        self.done.emit(plan, skipped)


class _TranslateWarmup(QThread):

    def __init__(self, target: str) -> None:
        super().__init__()
        self._target = target

    def run(self) -> None:
        from .translate import translator
        translator.warm(self._target)


class WaveLogo(QLabel):

    _PHASES = ((0.25, 0.15), (1.0, 0.3), (1.0, 1.0), (0.4, 0.75))

    def __init__(self, size: int = 26, stroke: float = 1.9) -> None:
        super().__init__()
        self._size = size
        self._stroke = stroke
        self._state = "idle"
        self._phase = 0
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self._timer = QTimer(self)
        self._timer.setInterval(260)
        self._timer.timeout.connect(self._advance)
        self._repaint()

    def _colour(self) -> str:
        return {
            "speaking": C["primary"],
            "ready": C["ok"],
            "offline": C["danger"],
        }.get(self._state, C["secondary"])

    def _advance(self) -> None:
        self._phase = (self._phase + 1) % len(self._PHASES)
        self._repaint()

    def _repaint(self) -> None:
        colour = self._colour()
        if self._state == "speaking":
            arc1, arc2 = self._PHASES[self._phase]
        elif self._state == "idle":
            arc1 = arc2 = 0.45
        else:
            arc1 = arc2 = 1.0
        self.setPixmap(
            wave_pixmap(colour, arc1, arc2, self._size, self._stroke)
        )

    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self._phase = 0
        if state == "speaking":
            self._timer.start()
        else:
            self._timer.stop()
        self._repaint()
        self.setToolTip({
            "speaking": "Speaking",
            "ready": "Ready to play",
            "offline": "Voice engine offline",
        }.get(state, "Nothing loaded"))

    def refresh_theme(self) -> None:
        self._repaint()


class SplashScreen(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(560, 400)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setObjectName("SplashCard")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(56, 52, 56, 40)
        card_lay.setSpacing(0)

        self.logo = WaveLogo(size=132, stroke=1.35)
        self.logo.set_state("speaking")
        logo_row = QHBoxLayout()
        logo_row.addStretch(1)
        logo_row.addWidget(self.logo)
        logo_row.addStretch(1)
        card_lay.addLayout(logo_row)
        card_lay.addSpacing(26)

        title = QLabel(config.APP_NAME)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"font-family: {config.FONT_DISPLAY}; font-size: 40px;"
            f" color: {C['text']}; background: transparent;"
        )
        card_lay.addWidget(title)
        card_lay.addSpacing(26)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        card_lay.addWidget(self.bar)
        card_lay.addSpacing(14)

        self.status = QLabel("Starting...")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet(
            f"font-family: {config.FONT_UI}; font-size: 14px;"
            f" color: {C['text_muted']}; background: transparent;"
        )
        card_lay.addWidget(self.status)
        card_lay.addStretch(1)

        ver = QLabel(f"v{config.APP_VERSION}")
        ver.setAlignment(Qt.AlignCenter)
        ver.setStyleSheet(
            f"font-size: 11px; color: {C['text_muted']};"
            " background: transparent;"
        )
        card_lay.addWidget(ver)

        card.setStyleSheet(
            f"#SplashCard {{ background: {C['bg']};"
            f" border: 1px solid {C['border']}; border-radius: 22px; }}"
            f" QProgressBar {{ background: {C['border']};"
            f" border: none; border-radius: 3px; }}"
            f" QProgressBar::chunk {{ background: {C['primary']};"
            f" border-radius: 3px; }}"
        )
        outer.addWidget(card)

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - 560) // 2,
                geo.y() + (geo.height() - 400) // 2,
            )

    def set_progress(self, message: str, percent: int) -> None:
        self.status.setText(message)
        self.bar.setValue(percent)


class ReaderPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._pair_source = ""
        self._pair_translated = ""
        self._pair_heading = ""
        self._engine_problem = ""
        self._engine_notice = ""
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 22, 34, 26)
        root.setSpacing(16)

        self.engine_banner = QFrame()
        self.engine_banner.setObjectName("EngineBanner")
        engine_row = QHBoxLayout(self.engine_banner)
        engine_row.setContentsMargins(15, 11, 15, 11)
        self.engine_banner_label = _label("", "EngineBannerText")
        self.engine_banner_label.setWordWrap(True)
        engine_row.addWidget(self.engine_banner_label, 1)
        engine_row.addStretch(1)
        root.addWidget(self.engine_banner)
        self.engine_banner.hide()

        self.banner = QFrame()
        self.banner.setObjectName("CaptureBanner")
        banner_row = QHBoxLayout(self.banner)
        banner_row.setContentsMargins(15, 11, 15, 11)
        self.banner_label = _label(
            "Capture is on. Highlighted text will appear below."
        )
        self.banner_label.setWordWrap(True)
        banner_row.addWidget(self.banner_label, 1)
        banner_row.addStretch(1)
        root.addWidget(self.banner)
        self.banner.hide()

        head = QHBoxLayout()
        self.field_label = _label("INPUT TEXT", "FieldLabel")
        head.addWidget(self.field_label)
        head.addStretch(1)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("GhostButton")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.clear_btn)
        root.addLayout(head)

        self.editor = QTextEdit()
        self.editor.setPlaceholderText("Type or paste text here...")
        self.editor.setAcceptRichText(False)
        root.addWidget(self.editor, 1)

        self.captured_view = QTextEdit()
        self.captured_view.setPlaceholderText(
            "Highlighted text will appear here."
        )
        self.captured_view.setAcceptRichText(False)
        root.addWidget(self.captured_view, 1)
        self.captured_view.hide()

        root.addWidget(self._transport_card())

        self.clear_btn.clicked.connect(self._clear_active)

    def _transport_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 18, 22, 20)
        lay.setSpacing(15)

        top = QHBoxLayout()
        top.setSpacing(14)

        self.src_voice_col = QWidget()
        self.src_voice_col.setObjectName("VoiceSlot")
        src_lay = QVBoxLayout(self.src_voice_col)
        src_lay.setContentsMargins(0, 0, 0, 0)
        src_lay.setSpacing(6)
        src_lay.addWidget(_label("ORIGINAL", "FieldLabel"))
        self.src_voice_box = QComboBox()
        self.src_voice_box.setMinimumWidth(120)
        self.src_voice_box.setCursor(Qt.PointingHandCursor)
        src_lay.addWidget(self.src_voice_box)
        self.src_voice_col.setMaximumWidth(260)
        top.addWidget(self.src_voice_col, 1)
        self.src_voice_col.hide()

        self.swap_btn = QPushButton()
        self.swap_btn.setObjectName("SwapButton")
        self.swap_btn.setIcon(svg_icon("swap", C["translate"], 18))
        self.swap_btn.setCursor(Qt.PointingHandCursor)
        self.swap_btn.setToolTip("Read me the other one")
        top.addWidget(self.swap_btn, 0, Qt.AlignBottom)
        self.swap_btn.hide()

        self.voice_col = QWidget()
        self.voice_col.setObjectName("VoiceSlot")
        voice_col = QVBoxLayout(self.voice_col)
        voice_col.setContentsMargins(0, 0, 0, 0)
        voice_col.setSpacing(6)
        self.voice_label = _label("VOICE", "FieldLabel")
        voice_col.addWidget(self.voice_label)
        self.voice_box = QComboBox()
        self.voice_box.setMinimumWidth(120)
        self.voice_box.setCursor(Qt.PointingHandCursor)
        voice_col.addWidget(self.voice_box)
        top.addWidget(self.voice_col, 1)

        top.addStretch(1)
        lay.addLayout(top)

        speed_row = QHBoxLayout()
        speed_row.setSpacing(10)
        speed_row.addWidget(_label("SPEED", "FieldLabel"))
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(50, 200)
        self.speed_slider.setValue(100)
        self.speed_slider.setMinimumWidth(80)
        self.speed_slider.setMaximumWidth(240)
        speed_row.addWidget(self.speed_slider)
        self.speed_value = _label("1.0x", "TimeLabel")
        speed_row.addWidget(self.speed_value)
        speed_row.addStretch(1)
        lay.addLayout(speed_row)

        scrub = QHBoxLayout()
        scrub.setSpacing(11)
        self.time_now = _label("0:00", "TimeLabel")
        self.scrubber = QSlider(Qt.Horizontal)
        self.scrubber.setRange(0, 1000)
        self.scrubber.setValue(0)
        self.time_total = _label("--:--", "TimeLabel")
        scrub.addWidget(self.time_now)
        scrub.addWidget(self.scrubber, 1)
        scrub.addWidget(self.time_total)
        lay.addLayout(scrub)

        self.spoken = _ElidedLabel("", Qt.ElideRight, "SpokenText")
        self.spoken.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.spoken)

        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.save_btn = QPushButton()
        self.save_btn.setObjectName("TransportButton")
        self.save_btn.setFlat(True)
        self.save_btn.setIcon(svg_icon("save", C["text_muted"], _ICON_SAVE))
        self.save_btn.setIconSize(QSize(_ICON_SAVE, _ICON_SAVE))
        self.save_btn.setToolTip("Save audio")
        self.save_btn.setAccessibleName("Save audio")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setEnabled(False)
        controls.addWidget(self.save_btn)

        controls.addStretch(1)
        self.back_btn = QPushButton()
        self.back_btn.setObjectName("TransportButton")
        self.back_btn.setFlat(True)
        self.back_btn.setIcon(svg_icon("back10", C["text"], _ICON_SKIP))
        self.back_btn.setIconSize(QSize(_ICON_SKIP, _ICON_SKIP))
        self.back_btn.setToolTip("Back 10 seconds")

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("TransportButton")
        self.play_btn.setFlat(True)
        self.play_btn.setIcon(svg_icon("play", C["text"], _ICON_PLAY, bg=C["sidebar"]))
        self.play_btn.setIconSize(QSize(_ICON_PLAY, _ICON_PLAY))
        self.play_btn.setToolTip("Play")

        self.fwd_btn = QPushButton()
        self.fwd_btn.setObjectName("TransportButton")
        self.fwd_btn.setFlat(True)
        self.fwd_btn.setIcon(svg_icon("fwd10", C["text"], _ICON_SKIP))
        self.fwd_btn.setIconSize(QSize(_ICON_SKIP, _ICON_SKIP))
        self.fwd_btn.setToolTip("Forward 10 seconds")

        for b in (self.back_btn, self.play_btn, self.fwd_btn):
            b.setCursor(Qt.PointingHandCursor)
        controls.addWidget(self.back_btn)
        controls.addSpacing(6)
        controls.addWidget(self.play_btn)
        controls.addSpacing(6)
        controls.addWidget(self.fwd_btn)
        controls.addStretch(1)

        self.autoplay = QCheckBox("Auto-speak")
        self.autoplay.setToolTip("Speak captured text immediately")
        self.autoplay.setCursor(Qt.PointingHandCursor)
        controls.addWidget(self.autoplay)

        lay.addLayout(controls)

        self.speed_slider.valueChanged.connect(
            lambda v: self.speed_value.setText(f"{v / 100:.1f}x")
        )
        return card

    def fit_voice_col(self) -> None:
        fm = QFontMetrics(self.voice_box.font())
        text_w = fm.horizontalAdvance(self.voice_box.currentText())
        self.voice_col.setMaximumWidth(max(text_w + 55, 120))

    def set_translate_mode(self, on: bool) -> None:
        self.voice_label.setText("TRANSLATION" if on else "VOICE")
        self.src_voice_col.setVisible(on)
        self.swap_btn.setVisible(on)
        if on:
            self._twinkle(self.src_voice_col)

    def _twinkle(self, widget: QWidget) -> None:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(420)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        widget._twinkle_anim = anim
        anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        anim.start()

    def _apply_engine_banner(self) -> None:
        w = self.engine_banner
        label = self.engine_banner_label
        if self._engine_problem:
            w.setObjectName("EngineBanner")
            label.setObjectName("EngineBannerText")
            label.setText(self._engine_problem)
            w.setVisible(True)
        elif self._engine_notice:
            w.setObjectName("EngineNotice")
            label.setObjectName("EngineNoticeText")
            label.setText(self._engine_notice)
            w.setVisible(True)
        else:
            label.setText("")
            w.setVisible(False)
        w.style().unpolish(w)
        w.style().polish(w)
        label.style().unpolish(label)
        label.style().polish(label)

    def set_engine_problem(self, text: str) -> None:
        self._engine_problem = text or ""
        self._apply_engine_banner()

    def set_engine_notice(self, text: str) -> None:
        self._engine_notice = text or ""
        self._apply_engine_banner()

    def set_capture_mode(self, capture: bool) -> None:
        self.clear_translation_pair()

        src = self.editor if capture else self.captured_view
        dst = self.captured_view if capture else self.editor
        carried = src.toPlainText().strip()
        if carried and not dst.toPlainText().strip():
            self._set_plain(dst, carried)

        self.editor.setVisible(not capture)
        self.captured_view.setVisible(capture)
        self.banner.setVisible(capture)
        self.field_label.setText("CAPTURED TEXT" if capture else "INPUT TEXT")

    @property
    def is_manual(self) -> bool:
        parent = self.editor.parentWidget()
        if parent is None:
            return self.editor.isVisible()
        return self.editor.isVisibleTo(parent)

    def active_view(self) -> QTextEdit:
        return self.editor if self.is_manual else self.captured_view

    def active_text(self) -> str:
        if self._pair_source:
            return self._pair_source.strip()
        return self.active_view().toPlainText().strip()

    @staticmethod
    def _set_plain(view: QTextEdit, text: str) -> None:
        view.clear()
        view.setCurrentCharFormat(QTextCharFormat())
        view.setPlainText(text)

    def show_translation_pair(
        self, original: str, translated: str, heading: str = ""
    ) -> None:
        view = self.active_view()
        self._pair_source = original
        self._pair_translated = translated
        self._pair_heading = heading
        view.setReadOnly(True)
        view.setHtml(_pair_html(original, translated, heading))
        view.moveCursor(QTextCursor.Start)
        view.ensureCursorVisible()

    def refresh_theme(self) -> None:
        if not self._pair_source:
            return
        self.show_translation_pair(
            self._pair_source, self._pair_translated, self._pair_heading
        )

    def clear_translation_pair(self) -> None:
        if not self._pair_source:
            return
        source = self._pair_source
        self._pair_source = ""
        self._pair_translated = ""
        self._pair_heading = ""
        for view in (self.editor, self.captured_view):
            if view.isReadOnly():
                view.setReadOnly(False)
                self._set_plain(view, source)

    def set_captured(self, text: str) -> None:
        self.clear_translation_pair()
        self._set_plain(self.captured_view, text)

    def set_active(self, text: str) -> None:
        self.clear_translation_pair()
        self._set_plain(self.active_view(), text)

    def _clear_active(self) -> None:
        self.clear_translation_pair()
        self._set_plain(self.active_view(), "")


class HistoryPage(QWidget):
    replay_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(34, 26, 34, 26)
        lay.setSpacing(14)
        head = QHBoxLayout()
        head.addWidget(_label("HISTORY", "FieldLabel"))
        head.addStretch(1)
        self.clear_btn = QPushButton("Clear history")
        self.clear_btn.setObjectName("GhostButton")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.clear_btn)
        lay.addLayout(head)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Date", "Time", "Words", "Preview"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        hdr.setHighlightSections(False)
        lay.addWidget(self.table, 1)
        self.table.cellDoubleClicked.connect(self._replay)

    def load(self, items: list[dict]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for entry in items:
            text = entry.get("text", "")
            preview = _clip(text, 80)
            words = len(text.split())
            stamp = entry.get("when", "")
            parts = stamp.split(" ", 1)
            date_str = parts[0] if parts else ""
            time_str = parts[1] if len(parts) > 1 else ""

            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(date_str))
            self.table.setItem(row, 1, QTableWidgetItem(time_str))
            word_item = QTableWidgetItem()
            word_item.setData(Qt.DisplayRole, words)
            self.table.setItem(row, 2, word_item)
            self.table.setItem(row, 3, QTableWidgetItem(preview))
            self.table.item(row, 0).setData(Qt.UserRole, text)
        self.table.setSortingEnabled(True)

    def _replay(self, row: int, _col: int) -> None:
        item = self.table.item(row, 0)
        if item:
            self.replay_requested.emit(item.data(Qt.UserRole))


_COL_TICK = 22
_COL_TYPE = 100
_COL_PITCH = 58
_COL_STAR = 22
_COL_RULE = 1
_ROW_MARGINS = (11, 6, 11, 6)
_ROW_SPACING = 10


def _column_rule(visible: bool) -> QWidget:
    rule = QFrame()
    rule.setFixedWidth(_COL_RULE)
    if visible:
        rule.setObjectName("ColRule")
        rule.setFrameShape(QFrame.VLine)
        rule.setFrameShadow(QFrame.Plain)
    else:
        rule.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    return rule


class _ColumnHeader(QWidget):

    clicked = Signal(str)

    def __init__(self, key: str, text: str, width: int = 0,
                 icon: str = "") -> None:
        super().__init__()
        self.key = key
        self.setObjectName("ColHeader")
        self.setProperty("active", "false")
        self.setCursor(Qt.PointingHandCursor)
        if width:
            self.setFixedWidth(width)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        self._icon_name = icon
        self.label = QLabel(text)
        self.label.setObjectName("ColHeaderText")
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.label)
        lay.addStretch(1)

        self.arrow = QLabel()
        self.arrow.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if icon:
            self.arrow.hide()
        else:
            self.arrow.setFixedWidth(11)
            lay.addWidget(self.arrow)
        self.set_state(False, False)

    def set_state(self, active: bool, descending: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        if self._icon_name:
            glyph = self._icon_name if (active and not descending) else "star"
            self.label.setPixmap(svg_icon(
                glyph, C["primary"] if active else C["text_muted"], 15,
            ).pixmap(15, 15))
            self.update()
            return
        if active:
            self.arrow.setPixmap(svg_icon(
                "chevron_down" if descending else "chevron_up",
                C["primary"], 11,
            ).pixmap(11, 11))
        else:
            self.arrow.clear()
        self.update()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.key)
        super().mousePressEvent(event)


class _VoiceRow(QWidget):

    favourited = Signal(str, bool)
    in_use_toggled = Signal(str, bool)
    picked = Signal(str)
    previewed = Signal(str)

    def __init__(self, voice_id: str, *, in_use: bool, favourite: bool) -> None:
        super().__init__()
        self.voice_id = voice_id
        self._in_use = bool(in_use)
        self._favourite = bool(favourite)
        self.setObjectName("VoiceRow")
        self.setProperty("selected", "false")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(*_ROW_MARGINS)
        lay.setSpacing(_ROW_SPACING)

        self.tick = QPushButton()
        self.tick.setObjectName("TickButton")
        self.tick.setFlat(True)
        self.tick.setFixedSize(_COL_TICK, _COL_TICK)
        self.tick.setCursor(Qt.PointingHandCursor)
        self.tick.clicked.connect(self._toggle_in_use)
        lay.addWidget(self.tick)

        self.name = QLabel(voicelib.base_label(voice_id))
        self.name.setObjectName("VoiceName")
        self.name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.name, 1)

        lay.addWidget(_column_rule(False))

        self.type_label = QLabel(
            "Feminine" if voicelib.gender(voice_id) == "f" else "Masculine"
        )
        self.type_label.setObjectName("VoiceDetail")
        self.type_label.setFixedWidth(_COL_TYPE)
        self.type_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.type_label)

        lay.addWidget(_column_rule(False))

        self.pitch_label = QLabel(voicelib.pitch_band(voice_id))
        self.pitch_label.setObjectName("VoiceDetail")
        self.pitch_label.setFixedWidth(_COL_PITCH)
        self.pitch_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.pitch_label)

        self.star = QPushButton()
        self.star.setObjectName("StarButton")
        self.star.setFlat(True)
        self.star.setFixedSize(_COL_STAR, _COL_STAR)
        self.star.setCursor(Qt.PointingHandCursor)
        self.star.clicked.connect(self._toggle_favourite)
        lay.addWidget(self.star)

        self._paint()


    def set_in_use(self, on: bool) -> None:
        self._in_use = bool(on)
        self._paint()

    def set_locked(self, reason: str) -> None:
        self.tick.setEnabled(not reason)
        self.tick.setToolTip(reason or self._tick_hint())

    def _tick_hint(self) -> str:
        return ("In the Reader dropdown" if self._in_use
                else "Not in the Reader dropdown")

    def _toggle_in_use(self) -> None:
        self._in_use = not self._in_use
        self._paint()
        self.picked.emit(self.voice_id)
        self.in_use_toggled.emit(self.voice_id, self._in_use)

    def _toggle_favourite(self) -> None:
        self._favourite = not self._favourite
        self._paint()
        self.picked.emit(self.voice_id)
        self.favourited.emit(self.voice_id, self._favourite)

    def _paint(self) -> None:
        self.tick.setIcon(svg_icon(
            "tick_on" if self._in_use else "tick_off",
            C["primary"] if self._in_use else C["text_muted"],
            18,
        ))
        self.star.setIcon(svg_icon(
            "star_full" if self._favourite else "star",
            C["primary"] if self._favourite else C["text_muted"],
            17,
        ))
        self.star.setToolTip("Starred" if self._favourite else "Star this voice")
        if self.tick.isEnabled():
            self.tick.setToolTip(self._tick_hint())

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def paintEvent(self, event) -> None:
        opt = QStyleOption()
        opt.initFrom(self)
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, opt, painter, self)

    def mousePressEvent(self, event) -> None:
        self.picked.emit(self.voice_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.previewed.emit(self.voice_id)
        super().mouseDoubleClickEvent(event)


class _LanguageSection(QWidget):

    toggled = Signal(str, bool)
    sort_changed = Signal(str)
    tick_all = Signal(str, bool)

    SORTS = ("Name", "Voice type", "Pitch")

    def __init__(self, code: str, count: int) -> None:
        super().__init__()
        self.code = code
        self._sort = "favourite"
        self._sort_desc = False
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.header = QPushButton()
        self.header.setObjectName("LangHeader")
        self.header.setCheckable(True)
        self.header.setCursor(Qt.PointingHandCursor)

        head_lay = QHBoxLayout(self.header)
        head_lay.setContentsMargins(12, 0, 14, 0)
        head_lay.setSpacing(9)
        self.arrow = QLabel()
        self.arrow.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        head_lay.addWidget(self.arrow)
        title = QLabel(translatelib.language_name(code))
        title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        head_lay.addWidget(title)
        head_lay.addStretch(1)
        self.count = QLabel("1 voice" if count == 1 else f"{count} voices")
        self.count.setObjectName("LangCount")
        self.count.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        head_lay.addWidget(self.count)
        lay.addWidget(self.header)

        self.body = QWidget()
        self.body.setObjectName("LangBody")
        body_lay = QVBoxLayout(self.body)
        body_lay.setContentsMargins(10, 8, 10, 10)
        body_lay.setSpacing(0)

        self.col_head = QWidget()
        self.col_head.setObjectName("VoiceHeader")
        col_lay = QHBoxLayout(self.col_head)
        col_lay.setContentsMargins(*_ROW_MARGINS)
        col_lay.setSpacing(_ROW_SPACING)

        self.all_box = QCheckBox()
        self.all_box.setObjectName("TickAllBox")
        self.all_box.setFixedWidth(_COL_TICK)
        self.all_box.setCursor(Qt.PointingHandCursor)
        self.all_box.setToolTip("Tick or untick every voice in this language")
        self.all_box.clicked.connect(
            lambda on: self.tick_all.emit(self.code, bool(on))
        )
        col_lay.addWidget(self.all_box)

        self._headers: dict[str, _ColumnHeader] = {}
        for key, text, width, icon in (
            ("name", "Name", 0, ""),
            ("type", "Voice type", _COL_TYPE, ""),
            ("pitch", "Pitch", _COL_PITCH, ""),
            ("favourite", "", _COL_STAR, "star_full"),
        ):
            if key in ("type", "pitch"):
                col_lay.addWidget(_column_rule(True))
            head = _ColumnHeader(key, text, width, icon)
            head.clicked.connect(self._on_header_clicked)
            self._headers[key] = head
            col_lay.addWidget(head, 0 if width else 1)
        self._headers["name"].setToolTip(
            "Click a heading to sort. Click it again to reverse."
        )
        self._headers["favourite"].setToolTip("Sort by starred")
        self._paint_headers()
        body_lay.addWidget(self.col_head)

        self.rows_holder = QWidget()
        self._rows_lay = QVBoxLayout(self.rows_holder)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(0)
        body_lay.addWidget(self.rows_holder)
        self.rows: list[_VoiceRow] = []

        lay.addWidget(self.body)
        self.body.setVisible(False)
        self._paint_arrow()

        self.header.toggled.connect(self._on_toggled)

    def sort_key(self) -> str:
        return self._sort

    def sort_descending(self) -> bool:
        return self._sort_desc

    def _on_header_clicked(self, key: str) -> None:
        if key == self._sort:
            self._sort_desc = not self._sort_desc
        else:
            self._sort = key
            self._sort_desc = False
        self._paint_headers()
        self.sort_changed.emit(self.code)

    def _paint_headers(self) -> None:
        for key, head in self._headers.items():
            head.set_state(key == self._sort, self._sort_desc)

    def set_all_box(self, all_ticked: bool) -> None:
        self.all_box.blockSignals(True)
        self.all_box.setChecked(all_ticked)
        self.all_box.blockSignals(False)

    def _on_toggled(self, on: bool) -> None:
        self.body.setVisible(on)
        self._paint_arrow()
        self.toggled.emit(self.code, on)

    def _paint_arrow(self) -> None:
        icon = "chevron_down" if self.header.isChecked() else "chevron_right"
        self.arrow.setPixmap(svg_icon(icon, C["text_muted"], 15).pixmap(15, 15))

    def clear_rows(self) -> None:
        while self._rows_lay.count():
            item = self._rows_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows = []

    def add_row(self, row: "_VoiceRow") -> None:
        self._rows_lay.addWidget(row)
        self.rows.append(row)


class VoicesPage(QWidget):
    preview_requested = Signal(str)
    voice_chosen = Signal(str)
    favourites_changed = Signal(list)
    in_use_changed = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self._ids: list[str] = []
        self._favourites: list[str] = []
        self._in_use: list[str] = []
        self._current = ""
        self._preferred = "en"
        self._expanded: set[str] = set()
        self._sections: dict[str, _LanguageSection] = {}
        self._rows: dict[str, _VoiceRow] = {}
        self._selected_id = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(34, 26, 34, 26)
        lay.setSpacing(12)

        lay.addWidget(_label("VOICES", "FieldLabel"))

        self.current_label = QLabel()
        self.current_label.setObjectName("CurrentVoice")
        self.current_label.setWordWrap(True)
        lay.addWidget(self.current_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self._sections_lay = QVBoxLayout(holder)
        self._sections_lay.setContentsMargins(0, 0, 6, 0)
        self._sections_lay.setSpacing(8)
        self._sections_lay.addStretch(1)
        self.scroll.setWidget(holder)
        lay.addWidget(self.scroll, 1)

        self.explainer = _label(
            "Each voice speaks its own language. Whatever you highlight is"
            " translated into that language before it is read aloud."
            "  (Highlight Chinese with an English voice and you hear English."
            " Highlight English with a Chinese voice and you hear Chinese.)",
            "VoiceExplainer",
        )
        self.explainer.setWordWrap(True)
        lay.addWidget(self.explainer)

        row = QHBoxLayout()
        self.tick_hint = _label(
            "Double-click a voice to hear it. Ticked voices appear in the"
            " Reader; starred ones stay on top.",
            "PathLabel",
        )
        self.tick_hint.setWordWrap(True)
        row.addWidget(self.tick_hint, 1)
        self.preview_btn = QPushButton("Preview")
        self.preview_btn.setObjectName("GhostButton")
        self.use_btn = QPushButton("Use this voice")
        self.use_btn.setObjectName("PrimaryButton")
        for b in (self.preview_btn, self.use_btn):
            b.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.preview_btn)
        row.addWidget(self.use_btn)
        lay.addLayout(row)

        self.preview_btn.clicked.connect(self._preview)
        self.use_btn.clicked.connect(self._use)


    def load(self, ordered_ids: list[str], favourites: list[str],
             in_use: list[str], current: str, preferred: str = "en") -> None:
        self._ids = list(ordered_ids)
        self._favourites = [v for v in favourites if v in self._ids]
        self._in_use = [v for v in in_use if v in self._ids]
        self._current = current
        self._preferred = preferred
        self._build()

    def set_current(self, voice_id: str) -> None:
        self._current = voice_id
        if voice_id and voice_id not in self._in_use:
            self._in_use.append(voice_id)
        self._paint_current()
        self._refresh_locks()

    def set_in_use(self, voice_id: str, on: bool) -> None:
        if on and voice_id not in self._in_use:
            self._in_use.append(voice_id)
        elif not on and not self._lock_reason(voice_id):
            self._in_use = [v for v in self._in_use if v != voice_id]
        if row := self._rows.get(voice_id):
            row.set_in_use(voice_id in self._in_use)
        self._refresh_locks()

    def select(self, voice_id: str) -> None:
        code = translatelib.voice_language(voice_id)
        section = self._sections.get(code)
        if section is not None and not section.header.isChecked():
            section.header.setChecked(True)
        self._select(voice_id)

    def in_use(self) -> list[str]:
        return [v for v in self._ids if v in set(self._in_use)]

    def favourites(self) -> list[str]:
        return [v for v in self._ids if v in set(self._favourites)]


    def _build(self) -> None:
        while self._sections_lay.count() > 1:
            item = self._sections_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._sections.clear()
        self._rows.clear()

        groups = voicelib.by_language(self._ids)
        for code in voicelib.languages_ordered(self._ids, self._preferred):
            section = _LanguageSection(code, len(groups.get(code, [])))
            section.toggled.connect(self._on_section_toggled)
            section.sort_changed.connect(self._fill)
            section.tick_all.connect(self._on_tick_all)
            self._sections_lay.insertWidget(self._sections_lay.count() - 1, section)
            self._sections[code] = section
            self._sync_all_box(code)
            if code in self._expanded:
                section.header.setChecked(True)

        self._paint_current()

    def _on_section_toggled(self, code: str, on: bool) -> None:
        if on:
            self._expanded.add(code)
            self._fill(code)
        else:
            self._expanded.discard(code)
            self._sections[code].clear_rows()
            for voice_id in list(self._rows):
                if translatelib.voice_language(voice_id) == code:
                    self._rows.pop(voice_id, None)

    def _sorted_ids(self, code: str) -> list[str]:
        ids = list(voicelib.by_language(self._ids).get(code, []))
        section = self._sections[code]
        sort = section.sort_key()
        favourites = set(self._favourites)
        if sort == "type":
            ids.sort(key=lambda v: (0 if voicelib.gender(v) == "f" else 1,
                                    voicelib.base_label(v)))
        elif sort == "pitch":
            ids.sort(key=voicelib.pitch_sort_key)
        elif sort == "favourite":
            ids.sort(key=lambda v: (v not in favourites,
                                    voicelib.base_label(v)))
        else:
            ids.sort(key=voicelib.base_label)
        if section.sort_descending():
            ids.reverse()
        return ids

    def _fill(self, code: str) -> None:
        section = self._sections.get(code)
        if section is None or not section.header.isChecked():
            return
        keep = self._selected_id
        section.clear_rows()
        for voice_id in list(self._rows):
            if translatelib.voice_language(voice_id) == code:
                self._rows.pop(voice_id, None)
        in_use = set(self._in_use)
        favourites = set(self._favourites)
        for voice_id in self._sorted_ids(code):
            row = _VoiceRow(
                voice_id,
                in_use=voice_id in in_use,
                favourite=voice_id in favourites,
            )
            row.picked.connect(self._select)
            row.previewed.connect(self.preview_requested)
            row.favourited.connect(self._on_favourited)
            row.in_use_toggled.connect(self._on_in_use_toggled)
            section.add_row(row)
            self._rows[voice_id] = row
        self._refresh_locks()
        self._sync_all_box(code)
        if keep:
            self._select(keep)


    def _lock_reason(self, voice_id: str) -> str:
        if voice_id == self._current:
            return "This is the voice you are using. Pick another voice first."
        if len(self._in_use) <= 1 and voice_id in self._in_use:
            return "At least one voice has to stay ticked."
        return ""

    def _refresh_locks(self) -> None:
        for voice_id, row in self._rows.items():
            row.set_locked(self._lock_reason(voice_id))

    def _sync_all_box(self, code: str) -> None:
        section = self._sections.get(code)
        if section is None:
            return
        ids = voicelib.by_language(self._ids).get(code, [])
        in_use = set(self._in_use)
        section.set_all_box(bool(ids) and all(v in in_use for v in ids))

    def _sync_all_boxes(self) -> None:
        for code in self._sections:
            self._sync_all_box(code)

    def _on_in_use_toggled(self, voice_id: str, on: bool) -> None:
        if on:
            if voice_id not in self._in_use:
                self._in_use.append(voice_id)
        else:
            if self._lock_reason(voice_id):
                self._rows[voice_id].set_in_use(True)
                return
            self._in_use = [v for v in self._in_use if v != voice_id]
        self._refresh_locks()
        self._sync_all_boxes()
        self.in_use_changed.emit(self.in_use())

    def _on_tick_all(self, code: str, on: bool) -> None:
        for voice_id in voicelib.by_language(self._ids).get(code, []):
            if on:
                if voice_id not in self._in_use:
                    self._in_use.append(voice_id)
            elif not self._lock_reason(voice_id):
                self._in_use = [v for v in self._in_use if v != voice_id]
        for voice_id, row in self._rows.items():
            row.set_in_use(voice_id in set(self._in_use))
        self._refresh_locks()
        self._sync_all_boxes()
        self.in_use_changed.emit(self.in_use())

    def _on_favourited(self, voice_id: str, on: bool) -> None:
        if on:
            if voice_id not in self._favourites:
                self._favourites.append(voice_id)
        else:
            self._favourites = [v for v in self._favourites if v != voice_id]
        self.favourites_changed.emit(self.favourites())


    def _select(self, voice_id: str) -> None:
        self._selected_id = voice_id
        for row in self._rows.values():
            row.set_selected(row.voice_id == voice_id)

    def _selected(self) -> str:
        if self._selected_id in self._rows:
            return self._selected_id
        return ""

    def _paint_current(self) -> None:
        if self._current:
            self.current_label.setText(
                f"Currently using: {voicelib.label(self._current)}"
            )
        else:
            self.current_label.setText("No voice selected yet.")

    def _preview(self) -> None:
        if voice := self._selected():
            self.preview_requested.emit(voice)

    def _use(self) -> None:
        if voice := self._selected():
            self.voice_chosen.emit(voice)


def _engine_state_text(ident: engine_identity.EngineIdentity) -> str:
    if ident.origin == "launched" and ident.trusted:
        text = "started by Backtalk"
    elif ident.origin in ("launched", "adopted"):
        pid = ident.pid if ident.pid is not None else "unknown"
        started = ident.started or "an unknown time"
        text = f"already running, PID {pid} since {started}"
    elif ident.origin == "unknown":
        text = "not identified"
    else:
        text = "no engine running"
    if ident.voices is not None:
        text += f" - {ident.voices} voices"
    return text


class SettingsPage(QWidget):
    theme_changed = Signal(str)
    path_changed = Signal(str, str)

    def __init__(self, settings: dict) -> None:
        super().__init__()
        self._hwinfo_done = False
        self._gpu_fell_back = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(34, 26, 34, 26)
        lay.setSpacing(16)
        lay.addWidget(_label("SETTINGS", "FieldLabel"))

        autostart_row, self.autostart = _wrapping_check(
            "Start voice engine automatically")
        self.autostart.setChecked(bool(settings.get("autostart_kokoro", True)))
        lay.addLayout(autostart_row)

        lang_row = QHBoxLayout()
        lang_row.addWidget(_label("Preferred language"))
        self.language_combo = QComboBox()
        for code in translatelib.SPEAKABLE:
            self.language_combo.addItem(translatelib.language_name(code), code)
        current_lang = settings.get("preferred_language", "en")
        idx = self.language_combo.findData(current_lang)
        self.language_combo.setCurrentIndex(max(0, idx))
        self.language_combo.setMaximumWidth(140)
        lang_row.addWidget(self.language_combo)
        lang_row.addStretch(1)
        lay.addLayout(lang_row)
        lang_hint = _label(
            "Sorts this language's voices to the top. Does not change what"
            " Backtalk speaks.",
            "PathLabel",
        )
        lang_hint.setWordWrap(True)
        lay.addWidget(lang_hint)

        keep_row, self.keep_original = _wrapping_check("Read as written")
        self.keep_original.setChecked(
            bool(settings.get("keep_original_languages", False))
        )
        lay.addLayout(keep_row)
        keep_hint = _label(
            "Foreign text keeps its own language and a matching voice."
            " Translate overrides this.",
            "PathLabel",
        )
        keep_hint.setWordWrap(True)
        lay.addWidget(keep_hint)

        latin_row, self.switch_latin = _wrapping_check(
            "Switch voices for French, Spanish, Italian, Portuguese")
        self.switch_latin.setChecked(
            bool(settings.get("switch_latin_voices", False))
        )
        lay.addLayout(latin_row)
        latin_hint = _label(
            "Needs a run of 40 characters or more and a voice for that"
            " language. Shorter runs keep your current voice.",
            "PathLabel",
        )
        latin_hint.setWordWrap(True)
        lay.addWidget(latin_hint)

        tx_row, self.show_translation = _wrapping_check("Show translation")
        self.show_translation.setChecked(
            bool(settings.get("show_translation", True))
        )
        lay.addLayout(tx_row)
        tx_hint = _label(
            "Shows the original and the translation together. Off, you only"
            " see what is spoken.",
            "PathLabel",
        )
        tx_hint.setWordWrap(True)
        lay.addWidget(tx_hint)

        theme_row = QHBoxLayout()
        theme_row.addWidget(_label("Theme"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        current_theme = settings.get("theme", "light")
        self.theme_combo.setCurrentIndex(1 if current_theme == "dark" else 0)
        self.theme_combo.setMaximumWidth(140)
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch(1)
        lay.addLayout(theme_row)

        vol_row = QHBoxLayout()
        vol_row.addWidget(_label("Volume"))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(float(settings.get("volume", 1.0)) * 100))
        self.volume.setMaximumWidth(280)
        self.volume_value = _label(f"{self.volume.value()}%", "TimeLabel")
        vol_row.addWidget(self.volume)
        vol_row.addWidget(self.volume_value)
        vol_row.addStretch(1)
        lay.addLayout(vol_row)
        self.volume.valueChanged.connect(
            lambda v: self.volume_value.setText(f"{v}%")
        )

        speed_row = QHBoxLayout()
        speed_row.addWidget(_label("Default speed"))
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(50, 200)
        self.speed.setValue(int(float(settings.get("speed", 1.0)) * 100))
        self.speed.setMaximumWidth(280)
        self.speed_value = _label(f"{self.speed.value() / 100:.1f}x", "TimeLabel")
        speed_row.addWidget(self.speed)
        speed_row.addWidget(self.speed_value)
        speed_row.addStretch(1)
        lay.addLayout(speed_row)
        self.speed.valueChanged.connect(
            lambda v: self.speed_value.setText(f"{v / 100:.1f}x")
        )

        lay.addSpacing(4)
        perf_header = _label("PERFORMANCE", "FieldLabel")
        perf_header.setWordWrap(True)
        lay.addWidget(perf_header)

        dev_row = QHBoxLayout()
        dev_row.setSpacing(8)
        dev_label = _label("Compute device")
        dev_label.setWordWrap(True)
        dev_row.addWidget(dev_label)
        self.device_combo = QComboBox()
        self.device_combo.addItem("Automatic", "auto")
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.addItem("GPU (none detected)", "gpu")
        current_device = str(settings.get("compute_device", "auto"))
        dev_idx = self.device_combo.findData(current_device)
        self.device_combo.setCurrentIndex(max(0, dev_idx))
        self.device_combo.setToolTip(
            "Where the voice engine does its work."
            " Automatic lets the engine choose."
        )
        dev_row.addWidget(self.device_combo, 1)
        lay.addLayout(dev_row)

        thr_row = QHBoxLayout()
        thr_row.setSpacing(8)
        thr_label = _label("CPU threads")
        thr_label.setWordWrap(True)
        thr_row.addWidget(thr_label)
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(0, 128)
        self.threads_spin.setSpecialValueText("Automatic")
        self.threads_spin.setValue(int(settings.get("cpu_threads", 0) or 0))
        self.threads_spin.setToolTip(
            "0 lets the engine pick its own thread count."
        )
        thr_row.addWidget(self.threads_spin, 1)
        lay.addLayout(thr_row)

        perf_hint = _label("Changes apply the next time Backtalk starts.")
        perf_hint.setWordWrap(True)
        lay.addWidget(perf_hint)

        self._gpu_fallback_hint = _label("")
        self._gpu_fallback_hint.setObjectName("GpuFallbackHint")
        self._gpu_fallback_hint.setWordWrap(True)
        self._gpu_fallback_hint.hide()
        lay.addWidget(self._gpu_fallback_hint)
        self.device_combo.currentIndexChanged.connect(
            lambda _: self._refresh_gpu_fallback_hint()
        )

        refresh_row = QHBoxLayout()
        refresh_row.setSpacing(8)
        refresh_label = _label("Engine auto-refresh")
        refresh_label.setWordWrap(True)
        refresh_row.addWidget(refresh_label)
        self.refresh_combo = QComboBox()
        self.refresh_combo.addItem("Never", "never")
        self.refresh_combo.addItem("5 minutes", "5m")
        self.refresh_combo.addItem("30 minutes", "30m")
        self.refresh_combo.addItem("2 hours", "2h")
        self.refresh_combo.addItem("5 hours", "5h")
        current_refresh = str(settings.get("engine_refresh", "never"))
        ref_idx = self.refresh_combo.findData(current_refresh)
        self.refresh_combo.setCurrentIndex(max(0, ref_idx))
        refresh_row.addWidget(self.refresh_combo, 1)
        lay.addLayout(refresh_row)
        refresh_hint = _label(
            "This will unload the engine to free up memory."
        )
        refresh_hint.setObjectName("PathLabel")
        refresh_hint.setWordWrap(True)
        lay.addWidget(refresh_hint)

        lay.addSpacing(8)
        lay.addWidget(_label("VOICE ENGINE", "FieldLabel"))

        engine_root_text = str(config.KOKORO_ROOT)
        engine_python_text = (
            str(config.KOKORO_PYTHON) if config.KOKORO_PYTHON else "not found"
        )
        self._engine_labels: dict[str, QLabel] = {}
        for key, row_label, value_text, tooltip_text in (
            ("engine", "Engine:", engine_root_text,
             f"{engine_root_text} (source: "
             f"{getattr(config, 'KOKORO_ROOT_SOURCE', 'unknown')})"),
            ("interpreter", "Interpreter:", engine_python_text,
             f"{engine_python_text} (source: "
             f"{getattr(config, 'KOKORO_PYTHON_SOURCE', 'unknown')})"),
            ("version", "Version:", "unknown", "unknown"),
            ("state", "State:", "not started yet", "not started yet"),
        ):
            row = QHBoxLayout()
            row.addWidget(_label(row_label))
            val = _ElidedLabel(value_text, Qt.ElideMiddle, "PathLabel")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            val.setToolTip(tooltip_text)
            self._engine_labels[key] = val
            row.addWidget(val, 1)
            lay.addLayout(row)

        lay.addSpacing(8)
        lay.addWidget(_label("LOCATIONS", "FieldLabel"))

        app_row = QHBoxLayout()
        app_row.addWidget(_label("Application:"))
        app_val = _ElidedLabel(str(config.ROOT), Qt.ElideMiddle, "PathLabel")
        app_val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        app_val.setToolTip(str(config.ROOT))
        app_row.addWidget(app_val, 1)
        lay.addLayout(app_row)

        self._path_labels: dict[str, QLabel] = {}
        for key, display_name, current_path in (
            ("audio_out_path", "Audio outputs", config.AUDIO_OUT),
            ("data_path", "Settings", config.DATA),
        ):
            row = QHBoxLayout()
            row.addWidget(_label(f"{display_name}:"))
            val = _ElidedLabel(
                str(current_path), Qt.ElideMiddle, "PathLabel")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            val.setToolTip(str(current_path))
            self._path_labels[key] = val
            row.addWidget(val, 1)
            browse = QPushButton("Browse")
            browse.setObjectName("GhostButton")
            browse.setCursor(Qt.PointingHandCursor)
            browse.setMaximumWidth(80)
            browse.clicked.connect(
                lambda _checked=False, k=key, n=display_name: self._browse(k, n)
            )
            row.addWidget(browse)
            lay.addLayout(row)

        lay.addStretch(1)

        self.theme_combo.currentIndexChanged.connect(
            lambda _: self.theme_changed.emit(self.theme_combo.currentData())
        )

    def _browse(self, key: str, display_name: str) -> None:
        current = self._path_labels[key].text()
        folder = QFileDialog.getExistingDirectory(
            self, f"Choose {display_name} folder", current
        )
        if folder:
            self._path_labels[key].setText(folder)
            self.path_changed.emit(key, folder)

    def _refresh_gpu_fallback_hint(self) -> None:
        device = self.device_combo.currentData()
        if device == "gpu" and self._gpu_fell_back:
            self._gpu_fallback_hint.setText(
                "GPU was chosen, but this voice engine can only use the "
                "CPU for now. It is running on the CPU."
            )
            self._gpu_fallback_hint.show()
        else:
            self._gpu_fallback_hint.setText("")
            self._gpu_fallback_hint.hide()

    def set_gpu_fallback(self, fell_back: bool) -> None:
        self._gpu_fell_back = fell_back
        self._refresh_gpu_fallback_hint()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._hwinfo_done:
            self._hwinfo_done = True
            info = hwinfo.detect()
            gpus = info.get("gpus", [])
            gpu_display = gpus[0] if gpus else "none detected"
            self.device_combo.setItemText(2, f"GPU ({gpu_display})")
            cores = info.get("cores")
            if cores and cores >= self.threads_spin.value():
                self.threads_spin.setMaximum(cores)
        self._refresh_gpu_fallback_hint()

    def set_engine_identity(self, ident: engine_identity.EngineIdentity) -> None:
        root_text = ident.root or "an unknown location"
        python_text = ident.python or "not found"

        engine_label = self._engine_labels["engine"]
        engine_label.setText(root_text)
        engine_label.setToolTip(f"{root_text} (source: {ident.root_source})")

        interpreter_label = self._engine_labels["interpreter"]
        interpreter_label.setText(python_text)
        interpreter_label.setToolTip(
            f"{python_text} (source: {ident.python_source})"
        )

        version_text = ident.version or "unknown"
        version_label = self._engine_labels["version"]
        version_label.setText(version_text)
        version_label.setToolTip(version_text)

        state_text = _engine_state_text(ident)
        state_label = self._engine_labels["state"]
        state_label.setText(state_text)
        state_label.setToolTip(state_text)


class CompactWidget(QWidget):

    MIN_WIDTH = 280
    MAX_WIDTH = 600
    GRIP_WIDTH = 8
    SPOKEN_MIN_HEIGHT = 32
    EXPAND_PULL = 60

    play_clicked = Signal()
    back_clicked = Signal()
    fwd_clicked = Signal()
    speed_changed = Signal(int)
    volume_changed = Signal(int)
    voice_changed = Signal(str)
    autoplay_changed = Signal(bool)
    capture_changed = Signal(bool)
    show_main = Signal()
    expand_requested = Signal()
    closed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(
            parent,
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setWindowTitle("Backtalk")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumWidth(self.MAX_WIDTH)
        self._drag_offset = None
        self._resize_origin = None
        self._width_restored = False
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QWidget()
        card.setObjectName("CompactCard")
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 14, 20, 18)
        lay.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.status_logo = WaveLogo(size=22, stroke=2.0)
        head.addWidget(self.status_logo)
        head.addStretch(1)
        self.capture_btn = QPushButton("  Capture")
        self.capture_btn.setObjectName("GhostButton")
        self.capture_btn.setCheckable(True)
        self.capture_btn.setIcon(svg_icon("highlight", C["text_muted"], 14))
        self.capture_btn.setCursor(Qt.PointingHandCursor)
        self.capture_btn.toggled.connect(self._on_capture_toggled)
        head.addWidget(self.capture_btn)
        self.main_btn = QPushButton()
        self.main_btn.setObjectName("CompactButton")
        self.main_btn.setIcon(svg_icon("expand", C["text_muted"], 14))
        self.main_btn.setToolTip("Show main window")
        self.main_btn.setCursor(Qt.PointingHandCursor)
        self.main_btn.clicked.connect(self.show_main)
        head.addWidget(self.main_btn)
        self.min_btn = QPushButton()
        self.min_btn.setObjectName("CompactButton")
        self.min_btn.setIcon(svg_icon("minimise", C["text_muted"], 14))
        self.min_btn.setToolTip("Minimise")
        self.min_btn.setCursor(Qt.PointingHandCursor)
        self.min_btn.clicked.connect(self.showMinimized)
        head.addWidget(self.min_btn)
        self.close_btn = QPushButton()
        self.close_btn.setObjectName("CompactButton")
        self.close_btn.setIcon(svg_icon("clear", C["text_muted"], 13))
        self.close_btn.setToolTip("Close")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        head.addWidget(self.close_btn)
        lay.addLayout(head)

        self.spoken = _label("", "SpokenText")
        self.spoken.setWordWrap(True)
        self.spoken.setAlignment(Qt.AlignCenter)
        self.spoken.setMinimumHeight(self.SPOKEN_MIN_HEIGHT)
        lay.addWidget(self.spoken)

        play_row = QHBoxLayout()
        play_row.setSpacing(10)
        play_row.addStretch(1)
        self.back_btn = QPushButton()
        self.back_btn.setObjectName("TransportButton")
        self.back_btn.setIcon(svg_icon("back10", C["text"], 20))
        self.back_btn.setToolTip("Back 10 seconds")
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.clicked.connect(self.back_clicked)
        play_row.addWidget(self.back_btn)

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("CompactPlay")
        self.play_btn.setIcon(svg_icon("play", C["bg"], 19))
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.clicked.connect(self.play_clicked)
        play_row.addWidget(self.play_btn)

        self.fwd_btn = QPushButton()
        self.fwd_btn.setObjectName("TransportButton")
        self.fwd_btn.setIcon(svg_icon("fwd10", C["text"], 20))
        self.fwd_btn.setToolTip("Forward 10 seconds")
        self.fwd_btn.setCursor(Qt.PointingHandCursor)
        self.fwd_btn.clicked.connect(self.fwd_clicked)
        play_row.addWidget(self.fwd_btn)
        play_row.addStretch(1)
        lay.addLayout(play_row)

        self.voice_box = QComboBox()
        self.voice_box.setCursor(Qt.PointingHandCursor)
        self.voice_box.currentIndexChanged.connect(self._emit_voice)
        lay.addWidget(self.voice_box)

        speed_row = QHBoxLayout()
        speed_row.setSpacing(6)
        speed_row.addWidget(_label("Speed", "FieldLabel"))
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(50, 200)
        self.speed_slider.setValue(100)
        self.speed_slider.valueChanged.connect(self.speed_changed)
        speed_row.addWidget(self.speed_slider, 1)
        self.speed_value = _label("1.0x", "TimeLabel")
        self.speed_slider.valueChanged.connect(
            lambda v: self.speed_value.setText(f"{v / 100:.1f}x")
        )
        speed_row.addWidget(self.speed_value)
        lay.addLayout(speed_row)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(6)
        vol_row.addWidget(_label("Vol", "FieldLabel"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.valueChanged.connect(self.volume_changed)
        vol_row.addWidget(self.volume_slider, 1)
        self.volume_value = _label("100%", "TimeLabel")
        self.volume_slider.valueChanged.connect(
            lambda v: self.volume_value.setText(f"{v}%")
        )
        vol_row.addWidget(self.volume_value)
        lay.addLayout(vol_row)

        auto_row = QHBoxLayout()
        auto_row.addStretch(1)
        self.autoplay = QCheckBox("Auto-speak on capture")
        self.autoplay.toggled.connect(self.autoplay_changed)
        auto_row.addWidget(self.autoplay)
        auto_row.addStretch(1)
        lay.addLayout(auto_row)

        self.grip = QWidget(self)
        self.grip_left = QWidget(self)
        self.grip_bottom = QWidget(self)
        for grip, cursor in ((self.grip, Qt.SizeHorCursor),
                             (self.grip_left, Qt.SizeHorCursor),
                             (self.grip_bottom, Qt.SizeVerCursor)):
            grip.setObjectName("CompactResizeGrip")
            grip.setStyleSheet(
                "#CompactResizeGrip { background: transparent; }"
            )
            grip.setCursor(cursor)
            grip.installEventFilter(self)


    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._width_restored:
            self._width_restored = True
            self._apply_width(self._saved_width())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        inset = 16
        side_height = max(self.height() - inset * 2, 1)
        self.grip.setGeometry(
            self.width() - self.GRIP_WIDTH, inset,
            self.GRIP_WIDTH, side_height,
        )
        self.grip_left.setGeometry(0, inset, self.GRIP_WIDTH, side_height)
        self.grip_bottom.setGeometry(
            inset, self.height() - self.GRIP_WIDTH,
            max(self.width() - inset * 2, 1), self.GRIP_WIDTH,
        )
        for grip in (self.grip, self.grip_left, self.grip_bottom):
            grip.raise_()

    def eventFilter(self, watched, event) -> bool:
        from PySide6.QtCore import QEvent

        if watched not in (self.grip, self.grip_left, self.grip_bottom):
            return super().eventFilter(watched, event)
        kind = event.type()
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            point = event.globalPosition().toPoint()
            self._resize_origin = (watched, point.x(), point.y(),
                                   self.width(), self.x())
            return True
        if kind == QEvent.MouseMove and self._resize_origin is not None:
            edge, start_x, start_y, start_width, start_left = self._resize_origin
            point = event.globalPosition().toPoint()

            if edge is self.grip_bottom:
                if point.y() - start_y > self.EXPAND_PULL:
                    self._resize_origin = None
                    self.expand_requested.emit()
                return True

            moved = point.x() - start_x
            wanted = start_width + (moved if edge is self.grip else -moved)
            if wanted - self.MAX_WIDTH > self.EXPAND_PULL:
                self._resize_origin = None
                self.expand_requested.emit()
                return True
            self._apply_width(wanted)
            if edge is self.grip_left:
                self.move(start_left + (start_width - self.width()), self.y())
            return True
        if kind == QEvent.MouseButtonRelease and self._resize_origin is not None:
            self._resize_origin = None
            self._save_width()
            return True
        return super().eventFilter(watched, event)

    def _apply_width(self, width: int) -> None:
        width = max(self.MIN_WIDTH, min(self.MAX_WIDTH, int(width)))
        self.resize(width, self.height())
        self._fit_height()

    def _fit_height(self) -> None:
        layout = self.layout()
        if layout is None:
            return
        layout.activate()
        label = self.spoken
        if label.width() > 0:
            label.setMinimumHeight(max(
                self.SPOKEN_MIN_HEIGHT,
                label.heightForWidth(label.width()),
            ))
            layout.activate()
        height = max(self.sizeHint().height(),
                     self.minimumSizeHint().height())
        if height != self.height():
            self.resize(self.width(), height)

    def _settings(self) -> dict | None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, MainWindow):
                return widget.settings
        return None

    def _saved_width(self) -> int:
        settings = self._settings()
        if settings is None:
            settings = config.load_settings()
        fallback = config.DEFAULTS["compact_width"]
        try:
            width = int(settings.get("compact_width", fallback))
        except (TypeError, ValueError):
            width = fallback
        return max(self.MIN_WIDTH, min(self.MAX_WIDTH, width))

    def _save_width(self) -> None:
        settings = self._settings()
        if settings is None:
            settings = config.load_settings()
        settings["compact_width"] = self.width()
        config.save_settings(settings)


    def sync_state(self, playing: bool, speed: int, volume: int,
                   autoplay: bool, spoken: str) -> None:
        self.set_playing(playing)
        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(speed)
        self.speed_slider.blockSignals(False)
        self.speed_value.setText(f"{speed / 100:.1f}x")
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(volume)
        self.volume_slider.blockSignals(False)
        self.volume_value.setText(f"{volume}%")
        self.autoplay.blockSignals(True)
        self.autoplay.setChecked(autoplay)
        self.autoplay.blockSignals(False)
        self.spoken.setText(spoken)

    def set_spoken(self, text: str) -> None:
        self.spoken.setText(text)

    def set_playing(self, playing: bool) -> None:
        self.play_btn.setIcon(
            svg_icon("pause" if playing else "play", C["bg"], 19)
        )

    def set_status(self, state: str) -> None:
        self.status_logo.set_state(state)

    def refresh_icons(self, playing: bool) -> None:
        self.set_playing(playing)
        self.back_btn.setIcon(svg_icon("back10", C["text"], 20))
        self.fwd_btn.setIcon(svg_icon("fwd10", C["text"], 20))
        self.main_btn.setIcon(svg_icon("expand", C["text_muted"], 14))
        self.min_btn.setIcon(svg_icon("minimise", C["text_muted"], 14))
        self.close_btn.setIcon(svg_icon("clear", C["text_muted"], 13))
        self._paint_capture(self.capture_btn.isChecked())
        self.status_logo.refresh_theme()

    def _on_capture_toggled(self, armed: bool) -> None:
        self._paint_capture(armed)
        self.capture_changed.emit(armed)

    def _paint_capture(self, armed: bool) -> None:
        self.capture_btn.setIcon(svg_icon(
            "highlight", C["bg"] if armed else C["text_muted"], 14))
        self.capture_btn.setText("  Capture")
        self.capture_btn.setToolTip(
            "Reading highlighted text is ON" if armed
            else "Turn on to read text you highlight anywhere")

    def set_capture(self, armed: bool) -> None:
        self.capture_btn.blockSignals(True)
        self.capture_btn.setChecked(armed)
        self.capture_btn.blockSignals(False)
        self._paint_capture(armed)

    def _emit_voice(self, index: int) -> None:
        voice_id = self.voice_box.itemData(index) if index >= 0 else None
        if voice_id:
            self.voice_changed.emit(voice_id)

    def load_voices(self, items: list[tuple[str, str]], current_index: int) -> None:
        self.voice_box.blockSignals(True)
        self.voice_box.clear()
        for voice_id, label in items:
            self.voice_box.addItem(label, voice_id)
        self.voice_box.setCurrentIndex(current_index)
        self.voice_box.blockSignals(False)
        _fit_popup(self.voice_box)

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)


_ENGINE_DOWN_MSG = (
    "The voice engine stopped. Press the refresh button "
    "to start it again."
)
_ENGINE_RESTARTING_MSG = "Starting the voice engine"
_ENGINE_RESTART_FAIL_MSG = (
    "The voice engine did not start. The file engine.log in the data folder says why."
)


class MainWindow(QWidget):
    _ENGINE_DOWN_MSG = _ENGINE_DOWN_MSG
    _ENGINE_RESTARTING_MSG = _ENGINE_RESTARTING_MSG
    _ENGINE_RESTART_FAIL_MSG = _ENGINE_RESTART_FAIL_MSG
    NARROW_WIDTH = 760
    COMPACT_WIDTH = 470

    def __init__(self) -> None:
        super().__init__()
        self._was_narrow = False
        self._restore_size: QSize | None = None
        self.settings = config.load_settings()
        self.history = config.load_history()
        self.client = KokoroClient()
        self.client._settings = self.settings
        self._engine_down = False
        self._engine_deaths = 0
        self.player = Player(self.client)
        self.capture = SelectionCapture(self)
        self._scrubbing = False
        self._server_ready = False
        self._pending_preview: str | None = None
        self._sidebar_visible = True
        self._compact: CompactWidget | None = None
        self._warmup: _ServerWarmup | None = None
        self._refresh_worker: _RefreshWorker | None = None
        self._restart_worker: _ServerWarmup | None = None
        self._warm_done = False
        self._pending_arm = False
        self._tx_worker: _TranslateWorker | None = None
        self._tx_warmup: _TranslateWarmup | None = None
        self._tx_warmed: set[str] = set()
        self._tx_threads: list[QThread] = []
        self._source_text: str = ""
        self._voice_ids: list[str] = []
        self._server_voice_count: int = 0
        self._tx_generation = 0
        self._tx_source_lang: str | None = None
        self._speaking_original = False
        self._translation_used = False
        self._reported_recovery = False
        self._reported_dir_fallback = False
        self._closing = False

        config.set_theme(self.settings.get("theme", "light"))

        self.setWindowTitle(config.APP_NAME)
        self.setWindowIcon(QIcon(str(config.ASSETS / "backtalk.ico")))
        self.resize(1180, 830)
        self.setMinimumSize(380, 420)

        self._build()
        self._wire()
        self._apply_settings()
        self._restore_geometry()
        self._warm_up()

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._sidebar_widget = self._sidebar()
        outer.addWidget(self._sidebar_widget)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._topbar())

        self.stack = QStackedWidget()
        self.reader = ReaderPage()
        self.history_page = HistoryPage()
        self.voices_page = VoicesPage()
        self.settings_page = SettingsPage(self.settings)
        for page in (self.reader, self.history_page, self.voices_page,
                     self.settings_page):
            self.stack.addWidget(_scrollable(page))
        right.addWidget(self.stack, 1)
        outer.addLayout(right, 1)

    def _sidebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(238)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(20, 26, 20, 20)
        lay.setSpacing(6)

        lay.addWidget(_label(config.APP_NAME, "AppName"))
        lay.addSpacing(26)

        self.nav_group = QButtonGroup(self)
        for index, (key, text) in enumerate((
            ("reader", "Reader"),
            ("history", "History"),
            ("voices", "Voices"),
            ("settings", "Settings"),
        )):
            btn = QPushButton(f"  {text}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIcon(svg_icon(key, C["text_muted"], 19))
            self.nav_group.addButton(btn, index)
            lay.addWidget(btn)
        self.nav_group.button(0).setChecked(True)

        lay.addSpacing(10)
        self.translate_btn = TranslateToggle("  Translate")
        self.translate_btn.setObjectName("TranslateToggle")
        self.translate_btn.setCheckable(True)
        self.translate_btn.setCursor(Qt.PointingHandCursor)
        self.translate_btn.setIcon(svg_icon("translate", C["text_muted"], 18))
        self.translate_btn.setToolTip(
            "Choose a voice for the original and a voice for the translation"
        )
        lay.addWidget(self.translate_btn)

        lay.addStretch(1)

        self.widget_btn = QPushButton("  Compact view")
        self.widget_btn.setObjectName("SidebarToggle")
        self.widget_btn.setCheckable(True)
        self.widget_btn.setCursor(Qt.PointingHandCursor)
        self.widget_btn.setIcon(svg_icon("reader", C["text_muted"], 17))
        lay.addWidget(self.widget_btn)
        lay.addSpacing(8)

        self.server_hint = _label("", "SpokenText")
        self.server_hint.setWordWrap(True)
        lay.addWidget(self.server_hint)
        return bar

    def _topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(62)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(34, 0, 34, 0)
        lay.setSpacing(13)

        self.sidebar_toggle = QPushButton()
        self.sidebar_toggle.setObjectName("TransportButton")
        self.sidebar_toggle.setToolTip("Toggle sidebar")
        self.sidebar_toggle.setCursor(Qt.PointingHandCursor)
        self.sidebar_toggle.setFixedSize(36, 36)
        self.sidebar_toggle.setIcon(svg_icon("menu", C["text"], 20))
        lay.addWidget(self.sidebar_toggle)

        self.capture_btn = QPushButton("  Capture")
        self.capture_btn.setObjectName("GhostButton")
        self.capture_btn.setCheckable(True)
        self.capture_btn.setCursor(Qt.PointingHandCursor)
        self.capture_btn.setIcon(svg_icon("highlight", C["text_muted"], 17))
        lay.addWidget(self.capture_btn)

        self.refresh_btn = QPushButton()
        self.refresh_btn.setObjectName("TransportButton")
        self.refresh_btn.setToolTip("Refresh capture and voice engine")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setFixedSize(36, 36)
        self.refresh_btn.setIcon(svg_icon("refresh", C["text_muted"], 18))
        lay.addWidget(self.refresh_btn)

        lay.addStretch(1)

        self.status_text = _label("", "TimeLabel")
        self.status_text.setAlignment(Qt.AlignVCenter)
        self.status_text.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        lay.addWidget(self.status_text, 0, Qt.AlignVCenter)

        self.status_logo = WaveLogo(size=30, stroke=1.8)
        lay.addWidget(self.status_logo, 0, Qt.AlignVCenter)

        return bar

    def _flash_status(self, text: str, colour: str, duration_ms: int = 2400) -> None:
        self.status_text.setText(text)
        self.status_text.setStyleSheet(
            f"color: {colour}; font-size: 12px; font-weight: 600;"
            " background: transparent;"
        )
        self._status_gen = getattr(self, "_status_gen", 0) + 1
        gen = self._status_gen
        QTimer.singleShot(duration_ms, lambda: self._clear_status(gen))

    def _clear_status(self, gen: int) -> None:
        if gen == getattr(self, "_status_gen", None):
            self.status_text.setText("")

    def _refresh_status(self) -> None:
        if not self._server_ready:
            state = "offline" if self._warm_done else "speaking"
        elif self.player.state in ("playing", "synthesizing"):
            state = "speaking"
        elif self.reader.active_text().strip():
            state = "ready"
        else:
            state = "idle"
        self.status_logo.set_state(state)
        if self._compact is not None:
            self._compact.set_status(state)

    def _wire(self) -> None:
        self.nav_group.idClicked.connect(self.stack.setCurrentIndex)

        r = self.reader
        r.play_btn.clicked.connect(self._on_play)
        r.back_btn.clicked.connect(lambda: self.player.skip(-10))
        r.fwd_btn.clicked.connect(lambda: self.player.skip(10))
        r.save_btn.clicked.connect(self._on_save)
        r.scrubber.sliderPressed.connect(lambda: setattr(self, "_scrubbing", True))
        r.scrubber.sliderReleased.connect(self._on_scrub_release)
        r.voice_box.currentIndexChanged.connect(self._on_voice_changed)
        r.voice_box.currentIndexChanged.connect(r.fit_voice_col)
        r.speed_slider.valueChanged.connect(self._on_speed_changed)

        self._speed_debounce = QTimer(self)
        self._speed_debounce.setSingleShot(True)
        self._speed_debounce.setInterval(220)
        self._speed_debounce.timeout.connect(self._apply_speed)

        self._settings_debounce = QTimer(self)
        self._settings_debounce.setSingleShot(True)
        self._settings_debounce.setInterval(600)
        self._settings_debounce.timeout.connect(self._flush_settings)

        self.capture_btn.toggled.connect(self._on_capture_toggled)
        self.capture.captured.connect(self._on_captured)

        self.sidebar_toggle.clicked.connect(self._toggle_sidebar)
        self.widget_btn.clicked.connect(self._toggle_compact)
        self.refresh_btn.clicked.connect(self._on_refresh)
        self.client.engine_died.connect(self._on_engine_died)
        self.client.engine_reloading.connect(self._on_engine_reloading)
        self.client.engine_reloaded.connect(self._on_engine_reloaded)

        self.player.position_changed.connect(self._on_position)
        self.player.state_changed.connect(self._on_state)
        self.player.chunk_spoken.connect(self._on_spoken)
        self.player.error.connect(self._on_error)
        self.player.finished.connect(lambda: self._on_spoken(""))

        self._idle_refresh_timer: QTimer | None = None
        self._idle_phase = "off"
        self._idle_countdown = 0
        self._idle_refresh_interval_ms = 0
        self._apply_idle_refresh_setting()

        self.history_page.replay_requested.connect(self._on_replay)
        self.history_page.clear_btn.clicked.connect(self._on_clear_history)
        self.voices_page.preview_requested.connect(self._on_preview_voice)
        self.voices_page.voice_chosen.connect(self._on_voice_chosen)
        self.voices_page.favourites_changed.connect(self._on_favourites_changed)
        self.voices_page.in_use_changed.connect(self._on_in_use_changed)
        self.settings_page.volume.valueChanged.connect(self._on_volume)
        self.settings_page.speed.valueChanged.connect(self._on_default_speed)
        self.settings_page.autostart.toggled.connect(
            lambda v: self._store("autostart_kokoro", v)
        )
        self.settings_page.theme_changed.connect(self._on_theme_changed)
        self.settings_page.device_combo.currentIndexChanged.connect(
            self._on_compute_device
        )
        self.settings_page.threads_spin.valueChanged.connect(
            self._on_cpu_threads
        )
        self.settings_page.refresh_combo.currentIndexChanged.connect(
            self._on_engine_refresh
        )
        self.settings_page.path_changed.connect(self._on_path_changed)
        self.settings_page.language_combo.currentIndexChanged.connect(
            self._on_preferred_language
        )
        self.settings_page.keep_original.toggled.connect(
            lambda on: self._store("keep_original_languages", bool(on))
        )
        self.settings_page.switch_latin.toggled.connect(
            lambda on: self._store("switch_latin_voices", bool(on))
        )
        self.settings_page.show_translation.toggled.connect(
            self._on_show_translation_toggled
        )
        self.translate_btn.toggled.connect(self._on_translate_toggled)
        self.reader.src_voice_box.currentIndexChanged.connect(
            self._on_src_voice_changed
        )
        self.reader.swap_btn.clicked.connect(self._on_swap)
        self.reader.autoplay.toggled.connect(
            lambda v: self._store("autoplay", v)
        )
        self.reader.editor.textChanged.connect(self._refresh_status)
        self.reader.captured_view.textChanged.connect(self._refresh_status)

    def _apply_settings(self) -> None:
        self.reader.speed_slider.setValue(
            int(float(self.settings.get("speed", 1.0)) * 100)
        )
        self.player.set_volume(float(self.settings.get("volume", 1.0)))
        self.history_page.load(self.history)
        self._pending_arm = bool(self.settings.get("capture_armed", False))
        self._migrate_voice_prefs()
        self.reader.autoplay.setChecked(
            bool(self.settings.get("autoplay", False))
        )
        if self.settings.get("translate_mode", False):
            self.translate_btn.setChecked(True)

    def _store(self, key: str, value) -> None:
        self.settings[key] = value
        config.save_settings(self.settings)

    def _store_soon(self, key: str, value) -> None:
        self.settings[key] = value
        self._settings_debounce.start()

    def _flush_settings(self) -> None:
        if self._closing:
            return
        config.save_settings(self.settings)

    def _warm_up(self) -> None:
        if not self.settings.get("autostart_kokoro", True):
            self._warm_done = True
            hint = "Autostart is off. Start the voice engine manually, then reopen."
            self.server_hint.setText(hint)
            self.reader.set_engine_problem(hint)
            ident = self.client.identify()
            self.client.log_identity(ident)
            self.settings_page.set_engine_identity(ident)
            self._refresh_status()
            return
        problem = self.client.engine_problem()
        if problem is not None:
            self._warm_done = True
            self.server_hint.setText(problem)
            self.reader.set_engine_problem(problem)
            self.client.launch_state = "refused"
            ident = self.client.identify()
            self.client.log_identity(ident)
            self.settings_page.set_engine_identity(ident)
            self._refresh_status()
            return
        self.server_hint.setText(
            f"First launch takes about {config.COLD_START_SECONDS} seconds."
        )
        self._refresh_status()
        self._warmup = _ServerWarmup(self.client)
        self._warmup.identified.connect(self._on_engine_identified)
        self._warmup.ready.connect(self._on_server_ready)

    def _on_server_ready(self, ok: bool) -> None:
        self._server_ready = ok
        self._warm_done = True
        if self.client.gpu_fallback:
            msg = ("GPU was chosen, but this voice engine can only use the "
                   "CPU for now. It is running on the CPU.")
            self.server_hint.setText(msg)
            self.reader.set_engine_notice(msg)
            self.settings_page.set_gpu_fallback(True)
        if ok:
            if not self.client.gpu_fallback:
                self.server_hint.setText("")
            self.reader.set_engine_problem("")
            self._load_voices()
            self._on_engine_identified(
                self.client.identify(
                    voice_count=self._server_voice_count or len(self._voice_ids)
                )
            )
            if self.settings.get("startup_phrase", True):
                from .phrases import random_phrase
                phrase = random_phrase()
                self.reader.set_active(phrase)
                greeter = (self._voice
                           if translatelib.can_pronounce(self._voice, phrase)
                           else voicelib.default_voice(self._voice_ids, "en"))
                if greeter:
                    self.player.load(phrase, greeter, self._speed)
            if self.settings.get("translate_mode", False):
                self._start_translate_warmup()
        else:
            timed_out = ("Voice engine did not respond. Restart the application "
                         f"to try again. Details are in {config.DATA / 'engine.log'}")
            self.server_hint.setText(timed_out)
            self.reader.set_engine_problem(timed_out)
        self._refresh_status()

    def _on_engine_died(self, code: int) -> None:
        self._engine_deaths += 1
        if self._engine_deaths < 2:
            return
        self._engine_down = True
        msg = _ENGINE_DOWN_MSG
        self.status_text.setText(msg)
        self.status_text.setStyleSheet(
            f"color: {C['danger']}; font-weight: bold;")
        self.server_hint.setText(msg)
        self.reader.set_engine_problem(msg)
        self._refresh_status()

    def _on_engine_identified(
        self, ident: engine_identity.EngineIdentity
    ) -> None:
        try:
            self.client.log_identity(ident)
            self.settings_page.set_engine_identity(ident)
            self.reader.set_engine_notice(engine_identity.notice(ident) or "")
        except Exception:
            pass

    def _start_translate_warmup(self, voice: str | None = None) -> None:
        target = translatelib.voice_language(voice or self._voice)
        if not self._server_ready or target in self._tx_warmed:
            return
        self._tx_warmed.add(target)
        self._tx_warmup = _TranslateWarmup(target)
        self._hold_thread(self._tx_warmup)
        self._tx_warmup.start()

    def _load_voices(self) -> None:
        found = self.client.voices()
        if not found:
            return
        self._server_voice_count = len(found)
        ordered = voicelib.ordered(
            found, self.settings.get("preferred_language", "en")
        )
        self._voice_ids = list(ordered)

        current = self.settings.get("voice", "am_adam")
        if voicelib.is_older(current):
            replacement = voicelib.modern_equivalent(current, found)
            if replacement:
                current = replacement
                self._store("voice", current)

        in_use = self._resolve_in_use(ordered, current)
        in_use = self._offer_new_languages(ordered, in_use)
        favourites = [v for v in self.settings.get("favourites", [])
                      if v in ordered]

        self.voices_page.load(
            ordered, favourites, in_use, current,
            self.settings.get("preferred_language", "en"),
        )
        self._fill_voice_box(in_use, favourites, current)

    def _resolve_in_use(self, ordered: list[str], current: str) -> list[str]:
        saved = self.settings.get("in_use")
        chosen = (voicelib.default_in_use(ordered) if saved is None
                  else [v for v in saved if v in ordered])
        if not chosen:
            chosen = voicelib.default_in_use(ordered)
        if current in ordered and current not in chosen:
            chosen = chosen + [current]
        keep = set(chosen)
        return [v for v in ordered if v in keep]

    def _offer_new_languages(self, ordered: list[str],
                             in_use: list[str]) -> list[str]:
        catalogue = set(voicelib.by_language(ordered))
        offered = self.settings.get("languages_offered")
        unrecorded = offered is None
        if unrecorded:
            offered = [translatelib.voice_language(v) for v in in_use]
        known = set(offered)

        added = [v for v in (voicelib.default_voice(ordered, code)
                             for code in voicelib.languages_ordered(ordered)
                             if code not in known) if v]
        if added:
            keep = set(in_use) | set(added)
            in_use = [v for v in ordered if v in keep]
            self._store("in_use", in_use)
        if unrecorded or not known >= catalogue:
            self._store("languages_offered", sorted(known | catalogue))
        return in_use

    def _fill_voice_box(self, in_use: list[str], favourites: list[str],
                        current: str) -> None:
        box = self.reader.voice_box
        box.blockSignals(True)
        box.clear()
        fav = set(favourites)
        position = {v: i for i, v in enumerate(in_use)}
        for voice_id in sorted(in_use,
                               key=lambda v: (0 if v in fav else 1, position[v])):
            box.addItem(voicelib.label(voice_id), voice_id)
        index = box.findData(current)
        box.setCurrentIndex(max(0, index))
        box.blockSignals(False)
        _fit_popup(box)
        self.reader.fit_voice_col()
        self._sync_compact_voices()

    def _sync_compact_voices(self) -> None:
        if self._compact is None or not self._compact.isVisible():
            return
        box = self.reader.voice_box
        items = [(box.itemData(i), box.itemText(i)) for i in range(box.count())]
        self._compact.load_voices(items, box.currentIndex())

    def _refill_voice_box(self) -> None:
        if not self._voice_ids:
            return
        self._fill_voice_box(
            self.voices_page.in_use(),
            self.voices_page.favourites(),
            self.reader.voice_box.currentData() or self.settings.get(
                "voice", "am_adam"),
        )

    @property
    def _voice(self) -> str:
        return self.reader.voice_box.currentData() or "am_adam"

    @property
    def _speed(self) -> float:
        return self.reader.speed_slider.value() / 100.0

    def _on_play(self) -> None:
        if self.player.state in ("playing", "paused"):
            self.player.toggle()
            return
        if not self._server_ready:
            QMessageBox.information(
                self, "Not ready",
                "The voice engine is still loading. Wait for the status to show Ready."
            )
            return
        text = self.reader.active_text()
        if not text:
            return
        self._speak(text)


    def _hold_thread(self, thread: QThread) -> None:
        _sweep_live_threads()
        _LIVE_THREADS.append(thread)
        self._tx_threads.append(thread)
        thread.finished.connect(self._drop_finished_threads)

    def _drop_finished_threads(self) -> None:
        self._tx_threads = [t for t in self._tx_threads if not t.isFinished()]
        _sweep_live_threads()

    def _release_translate_threads(self) -> None:
        for thread in list(self._tx_threads):
            if thread.isRunning():
                thread.wait(2000)
        self._tx_threads.clear()
        _sweep_live_threads()

    def _speak(self, text: str, record: bool = True) -> None:
        if not text.strip():
            return
        self._speaking_original = False
        self._source_text = text
        target = translatelib.voice_language(self._voice)

        if not translatelib.translator.needs_translation(text, target):
            self._play_text(text, record)
            return

        if (self.settings.get("keep_original_languages", False)
                and not self.settings.get("translate_mode", False)):
            self._speak_mixed(text, target, record)
            return

        source = translatelib.translator.detect(text)

        self._tx_generation += 1
        generation = self._tx_generation
        self._translation_used = True
        self._flash_status(
            f"Translating {translatelib.language_name(source)}...", C["text_muted"]
        )
        self.translate_btn.set_working(True)
        worker = _TranslateWorker(text, target, source)
        worker.done.connect(
            lambda result, g=generation, r=record: self._on_translated(result, g, r)
        )
        self._tx_worker = worker
        self._hold_thread(worker)
        worker.start()

    def _on_translated(self, result, generation: int, record: bool = True) -> None:
        if generation != self._tx_generation:
            return
        self.translate_btn.set_working(False)
        self._speaking_original = False
        changed = result.text.strip() != self._source_text.strip()
        if result.translated and changed:
            self._tx_source_lang = result.source
            if self.settings.get("show_translation", True):
                self.reader.show_translation_pair(
                    self._source_text, result.text, result.status
                )
            else:
                self.reader.clear_translation_pair()
                self.reader._set_plain(self.reader.active_view(), result.text)
            self._flash_status(result.status, C["ok"])
        else:
            self._flash_status(result.status, C["text_muted"])
        self._play_text(result.text, record)


    def _on_translate_toggled(self, on: bool) -> None:
        self._store("translate_mode", bool(on))
        self.reader.set_translate_mode(bool(on))
        self.translate_btn.setIcon(
            svg_icon("translate",
                     C["translate_on"] if on else C["text_muted"], 18)
        )
        if on:
            self._fill_src_voices()
            self._start_translate_warmup()
        else:
            self.translate_btn.set_working(False)

    def _fill_src_voices(self) -> None:
        box = self.reader.src_voice_box
        wanted = self.settings.get("source_voice") or self._suggested_source_voice()
        box.blockSignals(True)
        box.clear()
        for voice_id in voicelib.ordered(self._voice_ids or voicelib.shipped_ids()):
            box.addItem(voicelib.label(voice_id), voice_id)
        index = box.findData(wanted)
        if index >= 0:
            box.setCurrentIndex(index)
        box.blockSignals(False)

    def _suggested_source_voice(self) -> str | None:
        available = self._voice_ids or list(voicelib.shipped_ids())
        for lang in (self._tx_source_lang,
                     self.settings.get("preferred_language", "en")):
            if lang and (voice := voicelib.default_voice(available, lang)):
                return voice
        return available[0] if available else None

    def _on_src_voice_changed(self, _index: int) -> None:
        voice = self.reader.src_voice_box.currentData()
        if not voice:
            return
        self._store("source_voice", voice)
        self.voices_page.set_in_use(voice, True)
        self._store("in_use", self.voices_page.in_use())

    def _on_swap(self) -> None:
        if not self._server_ready:
            return
        original = self.reader.active_text()
        if not original.strip():
            return
        self._speaking_original = not self._speaking_original
        if self._speaking_original:
            voice = self.reader.src_voice_box.currentData()
            if not voice:
                self._flash_status("No voice for the original", C["text_muted"])
                self._speaking_original = False
                return
            if not translatelib.can_pronounce(voice, original):
                self._flash_status(
                    "That voice may not read the original well", C["working"]
                )
            self.player.reset()
            self._tx_generation += 1
            if self.player.load(original, voice, self._speed):
                self._flash_status("Reading the original", C["translate"])
        else:
            self.player.reset()
            self._speak(original, record=False)

    def _on_show_translation_toggled(self, on: bool) -> None:
        self._store("show_translation", bool(on))
        if on:
            return
        self.reader.clear_translation_pair()

    def _speak_mixed(self, text: str, target: str, record: bool) -> None:
        available = list(self._voice_ids or [self._voice])
        self._tx_generation += 1
        generation = self._tx_generation
        self._translation_used = True
        self.translate_btn.set_working(True)
        switch_latin = bool(self.settings.get("switch_latin_voices", False))
        worker = _VoicePlanWorker(text, target, self._voice, available, switch_latin)
        worker.done.connect(
            lambda plan, skipped, g=generation, t=text, r=record:
            self._on_voice_plan(plan, skipped, g, t, r)
        )
        self._tx_worker = worker
        self._hold_thread(worker)
        worker.start()

    def _on_voice_plan(self, plan: list, skipped: list, generation: int,
                       text: str, record: bool) -> None:
        if generation != self._tx_generation:
            return
        self.translate_btn.set_working(False)
        if skipped:
            self._flash_status(
                f"No voice installed for {skipped[0]}", C["danger"]
            )
        if not plan:
            self._play_text(text, record)
            return

        langs = {translatelib.voice_language(v) for _t, v in plan}
        if len(langs) > 1:
            names = ", ".join(sorted(translatelib.language_name(l) for l in langs))
            self._flash_status(f"Speaking {names}", C["ok"])
        if not self._server_ready:
            return
        if self.player.load_mixed(plan, self._speed) and record:
            self._record_history(text)

    def _speakable_voice(self, text: str) -> str:
        if translatelib.can_pronounce(self._voice, text):
            return self._voice
        lang = translatelib.translator.detect(text)
        other = (voicelib.default_voice(self._voice_ids or [self._voice], lang)
                 if lang else None)
        if other and translatelib.can_pronounce(other, text):
            self._flash_status(
                f"Read in {translatelib.language_name(lang)}", C["translate"]
            )
            return other
        return self._voice

    def _play_text(self, text: str, record: bool = True) -> None:
        if not self._server_ready or not text.strip():
            return
        voice = self._speakable_voice(text)
        if self.player.load(text, voice, self._speed) and record:
            self._record_history(text)

    def _on_scrub_release(self) -> None:
        self._scrubbing = False
        total = self.player.duration()
        if total > 0:
            self.player.seek(self.reader.scrubber.value() / 1000.0 * total)

    def _on_position(self, played: float, total: float) -> None:
        self.reader.time_now.setText(_mmss(played))
        self.reader.time_total.setText(_mmss(total) if total > 0 else "--:--")
        if not self._scrubbing and total > 0:
            self.reader.scrubber.setValue(int(played / total * 1000))
        self.reader.save_btn.setEnabled(total > 0)

    def _on_state(self, state: str) -> None:
        playing = state == "playing"
        self.reader.play_btn.setIcon(
            svg_icon("pause" if playing else "play", C["text"], _ICON_PLAY,
                     bg=C["sidebar"])
        )
        self.reader.play_btn.setToolTip("Pause" if playing else "Play")
        if self._compact and self._compact.isVisible():
            self._compact.set_playing(playing)
        self._reset_idle_refresh()
        self._refresh_status()

    def _on_spoken(self, text: str) -> None:
        one_line = _clip(text, 110)
        self.reader.spoken.setText(one_line)
        if self._compact and self._compact.isVisible():
            self._compact.set_spoken(one_line)

    def _on_error(self, message: str) -> None:
        self._flash_status(
            _clip(f"Voice engine error: {message}", 110), C["danger"], duration_ms=6000
        )
        self._refresh_status()

    def _on_voice_changed(self, _index: int) -> None:
        voice = self.reader.voice_box.currentData()
        if not voice:
            return
        self._store("voice", voice)
        self.voices_page.set_current(voice)
        if self._server_ready and self._translation_used:
            self._start_translate_warmup(voice)

        active = self.player.state in ("playing", "paused", "synthesizing")
        source = self._source_text or self.reader.active_text()
        if active and source:
            self.player.reset()
            self._speak(source, record=False)
        elif active:
            self.player.reset()

    def _on_speed_changed(self, value: int) -> None:
        self._store_soon("speed", value / 100.0)
        self._speed_debounce.start()

    def _apply_speed(self) -> None:
        self.player.set_speed(self._speed)

    def _on_volume(self, value: int) -> None:
        self.player.set_volume(value / 100.0)
        self._store_soon("volume", value / 100.0)

    def _on_default_speed(self, value: int) -> None:
        self._store_soon("speed", value / 100.0)
        self.reader.speed_slider.setValue(value)

    def _on_preferred_language(self) -> None:
        code = self.settings_page.language_combo.currentData() or "en"
        self._store("preferred_language", code)
        self._load_voices()
        if self._compact is not None and self._compact.isVisible():
            box = self.reader.voice_box
            items = [(box.itemData(i), box.itemText(i)) for i in range(box.count())]
            self._compact.load_voices(items, box.currentIndex())

    def _on_theme_changed(self, theme: str) -> None:
        self._store("theme", theme)
        config.set_theme(theme)
        QApplication.instance().setStyleSheet(stylesheet())
        self._refresh_icons()
        self.reader.refresh_theme()

    def _on_compute_device(self, index: int) -> None:
        data = self.settings_page.device_combo.currentData() or "auto"
        self._store("compute_device", data)

    def _on_cpu_threads(self, n: int) -> None:
        self._store("cpu_threads", n)

    def _on_engine_refresh(self, index: int) -> None:
        data = self.settings_page.refresh_combo.currentData() or "never"
        self._store("engine_refresh", data)
        self._apply_idle_refresh_setting()

    def _apply_idle_refresh_setting(self) -> None:
        if self._idle_refresh_timer is not None:
            self._idle_refresh_timer.stop()
            self._idle_refresh_timer.deleteLater()
            self._idle_refresh_timer = None
        self._idle_phase = "off"
        self._idle_countdown = 0
        self._idle_refresh_interval_ms = 0

        data = str(self.settings.get("engine_refresh", "never"))
        minutes = _ENGINE_REFRESH_MINUTES.get(data)
        if minutes is None:
            return

        self._idle_refresh_interval_ms = minutes * 60_000
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._on_idle_refresh_tick)
        self._idle_refresh_timer = timer
        self._arm_idle_refresh()

    def _arm_idle_refresh(self) -> None:
        if self._idle_refresh_timer is None or not self._idle_refresh_interval_ms:
            return
        self._idle_countdown = 0
        if self._idle_refresh_interval_ms > _IDLE_WARN_FIRST_MS:
            self._idle_phase = "warn5"
            first = self._idle_refresh_interval_ms - _IDLE_WARN_FIRST_MS
        else:
            self._idle_phase = "warn1"
            first = self._idle_refresh_interval_ms - _IDLE_WARN_LAST_MS
        self._idle_refresh_timer.start(first)

    def _engine_busy(self) -> bool:
        return (self.player.state in ("playing", "synthesizing")
                or self.player.generating)

    def _on_idle_refresh_tick(self) -> None:
        timer = self._idle_refresh_timer
        if timer is None:
            return
        if self._engine_busy() or self.client.unloaded:
            self._arm_idle_refresh()
            return

        if self._idle_phase == "warn5":
            self._flash_status("Engine will auto refresh in 5 minutes",
                               C["primary"], duration_ms=4_000)
            self._idle_phase = "warn1"
            timer.start(_IDLE_WARN_FIRST_MS - _IDLE_WARN_LAST_MS)
            return

        if self._idle_phase == "warn1":
            self._flash_status("Engine will auto refresh in 1 minute",
                               C["primary"], duration_ms=4_000)
            self._idle_phase = "countdown"
            self._idle_countdown = _IDLE_WARN_LAST_MS // 1000
            timer.start(1_000)
            return

        self._idle_countdown -= 1
        if self._idle_countdown > 0:
            if self._idle_countdown <= 56:
                self._flash_status(f"{self._idle_countdown}s",
                                   C["text_muted"], duration_ms=1_200)
            timer.start(1_000)
            return

        self._idle_phase = "off"
        if self.client.unload():
            self._flash_status("Engine refreshed", C["ok"], duration_ms=3_000)
        else:
            self._arm_idle_refresh()

    def _reset_idle_refresh(self) -> None:
        if not self._idle_refresh_interval_ms:
            return
        self._arm_idle_refresh()

    def _on_engine_reloading(self) -> None:
        if self._closing:
            return
        self._flash_status("Starting voice engine", C["text_muted"],
                           duration_ms=60_000)

    def _on_engine_reloaded(self, ok: bool) -> None:
        if self._closing:
            return
        if ok:
            self._flash_status("Voice engine started", C["ok"])
            self._on_engine_identified(self.client.identify())
            self._reset_idle_refresh()
        else:
            self._flash_status(_ENGINE_RESTART_FAIL_MSG, C["danger"],
                               duration_ms=6_000)

    def _refresh_icons(self) -> None:
        for index, key in enumerate(("reader", "history", "voices", "settings")):
            btn = self.nav_group.button(index)
            if btn is not None:
                btn.setIcon(svg_icon(key, C["text_muted"], 19))
        self.widget_btn.setIcon(svg_icon("reader", C["text_muted"], 17))
        self.sidebar_toggle.setIcon(svg_icon("menu", C["text"], 20))
        self.capture_btn.setIcon(svg_icon(
            "highlight",
            C["bg"] if self.capture_btn.isChecked() else C["text_muted"],
            17,
        ))
        self.refresh_btn.setIcon(svg_icon("refresh", C["text_muted"], 18))
        self.translate_btn.setIcon(svg_icon(
            "translate",
            C["translate_on"] if self.translate_btn.isChecked()
            else C["text_muted"],
            18,
        ))
        r = self.reader
        r.swap_btn.setIcon(svg_icon("swap", C["translate"], 18))
        r.back_btn.setIcon(svg_icon("back10", C["text"], _ICON_SKIP))
        r.fwd_btn.setIcon(svg_icon("fwd10", C["text"], _ICON_SKIP))
        r.save_btn.setIcon(svg_icon("save", C["text_muted"], _ICON_SAVE))
        r.play_btn.setIcon(svg_icon(
            "pause" if self.player.state == "playing" else "play",
            C["text"], _ICON_PLAY, bg=C["sidebar"],
        ))
        self.status_logo.refresh_theme()
        if self._compact is not None:
            self._compact.setStyleSheet(self.styleSheet())
            self._compact.refresh_icons(self.player.state == "playing")

    _DATA_FILES = ("history.json", "phrase_history.json", "voice_notes.json",
                   "settings.json")

    def _on_path_changed(self, key: str, folder: str) -> None:
        p = Path(folder)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._flash_status("Could not use that folder", C["danger"])
            return
        if key == "audio_out_path":
            config.AUDIO_OUT = p
        elif key == "data_path":
            old = config.DATA
            if old.resolve() != p.resolve():
                copied = []
                for name in self._DATA_FILES:
                    src, dst = old / name, p / name
                    if src.exists() and not dst.exists():
                        try:
                            shutil.copy2(src, dst)
                            copied.append(dst)
                        except OSError:
                            for f in copied + [dst]:
                                try:
                                    f.unlink()
                                except OSError:
                                    pass
                            self._flash_status(
                                "Could not copy your data there. Storage was not moved.",
                                C["danger"], 5000)
                            return
            config.DATA = p
        self._store(key, folder)

    def _on_refresh(self) -> None:
        if self._refresh_worker is not None and self._refresh_worker.isRunning():
            return
        if self._restart_worker is not None and self._restart_worker.isRunning():
            return
        import logging
        log = logging.getLogger("backtalk.capture")
        log.debug("REFRESH: start, armed=%s busy=%s listener=%s",
                  self.capture.armed, self.capture._busy,
                  self.capture._listener.running if self.capture._listener else None)
        self.capture._busy = False
        self.player.reset()
        self._tx_generation += 1
        if self.capture.armed:
            self.capture.set_armed(False)
            self.capture.set_armed(True)
            self.capture.register_window(int(self.winId()))
            if self._compact and self._compact.isVisible():
                self.capture.register_window(int(self._compact.winId()))
        self._flash_status("Checking engine", C["text_muted"])
        worker = _RefreshWorker(self.client)
        self._refresh_worker = worker
        _sweep_live_threads()
        _LIVE_THREADS.append(worker)
        worker.checked.connect(self._on_refresh_finished)
        worker.start()

    def _on_refresh_finished(self, ok: bool) -> None:
        self._refresh_worker = None
        _sweep_live_threads()
        if self._closing:
            return
        import logging
        log = logging.getLogger("backtalk.capture")
        self._server_ready = ok
        self._refresh_status()
        log.debug("REFRESH: done, health=%s listener=%s",
                  ok, self.capture._listener.running if self.capture._listener else None)
        if ok:
            self._flash_status("Refreshed", C["ok"])
            if self._engine_down:
                self._engine_down = False
                self.status_text.setText("")
                self.status_text.setStyleSheet("")
                self.server_hint.setText("")
                self.reader.set_engine_problem("")
                self.reader.set_engine_notice("")
        elif self._engine_down:
            if self._restart_worker is None or not self._restart_worker.isRunning():
                self._status_gen = getattr(self, "_status_gen", 0) + 1
                msg = _ENGINE_RESTARTING_MSG
                self.status_text.setText(msg)
                self.status_text.setStyleSheet(f"color: {C['text_muted']};")
                self.server_hint.setText(msg)
                self.reader.set_engine_notice(msg)
                self.reader.set_engine_problem("")
                worker = _ServerWarmup(self.client)
                self._restart_worker = worker
                _sweep_live_threads()
                _LIVE_THREADS.append(worker)
                worker.identified.connect(self._on_engine_identified)
                worker.ready.connect(self._on_restart_finished)
                worker.start()
        else:
            self._flash_status("Engine offline", C["danger"])

    def _on_restart_finished(self, ok: bool) -> None:
        self._restart_worker = None
        _sweep_live_threads()
        if self._closing:
            return
        if ok:
            self._server_ready = True
            self._engine_down = False
            self.client._watchdog_restarted = False
            self._engine_deaths = 0
            self.status_text.setText("")
            self.status_text.setStyleSheet("")
            self.server_hint.setText("")
            self.reader.set_engine_problem("")
            self.reader.set_engine_notice("")
            self._refresh_status()
            self._flash_status("Voice engine started", C["ok"])
        else:
            self._server_ready = False
            self._status_gen = getattr(self, "_status_gen", 0) + 1
            msg = _ENGINE_RESTART_FAIL_MSG
            self.status_text.setText(msg)
            self.status_text.setStyleSheet(
                f"color: {C['danger']}; font-weight: bold;")
            self.server_hint.setText(msg)
            self.reader.set_engine_notice("")
            self.reader.set_engine_problem(msg)
            self._refresh_status()

    def _on_capture_toggled(self, armed: bool) -> None:
        self.capture.register_window(int(self.winId()))
        self.capture.set_armed(armed)
        self.capture_btn.setText("  Capture")
        self.capture_btn.setIcon(
            svg_icon("highlight", C["bg"] if armed else C["text_muted"], 17)
        )
        self.reader.set_capture_mode(armed)
        if self._compact is not None:
            self._compact.set_capture(armed)
        self._store("capture_armed", armed)

    def _on_captured(self, text: str) -> None:
        self.player.stop()
        self.reader.set_captured(text)
        self.reader.set_capture_mode(True)
        if self.stack.currentIndex() != 0:
            self.nav_group.button(0).setChecked(True)
            self.stack.setCurrentIndex(0)
        if self.reader.autoplay.isChecked() and self._server_ready and text.strip():
            self._speak(text)

    def _toggle_sidebar(self) -> None:
        self._sidebar_visible = not self._sidebar_visible
        self._sidebar_widget.setVisible(self._sidebar_visible)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        narrow = self.width() < self.NARROW_WIDTH
        if narrow != self._was_narrow:
            self._was_narrow = narrow
            self._sidebar_visible = not narrow
            self._sidebar_widget.setVisible(self._sidebar_visible)

        if (self.isVisible()
                and not (self._compact and self._compact.isVisible())
                and self.width() < self.COMPACT_WIDTH):
            QTimer.singleShot(0, self._drop_to_compact)

    def _drop_to_compact(self) -> None:
        if not self.isVisible() or (self._compact and self._compact.isVisible()):
            return
        self._restore_size = QSize(max(self.width(), 940),
                                   max(self.height(), 700))
        self._toggle_compact()
        self.hide()

    def _toggle_compact(self) -> None:
        if self._compact and self._compact.isVisible():
            self._compact.close()
            self.widget_btn.setChecked(False)
            return
        if not self._compact:
            self._compact = CompactWidget()
            self._compact.setStyleSheet(self.styleSheet())
            self._compact.play_clicked.connect(self._on_play)
            self._compact.back_clicked.connect(lambda: self.player.skip(-10))
            self._compact.fwd_clicked.connect(lambda: self.player.skip(10))
            self._compact.speed_changed.connect(self._on_compact_speed)
            self._compact.volume_changed.connect(self._on_compact_volume)
            self._compact.voice_changed.connect(self._on_compact_voice)
            self._compact.autoplay_changed.connect(self._on_compact_autoplay)
            self._compact.capture_changed.connect(self._on_compact_capture)
            self._compact.show_main.connect(self._on_compact_show_main)
            self._compact.expand_requested.connect(self._on_compact_expand)
            self._compact.closed.connect(self._on_compact_closed)
        self.widget_btn.setChecked(True)
        box = self.reader.voice_box
        voices = [(box.itemData(i), box.itemText(i)) for i in range(box.count())]
        self._compact.load_voices(voices, box.currentIndex())
        self._compact.sync_state(
            self.player.state == "playing",
            self.reader.speed_slider.value(),
            self.settings_page.volume.value(),
            self.reader.autoplay.isChecked(),
            self.reader.spoken.text(),
        )
        self._compact.set_capture(self.capture_btn.isChecked())
        self._compact.show()
        self.capture.register_window(int(self._compact.winId()))
        self._refresh_status()

    def _on_compact_speed(self, value: int) -> None:
        self.reader.speed_slider.setValue(value)

    def _on_compact_volume(self, value: int) -> None:
        self.settings_page.volume.setValue(value)

    def _on_compact_voice(self, voice_id: str) -> None:
        index = self.reader.voice_box.findData(voice_id)
        if index >= 0:
            self.reader.voice_box.setCurrentIndex(index)

    def _on_compact_autoplay(self, checked: bool) -> None:
        self.reader.autoplay.setChecked(checked)

    def _on_compact_capture(self, armed: bool) -> None:
        self.capture_btn.setChecked(armed)

    def _save_geometry(self) -> None:
        self.settings["window_maximized"] = self.isMaximized()
        r = self.normalGeometry()
        if r.width() > 0 and r.height() > 0:
            self.settings["window_geometry"] = [r.x(), r.y(), r.width(), r.height()]

    def _centre_on_primary(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        w = min(self.width(), area.width())
        h = min(self.height(), area.height())
        self.setGeometry(area.x() + (area.width() - w) // 2,
                         area.y() + (area.height() - h) // 2,
                         w, h)

    def _restore_geometry(self) -> None:
        self._start_maximized = bool(self.settings.get("window_maximized"))
        saved = self.settings.get("window_geometry")
        rect = None
        if isinstance(saved, (list, tuple)) and len(saved) == 4:
            try:
                x, y, w, h = (int(v) for v in saved)
            except (TypeError, ValueError):
                rect = None
            else:
                rect = QRect(x, y, max(w, self.minimumWidth()),
                             max(h, self.minimumHeight()))
        if rect is not None and _reachable(rect):
            self.setGeometry(rect)
        else:
            self._centre_on_primary()

    def show_restored(self) -> None:
        if self._start_maximized:
            self.showMaximized()
        else:
            self.show()

    def _raise_main(self) -> None:
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_compact_show_main(self) -> None:
        self._on_compact_expand()
        self._raise_main()

    def _on_compact_expand(self) -> None:
        if self._restore_size is None and not self.isVisible():
            self._restore_size = QSize(940, 700)
        if self._compact is not None:
            self._compact.close()

    def _on_compact_closed(self) -> None:
        self.widget_btn.setChecked(False)
        if not self.isVisible():
            if self._restore_size is not None:
                self.resize(self._restore_size)
                self._restore_size = None
            self._raise_main()

    def _record_history(self, text: str) -> None:
        entry = {
            "when": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "text": text,
            "voice": self._voice,
            "speed": self._speed,
        }
        self.history.insert(0, entry)
        limit = int(self.settings.get("history_limit", 50))
        del self.history[limit:]
        config.save_history(self.history)
        self.history_page.load(self.history)

    def _on_replay(self, text: str) -> None:
        self.capture_btn.setChecked(False)
        self.reader.set_capture_mode(False)
        self.reader.editor.setPlainText(text)
        self.nav_group.button(0).setChecked(True)
        self.stack.setCurrentIndex(0)

    def _on_clear_history(self) -> None:
        self.history = []
        config.save_history(self.history)
        self.history_page.load(self.history)

    def _on_preview_voice(self, voice: str) -> None:
        if not self._server_ready:
            return
        self.player.load(translatelib.preview_text(voice), voice, self._speed)

    def _migrate_voice_prefs(self) -> None:
        ratings = self.settings.get("ratings") or {}
        if not ratings:
            return
        favourites = list(self.settings.get("favourites") or [])
        for voice, stars in ratings.items():
            try:
                liked = int(stars) >= 4
            except (TypeError, ValueError):
                liked = False
            if liked and voice not in favourites:
                favourites.append(voice)
        self.settings.pop("ratings", None)
        self.settings.pop("rated_first", None)
        self._store("favourites", favourites)

    def _on_favourites_changed(self, favourites: list) -> None:
        self._store("favourites", list(favourites))
        self._refill_voice_box()

    def _on_in_use_changed(self, in_use: list) -> None:
        self._store("in_use", list(in_use))
        self._refill_voice_box()

    def _on_voice_chosen(self, voice: str) -> None:
        box = self.reader.voice_box
        if box.findData(voice) < 0:
            self.voices_page.set_in_use(voice, True)
            self._store("in_use", self.voices_page.in_use())
            self._refill_voice_box()
        index = box.findData(voice)
        if index >= 0:
            box.setCurrentIndex(index)
        self.voices_page.set_current(voice)

    def _on_save(self) -> None:
        default = config.AUDIO_OUT / (
            _dt.datetime.now().strftime("backtalk_%Y%m%d_%H%M%S") + ".wav"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save audio", str(default), "WAV audio (*.wav)"
        )
        if not path:
            return
        if self.player.save_wav(Path(path)):
            self._flash_status("Saved", C["ok"])
        else:
            QMessageBox.information(self, "Nothing to save", "Play something first.")

    def _report_recovery(self) -> None:
        kept = config.RECOVERED_FROM
        if not kept or self._reported_recovery:
            return
        self._reported_recovery = True
        QMessageBox.warning(
            self, "Settings could not be read",
            "Backtalk could not read its settings file, so it has started with "
            "default settings.\n\nThe unreadable file has been kept, in case "
            "you want to recover anything from it:\n\n" + kept,
        )

    def _report_dir_fallback(self) -> None:
        moved = config.DIR_FALLBACK
        if not moved or self._reported_dir_fallback:
            return
        self._reported_dir_fallback = True
        QMessageBox.warning(
            self, "A folder could not be created",
            moved + "\n\nBacktalk will keep working, but that folder is "
            "temporary and Windows may clear it. Pick a different location "
            "under Settings to keep your files.",
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.capture.register_window(int(self.winId()))
        self._report_recovery()
        self._report_dir_fallback()
        if self._pending_arm:
            self._pending_arm = False
            self.capture_btn.setChecked(True)
        elif self.capture_btn.isChecked():
            self.capture.set_armed(True)
        self._refresh_status()

    def closeEvent(self, event) -> None:
        if self._compact and self._compact.isVisible():
            event.ignore()
            self.hide()
            return
        self._closing = True
        self.hide()
        if self._compact is not None:
            self._compact.hide()
        self._settings_debounce.stop()
        self.capture.shutdown()
        self.player.stop()
        self._release_translate_threads()
        if self._refresh_worker is not None and self._refresh_worker.isRunning():
            self._refresh_worker.wait(2500)
        if self._restart_worker is not None and self._restart_worker.isRunning():
            self._restart_worker.requestInterruption()
            self._restart_worker.wait(4000)
        self._save_geometry()
        config.save_settings(self.settings)
        self.client.stop_server()
        super().closeEvent(event)


def _force_taskbar_icon(hwnd: int, ico_path: str) -> None:
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32

    WM_SETICON = 0x0080
    ICON_SMALL = 0
    ICON_BIG = 1
    GCLP_HICON = -14
    GCLP_HICONSM = -34
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010

    LoadImageW = user32.LoadImageW
    LoadImageW.restype = ctypes.c_void_p

    hicon_big = LoadImageW(None, ico_path, IMAGE_ICON, 48, 48, LR_LOADFROMFILE)
    hicon_small = LoadImageW(None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)

    if hicon_big:
        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
        ctypes.windll.kernel32.SetLastError(0)
        user32.SetClassLongPtrW(hwnd, GCLP_HICON, hicon_big)
    if hicon_small:
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
        ctypes.windll.kernel32.SetLastError(0)
        user32.SetClassLongPtrW(hwnd, GCLP_HICONSM, hicon_small)


_MUTEX_NAME = "Backtalk.SingleInstance.v1"
_ERROR_ALREADY_EXISTS = 183
_instance_lock = None

def _claim_single_instance() -> bool:
    global _instance_lock
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                          ctypes.c_wchar_p]
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            return True
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            return False
        _instance_lock = handle
        return True
    except OSError:
        return True


def _raise_existing_window() -> None:
    try:
        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, _wt.HWND, _wt.LPARAM)
        def _visit(hwnd, _param):
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(hwnd, buf, 64)
            if buf.value == config.APP_NAME:
                found.append(int(hwnd))
            return True

        user32.EnumWindows(_visit, 0)
        for hwnd in found:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            break
    except OSError:
        pass


def _qt_fatal_handler(msg_type: int, context: object, message: str) -> None:
    if msg_type not in (QtMsgType.QtCriticalMsg,
                        QtMsgType.QtFatalMsg):
        return
    try:
        from . import config
        path = config.DATA / "qt-fatal.log"
        with open(path, "a", encoding="utf-8",
                  errors="replace") as fh:
            fh.write(_dt.datetime.now().isoformat()
                     + " " + str(message).rstrip() + "\n")
            fh.flush()
    except Exception:
        pass


_crash_trace_file = None


def _enable_crash_trace() -> None:
    global _crash_trace_file
    try:
        path = config.DATA / "crash-trace.log"
        if path.exists() and path.stat().st_size > 1_000_000:
            path.replace(path.with_name("crash-trace.log.1"))
        fh = open(path, "a", encoding="utf-8", errors="replace")
        fh.write(_dt.datetime.now().isoformat() + " started\n")
        fh.flush()
        faulthandler.enable(file=fh, all_threads=True)
        _crash_trace_file = fh
    except Exception:
        pass


def run() -> int:
    if not _claim_single_instance():
        _raise_existing_window()
        return 0
    qInstallMessageHandler(_qt_fatal_handler)
    app = QApplication([])
    stallwatch.start()
    app.setApplicationName(config.APP_NAME)
    app.setWindowIcon(QIcon(str(config.ASSETS / "backtalk.ico")))
    settings = config.load_settings()
    if settings.get("audio_out_path"):
        config.AUDIO_OUT = Path(settings["audio_out_path"])
    if settings.get("data_path"):
        config.DATA = Path(settings["data_path"])
    config._ensure_dirs()
    _enable_crash_trace()
    config.set_theme(settings.get("theme", "light"))
    app.setStyleSheet(stylesheet())
    window = MainWindow()
    ico = str(config.ASSETS / "backtalk.ico")

    def _apply_icon() -> None:
        hwnd = int(window.winId())
        if hwnd:
            _force_taskbar_icon(hwnd, ico)

    if window._warmup is not None:
        splash = SplashScreen()
        window._warmup.progress.connect(splash.set_progress)
        def _show_main(ok: bool) -> None:
            splash.close()
            window.show_restored()
            QTimer.singleShot(100, _apply_icon)
        window._warmup.ready.connect(_show_main)
        splash.show()
        window._warmup.start()
    else:
        window.show_restored()
        QTimer.singleShot(100, _apply_icon)
    return app.exec()
