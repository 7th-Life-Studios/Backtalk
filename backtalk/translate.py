from __future__ import annotations

import threading
from dataclasses import dataclass

from .cjk_forms import JA_ONLY_CHARS, ZH_ONLY_CHARS

VOICE_PREFIX_LANG: dict[str, tuple[str, str]] = {
    "a": ("en", "English - US"),
    "b": ("en", "English - UK"),
    "e": ("es", "Spanish"),
    "f": ("fr", "French"),
    "h": ("hi", "Hindi"),
    "i": ("it", "Italian"),
    "j": ("ja", "Japanese"),
    "p": ("pt", "Portuguese"),
    "z": ("zh", "Chinese"),
}

SPEAKABLE = ("en", "es", "fr", "hi", "it", "ja", "pt", "zh")

BRIDGE_VIA: dict[str, str] = {"es": "pt"}

MIN_DETECT_CHARS = 12

MIN_SEGMENT_CHARS = 20

MIN_LATIN_SWITCH_CHARS = 40

LATIN_LANGS = frozenset({"en", "es", "fr", "it", "pt"})


_KANA = ((0x3040, 0x309F), (0x30A0, 0x30FF))
_HAN = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF))
_DEVANAGARI = ((0x0900, 0x097F),)


def _in(code: int, ranges) -> bool:
    return any(lo <= code <= hi for lo, hi in ranges)


def _script(ch: str) -> str:
    code = ord(ch)
    if _in(code, _KANA):
        return "kana"
    if _in(code, _HAN):
        return "han"
    if _in(code, _DEVANAGARI):
        return "deva"
    if ch.isalpha():
        return "latin"
    return "neutral"


_ZH_ONLY = frozenset(ZH_ONLY_CHARS)
_JA_ONLY = frozenset(JA_ONLY_CHARS)


def han_language(text: str) -> str | None:
    zh = sum(1 for c in text if c in _ZH_ONLY)
    ja = sum(1 for c in text if c in _JA_ONLY)
    if zh and not ja:
        return "zh"
    if ja and not zh:
        return "ja"
    if zh != ja:
        return "zh" if zh > ja else "ja"
    return None


def script_language(text: str, has_kana_nearby: bool = False) -> str | None:
    scripts = {_script(c) for c in text if not c.isspace()}
    scripts.discard("neutral")
    if not scripts:
        return None
    if "kana" in scripts:
        return "ja"
    if "deva" in scripts:
        return "hi"
    if "han" in scripts:
        by_form = han_language(text)
        if by_form:
            return by_form
        return "ja" if has_kana_nearby else "zh"
    return None


_LANGUAGE_SCRIPTS: dict[str, frozenset[str]] = {
    "en": frozenset({"latin"}),
    "es": frozenset({"latin"}),
    "fr": frozenset({"latin"}),
    "it": frozenset({"latin"}),
    "pt": frozenset({"latin"}),
    "hi": frozenset({"deva"}),
    "ja": frozenset({"kana", "han"}),
    "zh": frozenset({"han"}),
}


def scripts_in(text: str) -> set[str]:
    found = {_script(c) for c in text if not c.isspace()}
    found.discard("neutral")
    return found


def can_pronounce(voice_id: str, text: str) -> bool:
    readable = _LANGUAGE_SCRIPTS.get(voice_language(voice_id))
    if readable is None:
        return True
    present = scripts_in(text)
    if not present:
        return True
    if present <= readable:
        return True
    return present - readable == {"latin"} and bool(present & readable)


def segment(text: str) -> list[str]:
    if not text:
        return []

    parts: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.strip():
            parts.append(line)
        elif parts:
            parts[-1] += line
        else:
            parts.append(line)

    terminators = ".!?。！？"
    sentences: list[str] = []
    for part in parts:
        buf = ""
        for i, ch in enumerate(part):
            buf += ch
            if ch == "\n":
                if buf.strip():
                    sentences.append(buf)
                    buf = ""
                continue
            if ch not in terminators:
                continue
            nxt = part[i + 1] if i + 1 < len(part) else ""
            if nxt in terminators:
                continue
            if nxt and not nxt.isspace():
                continue
            if buf.strip(terminators + " \t\r\n"):
                sentences.append(buf)
                buf = ""
        if buf:
            sentences.append(buf)

    runs: list[str] = []
    for sentence in sentences:
        buf = ""
        current = None
        for ch in sentence:
            script = _script(ch)
            if script == "neutral" or current is None:
                buf += ch
                if script != "neutral":
                    current = script
                continue
            same = script == current or (
                {script, current} <= {"han", "kana"}
            )
            if same:
                buf += ch
            else:
                runs.append(buf)
                buf = ch
                current = script
        if buf:
            runs.append(buf)
    return [r for r in runs if r]


def voice_language(voice_id: str) -> str:
    if not voice_id:
        return "en"
    return VOICE_PREFIX_LANG.get(voice_id[:1].lower(), ("en", ""))[0]


PREVIEW_TEXT: dict[str, str] = {
    "en": "This is how I sound when Backtalk reads your text out loud.",
    "es": "Asi sueno cuando Backtalk lee tu texto en voz alta.",
    "fr": "Voici ma voix lorsque Backtalk lit votre texte a voix haute.",
    "it": "Ecco come suono quando Backtalk legge il tuo testo ad alta voce.",
    "pt": "É assim que eu soo quando o Backtalk lê o seu texto em voz alta.",
    "hi": "जब बैकटॉक आपका पाठ पढ़ता है तो मेरी आवाज़ ऐसी लगती है।",
    "zh": "这就是我朗读你的文字时的声音。",
    "ja": "バックトークがあなたの文章を読み上げると、こんな声になります。",
}


def preview_text(voice_id: str) -> str:
    return PREVIEW_TEXT.get(voice_language(voice_id), PREVIEW_TEXT["en"])


def language_name(code: str) -> str:
    for lang_code, name in VOICE_PREFIX_LANG.values():
        if lang_code == code:
            return name.split(" - ")[0]
    return code


@dataclass
class Result:
    text: str
    source: str | None
    target: str
    translated: bool
    note: str = ""

    @property
    def status(self) -> str:
        if not self.translated:
            return self.note
        if self.source:
            return f"{language_name(self.source)} to {language_name(self.target)}"
        return f"{self.note} to {language_name(self.target)}" if self.note else \
               f"translated to {language_name(self.target)}"


class Translator:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._langs = None
        self._langid = None
        self._available = None
        self._warmed: set[str] = set()


    def available(self) -> bool:
        if self._available is None:
            try:
                import argostranslate.translate
                self._available = True
            except Exception:
                self._available = False
        return self._available

    def _languages(self):
        if self._langs is None:
            from argostranslate import translate as _t
            self._langs = _t.get_installed_languages()
        return self._langs

    def can_translate(self, source: str, target: str) -> bool:
        if source == target:
            return True
        if not self.available():
            return False
        try:
            langs = self._languages()
            src = next((x for x in langs if x.code == source), None)
            dst = next((x for x in langs if x.code == target), None)
            if src is None or dst is None:
                return False
            return src.get_translation(dst) is not None
        except Exception:
            return False


    def detect(self, text: str) -> str | None:
        stripped = text.strip()
        if len(stripped) < MIN_DETECT_CHARS:
            return None
        if self._langid is None:
            try:
                import py3langid
                try:
                    py3langid.set_languages(list(SPEAKABLE))
                except Exception:
                    pass
                self._langid = py3langid
            except Exception:
                self._langid = False
        if not self._langid:
            return None
        try:
            code, _score = self._langid.classify(stripped)
            return code if code in SPEAKABLE else None
        except Exception:
            return None


    def translate(self, text: str, target: str, source: str | None = None) -> Result:
        if not text.strip():
            return Result(text, None, target, False, "empty")

        if target not in SPEAKABLE:
            return Result(text, None, target, False, f"{target} not speakable")

        if source is None:
            source = self.detect(text)

        if source is None:
            return Result(text, None, target, False, "language not detected")

        if source == target:
            return Result(text, source, target, False,
                          f"already {language_name(target)}")

        if not self.available():
            return Result(text, source, target, False, "translator not installed")

        with self._lock:
            try:
                langs = self._languages()
                find = lambda code: next(
                    (x for x in langs if x.code == code), None
                )
                src = find(source)
                dst = find(target)
                if src is None or dst is None:
                    missing = source if src is None else target
                    return Result(text, source, target, False,
                                  f"{language_name(missing)} pack not installed")

                working = text
                bridge_code = BRIDGE_VIA.get(source)
                if bridge_code and bridge_code != target:
                    bridge = find(bridge_code)
                    hop = src.get_translation(bridge) if bridge else None
                    if hop is not None:
                        working = hop.translate(working)
                        src = bridge

                route = src.get_translation(dst)
                if route is None:
                    return Result(text, source, target, False,
                                  f"no route {source} to {target}")
                out = route.translate(working)
                if not out or not out.strip():
                    return Result(text, source, target, False, "empty translation")
                if out.strip() == text.strip():
                    return Result(text, source, target, False, "nothing to change")
                return Result(out, source, target, True)
            except Exception as exc:
                return Result(text, source, target, False,
                              f"translation failed: {type(exc).__name__}")


    def segment_language(
        self, text: str, has_kana: bool, statistical: bool = True
    ) -> str | None:
        by_script = script_language(text, has_kana)
        if by_script:
            return by_script
        if not statistical:
            return None
        if len(text.strip()) < MIN_SEGMENT_CHARS:
            return None
        return self.detect(text)

    def needs_translation(self, text: str, target: str) -> bool:
        if not text.strip() or target not in SPEAKABLE:
            return False
        has_kana = any(_script(c) == "kana" for c in text)
        for chunk in segment(text):
            if not chunk.strip():
                continue
            lang = self.segment_language(chunk, has_kana)
            if lang and lang != target:
                return True
        return False

    def translate_text(self, text: str, target: str) -> Result:
        if not text.strip():
            return Result(text, None, target, False, "empty")
        if target not in SPEAKABLE:
            return Result(text, None, target, False, f"{target} not speakable")

        segments = segment(text)
        has_kana = any(_script(c) == "kana" for c in text)

        langs = [self.segment_language(s, has_kana) for s in segments]
        foreign = {l for l in langs if l and l != target}

        if not foreign:
            only = {l for l in langs if l}
            source = next(iter(only)) if len(only) == 1 else None
            return Result(text, source, target, False,
                          f"already {language_name(target)}")

        identified = {l for l in langs if l}
        if len(identified) == 1:
            return self.translate(text, target, source=next(iter(identified)))

        out: list[str] = []
        translated_any = False
        for chunk, lang in zip(segments, langs):
            if not lang or lang == target or not chunk.strip():
                out.append(chunk)
                continue
            piece = self.translate(chunk, target, source=lang)
            if piece.translated:
                translated_any = True
                lead = chunk[:len(chunk) - len(chunk.lstrip())]
                trail = chunk[len(chunk.rstrip()):]
                out.append(f"{lead}{piece.text.strip()}{trail}")
            else:
                out.append(chunk)

        joined = "".join(out)
        if not translated_any:
            return Result(text, None, target, False, "nothing could be translated")
        names = ", ".join(sorted(language_name(f) for f in foreign))
        return Result(joined, None, target, True, f"mixed: {names}")

    def voice_plan(
        self, text: str, target: str, voice: str, pick_voice, on_skip=None,
        switch_latin: bool = False,
    ) -> list[tuple[str, str]]:
        has_kana = any(_script(c) == "kana" for c in text)
        plan: list[tuple[str, str]] = []

        for chunk in segment(text):
            if not chunk.strip():
                if plan:
                    plan[-1] = (plan[-1][0] + chunk, plan[-1][1])
                continue

            lang = self.segment_language(chunk, has_kana, statistical=False)

            if lang is None and not can_pronounce(voice, chunk):
                lang = self.segment_language(chunk, has_kana, statistical=True)

            if (lang is None and switch_latin and can_pronounce(voice, chunk)
                    and len(chunk.strip()) >= MIN_LATIN_SWITCH_CHARS):
                candidate = self.detect(chunk)
                if candidate in LATIN_LANGS and candidate != voice_language(voice):
                    other = pick_voice(candidate)
                    if other and can_pronounce(other, chunk):
                        lang = candidate

            if (lang is None or lang == target) and can_pronounce(voice, chunk):
                spoken, use = chunk, voice
            else:
                other = pick_voice(lang) if lang else None
                if other and can_pronounce(other, chunk):
                    spoken, use = chunk, other
                else:
                    result = self.translate(chunk, target, source=lang)
                    if not result.translated:
                        if on_skip is not None:
                            on_skip(chunk, lang)
                    spoken, use = result.text, voice

            if plan and plan[-1][1] == use:
                plan[-1] = (plan[-1][0] + spoken, use)
            else:
                plan.append((spoken, use))

        return plan


    def warm(self, target: str = "en") -> bool:
        if target in self._warmed or not self.available():
            return False
        try:
            source = "es" if target != "es" else "en"
            if self.can_translate(source, target):
                self.translate("Hello, this is a warm up sentence.", target,
                               source=source)
            self._warmed.add(target)
            return True
        except Exception:
            return False


translator = Translator()
