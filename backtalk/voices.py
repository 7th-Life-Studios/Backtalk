from __future__ import annotations

import json

from .translate import SPEAKABLE, voice_language, language_name

ENGLISH_ONLY = False

HIDE_OLDER = True

LABELS: dict[str, str] = {
    "af_nicole": "Tammy_BT",
    "af_alloy": "Aja_BT",
    "af_heart": "Linda_BT",
    "af_bella": "Patricia_BT",
    "af_sarah": "Christine_BT",
    "af_sky": "Lindsay_BT",
    "af_nova": "Britney_BT",
    "af_kore": "Angela_BT",
    "af_river": "Chani_BT",
    "af_jessica": "Terri_BT",
    "af_aoede": "Katrina_BT",
    "af_jadzia": "Nancy_BT",
    "am_adam": "Arlo_BT",
    "am_eric": "Eric_BT",
    "am_michael": "Marshall_BT",
    "am_fenrir": "Preston_BT",
    "am_onyx": "Perry_BT",
    "am_echo": "Mark_BT",
    "am_puck": "Ron_BT",
    "am_liam": "Lee_BT",
    "am_santa": "Bob_BT",
    "bf_alice": "Hillori_BT",
    "bf_emma": "Elizabeth_BT",
    "bf_isabella": "Julia_BT",
    "bf_lily": "Grace_BT",
    "bm_fable": "Chris_BT",
    "bm_daniel": "Henry_BT",
    "bm_george": "Charles_BT",
    "bm_lewis": "William_BT",
    "ef_dora": "Paula_BT",
    "em_alex": "Pablo_BT",
    "em_santa": "Julio_BT",
    "pf_dora": "Aline_BT",
    "pm_alex": "Victor_BT",
    "pm_santa": "Ubaldo_BT",
    "ff_siwis": "Ines_BT",
    "if_sara": "Michela_BT",
    "im_nicola": "Nicola_BT",
    "jf_alpha": "Yoko_BT",
    "jf_gongitsune": "Kanae_BT",
    "jf_nezumi": "Mari_BT",
    "jf_tebukuro": "Shinobu_BT",
    "jm_kumo": "Kazuhiro_BT",
    "zf_xiaobei": "Zhangman_BT",
    "zf_xiaoni": "Xiaoling_BT",
    "zf_xiaoxiao": "Xiaoyang_BT",
    "zf_xiaoyi": "Menghui_BT",
    "zm_yunxia": "Meilin_BT",
    "zm_yunjian": "Wang_BT",
    "zm_yunxi": "Bin_BT",
    "zm_yunyang": "Haoran_BT",
    "hf_alpha": "Meera_BT",
    "hf_beta": "Lata_BT",
    "hm_omega": "Rajiv_BT",
    "hm_psi": "Sunil_BT",
    "af_v0": "Blended Female (older)",
    "af_v0bella": "Rich Female (older)",
    "af_v0irulan": "Stately Female (older)",
    "af_v0nicole": "Whispery Female (older)",
    "af_v0sarah": "Clear Female (older)",
    "af_v0sky": "Light Female (older)",
    "am_v0adam": "Flat Male (older)",
    "am_v0gurney": "Gravelly Male (older)",
    "am_v0michael": "Warm Male (older)",
    "bf_v0emma": "Warm British Female (older)",
    "bf_v0isabella": "Formal British Female (older)",
    "bm_v0george": "Deep British Male (older)",
    "bm_v0lewis": "Gruff British Male (older)",
}

BUNDLED = [
    "am_adam",
    "bm_fable",
    "bm_daniel",
    "af_nicole",
    "af_alloy",
    "bf_alice",
]

_GENDER = {"f": "Female", "m": "Male"}

GENDER_OVERRIDE: dict[str, str] = {
    "zm_yunxia": "f",
}


def gender(voice_id: str) -> str:
    return GENDER_OVERRIDE.get(voice_id) or voice_id[1:2]


def is_english(voice_id: str) -> bool:
    return voice_id[:1] in ("a", "b")


def is_older(voice_id: str) -> bool:
    return "v0" in voice_id


def modern_equivalent(voice_id: str, available: list[str]) -> str | None:
    if not is_older(voice_id):
        return None
    candidate = voice_id.replace("v0", "", 1)
    return candidate if candidate in available else None


def is_speakable(voice_id: str) -> bool:
    return voice_language(voice_id) in SPEAKABLE


def shipped_ids() -> list[str]:
    return [v for v in LABELS if not (HIDE_OLDER and "v0" in v)]


def duplicate_names() -> dict[str, list[str]]:
    claimed: dict[str, list[str]] = {}
    for vid in shipped_ids():
        claimed.setdefault(LABELS[vid], []).append(vid)
    return {name: ids for name, ids in claimed.items() if len(ids) > 1}


_COLLIDING = frozenset(duplicate_names())


def base_label(voice_id: str) -> str:
    if voice_id in LABELS:
        return LABELS[voice_id]
    word = _GENDER.get(gender(voice_id), "")
    stem = voice_id.split("_", 1)[-1].replace("_", " ").strip().title()
    if not stem:
        stem = voice_id.title()
    return f"{stem} {word}".strip()


def label(voice_id: str) -> str:
    name = base_label(voice_id)
    lang = language_name(voice_language(voice_id))
    if name in _COLLIDING:
        return f"{name} ({lang}) [{voice_id}]"
    return f"{name} ({lang})"


def sort_key(voice_id: str, preferred: str | None = None) -> tuple:
    off_language = 0 if (preferred and voice_language(voice_id) == preferred) else 1
    in_bundled = BUNDLED.index(voice_id) if voice_id in BUNDLED else len(BUNDLED)
    older = 1 if "v0" in voice_id else 0
    is_female = 0 if gender(voice_id) == "f" else 1
    return (off_language, in_bundled, older, is_female, base_label(voice_id))


def ordered(voice_ids: list[str], preferred: str | None = None) -> list[str]:
    if ENGLISH_ONLY:
        filtered = [v for v in voice_ids if is_english(v)]
    else:
        filtered = [v for v in voice_ids if is_speakable(v)]
    if HIDE_OLDER:
        filtered = [v for v in filtered if not is_older(v)]
    return sorted(filtered, key=lambda v: sort_key(v, preferred))


def by_language(voice_ids: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for voice_id in voice_ids:
        if not is_speakable(voice_id):
            continue
        if HIDE_OLDER and is_older(voice_id):
            continue
        groups.setdefault(voice_language(voice_id), []).append(voice_id)
    for code in groups:
        groups[code] = sorted(groups[code], key=lambda v: sort_key(v))
    return groups


def default_voice(voice_ids: list[str], language: str) -> str | None:
    candidates = by_language(voice_ids).get(language, [])
    return candidates[0] if candidates else None


def default_in_use(voice_ids: list[str]) -> list[str]:
    groups = by_language(voice_ids)
    return [group[0] for group in groups.values() if group]


def languages_ordered(voice_ids: list[str], preferred: str | None = None) -> list[str]:
    groups = by_language(voice_ids)
    return sorted(
        groups,
        key=lambda code: (0 if code == preferred else 1, language_name(code)),
    )


_PROFILES: dict[str, dict] | None = None


def _profiles() -> dict[str, dict]:
    global _PROFILES
    if _PROFILES is None:
        _PROFILES = {}
        try:
            from . import config

            candidates = [
                config.DATA / "voice_profiles.json",
                config.ROOT / "data" / "voice_profiles.json",
            ]
            for path in candidates:
                if not path.exists():
                    continue
                raw = json.loads(path.read_text(encoding="utf-8"))
                found = raw.get("voices")
                if isinstance(found, dict) and found:
                    _PROFILES = found
                    break
        except (OSError, json.JSONDecodeError, ValueError, AttributeError):
            _PROFILES = {}
    return _PROFILES


def pitch_hz(voice_id: str) -> float | None:
    value = _profiles().get(voice_id, {}).get("pitch_hz")
    return float(value) if isinstance(value, (int, float)) else None


def pitch_band(voice_id: str) -> str:
    value = _profiles().get(voice_id, {}).get("pitch_band")
    return value if isinstance(value, str) else ""


def pitch_sort_key(voice_id: str) -> tuple:
    value = pitch_hz(voice_id)
    return (
        0 if gender(voice_id) == "f" else 1,
        1 if value is None else 0,
        value if value is not None else 0.0,
        base_label(voice_id),
    )
