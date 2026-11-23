"""Devanagari handling, word-synced captions, CapCut text styles, glow and pop keyframes."""

import copy
import re

from streamclipper.captions.styles import (
    NEON_PALETTE,
    CaptionStyle,
    apply_case,
    emoji_positions,
    get_style,
    mask_profanity,
    style_name,
)
from streamclipper.env import GLOW_PRESETS
from streamclipper.util import find_triggers, new_id, utf16_len

DEVANAGARI_CONSONANTS = {
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "n",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "झ": "jh",
    "ञ": "n",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ल": "l",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
    "क़": "q",
    "ख़": "kh",
    "ग़": "gh",
    "ज़": "z",
    "ड़": "r",
    "ढ़": "rh",
    "फ़": "f",
}


DEVANAGARI_VOWELS = {
    "अ": "a",
    "आ": "aa",
    "इ": "i",
    "ई": "ee",
    "उ": "u",
    "ऊ": "oo",
    "ऋ": "ri",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "ऑ": "o",
    "ऍ": "e",
    "ऎ": "e",
    "ऒ": "o",
    "ॐ": "om",
}


DEVANAGARI_MATRAS = {
    "ा": "a",
    "ि": "i",
    "ी": "ee",
    "ु": "u",
    "ू": "oo",
    "ृ": "ri",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    "ॉ": "o",
    "ॅ": "e",
    "ॆ": "e",
    "ॊ": "o",
    "्": "",
}

# Nasal/visarga modifiers follow a syllable without replacing a bare consonant's inherent 'a'
# (e.g. बंदा = ब + ं + द + ा -> "banda", NOT "bnda").
DEVANAGARI_MODIFIERS = {
    "ं": "n",
    "ँ": "n",
    "ः": "h",
}

DEVANAGARI_DIGITS = {chr(0x0966 + d): str(d) for d in range(10)}

# Whisper often emits Urdu/Perso-Arabic script for spoken Hindi/Hindustani when language='auto'.
URDU_DIGRAPHS = {
    "بھ": "bh",
    "پھ": "ph",
    "تھ": "th",
    "ٹھ": "th",
    "جھ": "jh",
    "چھ": "chh",
    "دھ": "dh",
    "ڈھ": "dh",
    "رھ": "rh",
    "ڑھ": "rh",
    "کھ": "kh",
    "گھ": "gh",
    "لھ": "lh",
    "مھ": "mh",
    "نھ": "nh",
}

URDU_CHARS = {
    "ا": "a",
    "آ": "aa",
    "أ": "a",
    "إ": "i",
    "ب": "b",
    "پ": "p",
    "ت": "t",
    "ٹ": "t",
    "ث": "s",
    "ج": "j",
    "چ": "ch",
    "ح": "h",
    "خ": "kh",
    "د": "d",
    "ڈ": "d",
    "ذ": "z",
    "ر": "r",
    "ڑ": "r",
    "ز": "z",
    "ژ": "zh",
    "س": "s",
    "ش": "sh",
    "ص": "s",
    "ض": "z",
    "ط": "t",
    "ظ": "z",
    "ع": "a",
    "غ": "gh",
    "ف": "f",
    "ق": "q",
    "ک": "k",
    "ك": "k",
    "گ": "g",
    "ل": "l",
    "م": "m",
    "ن": "n",
    "ں": "n",
    "و": "o",
    "ؤ": "o",
    "ہ": "h",
    "ه": "h",
    "ھ": "h",
    "ۃ": "t",
    "ء": "",
    "ی": "i",
    "ي": "i",
    "ئ": "i",
    "ے": "e",
    **{chr(0x06F0 + d): str(d) for d in range(10)},
    **{chr(0x0660 + d): str(d) for d in range(10)},
}


# Natural Hinglish spelling overrides (turns literal Devanagari transliteration into editor-grade Hinglish).
HINGLISH_LEXICON = {
    "bhaee": "bhai",
    "bhaiyaa": "bhaiya",
    "naheen": "nahi",
    "nahin": "nahi",
    "kyon": "kyun",
    "kyonki": "kyunki",
    "seen": "scene",
    "bhee": "bhi",
    "abhee": "abhi",
    "kabhee": "kabhi",
    "sabhee": "sabhi",
    "wahee": "wahi",
    "yahee": "yahi",
    "kuchh": "kuch",
    "phir": "fir",
    "shuroo": "shuru",
    "zindagee": "zindagi",
    "ladakee": "ladki",
    "ladaka": "ladka",
    "gaadee": "gadi",
    "gaaree": "gadi",
    "maaree": "mari",
    "sachee": "sachi",
    "asalee": "asli",
    "nakalee": "nakli",
    "jaldee": "jaldi",
    "theek": "thik",
    "achchha": "achha",
    "achchhe": "achhe",
    "bhaag": "bhag",
    "bhaago": "bhago",
    "dekhie": "dekhiye",
    "sunie": "suniye",
    "karie": "kariye",
    "chahie": "chahiye",
    "chaahie": "chahiye",
    "hamaara": "hamara",
    "tumhaara": "tumhara",
    "unaka": "unka",
    "inaka": "inka",
    "usaka": "uska",
    "isaka": "iska",
    "apanee": "apni",
    "apana": "apna",
    "apane": "apne",
    "kitana": "kitna",
    "kitanee": "kitni",
    "kitane": "kitne",
    "itana": "itna",
    "itanee": "itni",
    "itane": "itne",
    "khatarnak": "khatarnak",
    "khatarnaak": "khatarnak",
    "bos": "boss",
    "shoat": "shot",
    "shaut": "shot",
    "gaim": "game",
    "stareem": "stream",
    "streem": "stream",
    "chat": "chat",
    "bro": "bro",
}


def _polish_hinglish_line(line: str) -> str:
    """Apply natural Hinglish spellings per word while preserving punctuation and original capitalisation."""
    if not line:
        return ""

    def _repl(m: re.Match) -> str:
        word = m.group(0)
        low = word.lower()
        rep = HINGLISH_LEXICON.get(low)
        if not rep:
            return word
        if word.isupper():
            return rep.upper()
        if word[0].isupper():
            return rep.capitalize()
        return rep

    return re.sub(r"[A-Za-z]+", _repl, line)


def devanagari_to_hinglish(text: str, strip_emojis: bool = True) -> str:
    """
    Convert Devanagari (and Urdu/Perso-Arabic Whisper Hindi output) into readable Roman Hinglish,
    preserving ASCII, newlines and optional emojis while stripping stray CJK/unknown glyphs.
    """
    if not text:
        return ""
    out = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch in DEVANAGARI_CONSONANTS:
            if i + 1 < n and text[i + 1] == "़":
                base = DEVANAGARI_CONSONANTS.get(ch + "़", DEVANAGARI_CONSONANTS[ch])
                i += 2
            else:
                base = DEVANAGARI_CONSONANTS[ch]
                i += 1
            if i < n and text[i] in DEVANAGARI_MATRAS:
                out.append(base + DEVANAGARI_MATRAS[text[i]])
                i += 1
            elif i < n and (
                text[i] in DEVANAGARI_CONSONANTS
                or text[i] in DEVANAGARI_VOWELS
                or text[i] in DEVANAGARI_MODIFIERS
            ):
                out.append(base + "a")
            else:
                out.append(base)
        elif ch in DEVANAGARI_VOWELS:
            out.append(DEVANAGARI_VOWELS[ch])
            i += 1
        elif ch in DEVANAGARI_MATRAS:
            out.append(DEVANAGARI_MATRAS[ch])
            i += 1
        elif ch in DEVANAGARI_MODIFIERS:
            out.append(DEVANAGARI_MODIFIERS[ch])
            i += 1
        elif ch in DEVANAGARI_DIGITS:
            out.append(DEVANAGARI_DIGITS[ch])
            i += 1
        elif i + 1 < n and text[i : i + 2] in URDU_DIGRAPHS:
            out.append(URDU_DIGRAPHS[text[i : i + 2]])
            i += 2
        elif ch in URDU_CHARS:
            out.append(URDU_CHARS[ch])
            i += 1
        elif ord(ch) < 128:
            out.append(ch)
            i += 1
        elif not strip_emojis and ord(ch) > 0x1F000:
            out.append(ch)
            i += 1
        else:
            i += 1
    lines = [_polish_hinglish_line(re.sub(r"[ \t]+", " ", ln).strip()) for ln in "".join(out).split("\n")]
    return "\n".join(ln for ln in lines if ln)


def wrap_caption_lines(raw_text: str, max_chars_per_line: int = 13) -> str:
    """
    Guarantee that no caption line ever overflows the 9:16 or 1:1 canvas width!
    Preserves `{color:word}` tags while inserting `\n` between balanced lines.
    """
    if "\n" in raw_text:
        return raw_text
    plain = re.sub(r"\{(yellow|green|red|cyan|orange|pink|purple|white|black):([^{}]+)\}", r"\2", raw_text)
    plain = re.sub(r"\*([^*]+)\*", r"\1", plain)
    if len(plain) <= max_chars_per_line:
        return raw_text

    # Tokenize keeping `{color:...}` intact as single tokens or splitting words
    tokens = re.findall(r"\{[a-z]+:[^{}]+\}|\*[^*]+\*|\S+", raw_text)
    if len(tokens) <= 1:
        return raw_text

    def plain_len(tok: str) -> int:
        t = re.sub(r"\{[a-z]+:([^{}]+)\}", r"\1", tok)
        t = re.sub(r"\*([^*]+)\*", r"\1", t)
        return len(t)

    # Split into 2 balanced lines
    total_l = sum(plain_len(t) for t in tokens) + len(tokens) - 1
    if total_l <= max_chars_per_line:
        return raw_text

    best_split = 1
    best_diff = 9999
    for split_i in range(1, len(tokens)):
        l1 = sum(plain_len(t) for t in tokens[:split_i]) + split_i - 1
        l2 = sum(plain_len(t) for t in tokens[split_i:]) + (len(tokens) - split_i) - 1
        diff = abs(l1 - l2)
        if diff < best_diff:
            best_diff = diff
            best_split = split_i
    return " ".join(tokens[:best_split]) + "\n" + " ".join(tokens[best_split:])


def parse_multicolor_styles(
    raw_text: str,
    base_style: dict,
    font_path: str,
    font_id: str,
    font_sz: float,
    stroke_w: float,
    auto_highlight: bool = True,
    max_chars_per_line: int = 13,
    keep_devanagari: bool = False,
    keep_emoji: bool = False,
    colour_map: dict = None,
    stroke_rgb: list = None,
    glow_fallback_rgb: list = None,
):
    """
    Build CapCut multi-range `styles[]` with:
      - Automatic safe 2-line wrapping (`<= 13 chars/line`) so text NEVER clips off 9:16 edges
      - UTF-16 code unit `range` calculation (`utf16_len`) so C++/Qt never drops glyphs (`Y`, `C`)
      - Crisp dark drop-shadow (`diffuse: 0.025`, `alpha: 0.88`, `distance: 4.5`, `color: [0,0,0]`)
        preventing any yellow/orange shadow blob!
    """
    if not keep_devanagari:
        raw_text = devanagari_to_hinglish(raw_text, strip_emojis=not keep_emoji)
    if not raw_text:
        raw_text = "..."
    raw_text = wrap_caption_lines(raw_text, max_chars_per_line=max_chars_per_line)

    segments = []
    tag_re = re.compile(r"\{(yellow|green|red|cyan|orange|pink|purple|white|black):([^{}]+)\}|\*([^*]+)\*")
    pos = 0
    clean_parts = []

    has_explicit_tags = bool(tag_re.search(raw_text))
    if has_explicit_tags:
        u16_cursor = 0
        for m in tag_re.finditer(raw_text):
            if m.start() > pos:
                chunk = raw_text[pos : m.start()]
                clen = utf16_len(chunk)
                clean_parts.append(chunk)
                segments.append((u16_cursor, u16_cursor + clen, "white"))
                u16_cursor += clen
            if m.group(1):
                col_name = m.group(1).lower()
                word = m.group(2)
            else:
                col_name = "yellow"
                word = m.group(3)
            wlen = utf16_len(word)
            clean_parts.append(word)
            segments.append((u16_cursor, u16_cursor + wlen, col_name))
            u16_cursor += wlen
            pos = m.end()
        if pos < len(raw_text):
            chunk = raw_text[pos:]
            clen = utf16_len(chunk)
            clean_parts.append(chunk)
            segments.append((u16_cursor, u16_cursor + clen, "white"))
            u16_cursor += clen
        clean_text = "".join(clean_parts)
    else:
        clean_text = raw_text
        if auto_highlight:
            # Power words and trigger phrases (unicode-aware, incl. Devanagari) are red; the rest white.
            # Whole words only, so a style never splits a Devanagari grapheme cluster.
            last_py = 0
            tokens = list(re.finditer(r"\S+", clean_text))
            power_idx = {
                k for a, b, _ in find_triggers([m.group(0) for m in tokens], digits=True) for k in range(a, b + 1)
            }
            for t_i, wm in enumerate(tokens):
                if t_i in power_idx:
                    if wm.start() > last_py:
                        s_u16 = utf16_len(clean_text[:last_py])
                        e_u16 = utf16_len(clean_text[: wm.start()])
                        segments.append((s_u16, e_u16, "white"))
                    col = "red"
                    s_u16 = utf16_len(clean_text[: wm.start()])
                    e_u16 = utf16_len(clean_text[: wm.end()])
                    segments.append((s_u16, e_u16, col))
                    last_py = wm.end()
            if last_py < len(clean_text):
                segments.append((utf16_len(clean_text[:last_py]), utf16_len(clean_text), "white"))
        if not segments:
            segments = [(0, utf16_len(clean_text), "white")]

    styles_out = []
    primary_hex = "#FFFFFF"
    cmap = colour_map or {}
    for r0, r1, col_key in segments:
        if r1 <= r0:
            continue
        col_key = cmap.get(col_key, cmap.get("*", col_key))
        rgb, hex_col = NEON_PALETTE.get(col_key, NEON_PALETTE["white"])
        if col_key != "white" and primary_hex == "#FFFFFF":
            primary_hex = hex_col
        st = copy.deepcopy(base_style)
        st["range"] = [r0, r1]
        st["size"] = font_sz
        st["bold"] = True
        st["useLetterColor"] = True
        st["fill"] = {
            "alpha": 1.0,
            "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": rgb}},
        }
        st["strokes"] = [
            {
                "alpha": 1.0,
                "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": stroke_rgb or [0.04, 0.04, 0.07]}},
                "width": stroke_w,
                "mode": 0,
            }
        ]
        if font_path:
            st["font"] = {"path": font_path, "id": font_id}
        # Exact CapCut crisp drop shadow (diffuse=0.025, NEVER > 0.15!), or soft colored glow when
        # TextBloom shader is not cached on the user's PC (neon-glow / outline-glow).
        if glow_fallback_rgb:
            st["shadows"] = [
                {
                    "thickness_projection_angle": 0,
                    "thickness_projection_enable": False,
                    "diffuse": 0.14,
                    "alpha": 0.90,
                    "distance": 0.0,
                    "content": {"render_type": "solid", "solid": {"color": glow_fallback_rgb}},
                    "angle": 0.0,
                    "thickness_projection_distance": 0,
                }
            ]
        else:
            st["shadows"] = [
                {
                    "thickness_projection_angle": -45,
                    "thickness_projection_enable": False,
                    "diffuse": 0.025,
                    "alpha": 0.88,
                    "distance": 4.5,
                    "content": {"render_type": "solid", "solid": {"color": [0.0, 0.0, 0.0]}},
                    "angle": -45.0,
                    "thickness_projection_distance": 0,
                }
            ]
        styles_out.append(st)

    return clean_text, styles_out, primary_hex


def make_text_glow_effect(
    glow_preset: str, glow_hex: str, assets: dict, strength: float = None, rng: float = None
) -> dict:
    """
    Create a REAL CapCut Desktop `panel_id: "text_glow"` effect entry for `materials["effects"]`
    (verified from user drafts `0926`, `1029 (11)`, `B-rolls edit 1`).
    """
    preset_key = glow_preset if glow_preset in GLOW_PRESETS else "neon"
    pinfo = GLOW_PRESETS[preset_key]
    shader_path = assets.get("glows", {}).get(preset_key, "")
    if not shader_path:
        return None
    return {
        "id": new_id(),
        "effect_id": pinfo["effect_id"],
        "resource_id": pinfo["effect_id"],
        "third_resource_id": "",
        "name": pinfo["name"],
        "report_name": "",
        "type": "bloom",
        "sub_type": "none",
        "path": shader_path,
        "value": 0.0,
        "visible": True,
        "item_effect_type": 0,
        "category_id": "",
        "category_name": "",
        "category_key": "",
        "sub_category_id": "",
        "sub_category_name": "",
        "platform": "all",
        "apply_target_type": 0,
        "source_platform": 1,
        "version": "",
        "adjust_params": [],
        "time_range": None,
        "formula_id": "",
        "enable_skin_tone_correction": False,
        "algorithm_artifact_path": "",
        "intensity_key": "",
        "face_adjust_params": [],
        "exclusion_group": [],
        "panel_id": "text_glow",
        "bloom_params": {
            "color": glow_hex or "#FFFFFF",
            "strength": float(strength if strength is not None else pinfo["strength"]),
            "range": float(rng if rng is not None else pinfo["range"]),
            "dir_x": 0.5,
            "dir_y": 0.5,
        },
        "request_id": "",
        "color_match_info": None,
        "multi_language_current": "",
        "lumi_hub_path": "",
        "covering_relation_change": 0,
        "beauty_face_auto_preset_id": "",
        "beauty_body_auto_preset_id": "",
        "beauty_face_auto_retouch_info": {
            "face_id": [],
            "beauty_face_auto_retouch_id": "",
        },
        "smart_color_mode": 0,
        "is_from_intelligent_quality": False,
    }


def _kf_track(prop: str, points: list, curve: str = "FreeCurveInOut") -> dict:
    """One CapCut keyframe track from [(time_offset_us, value), ...]. Bezier handles are clamped to 1/3 of each
    adjacent span so handles never cross over on short captions."""
    kl = []
    for i, (t, v) in enumerate(points):
        if curve == "Line":
            lx, rx = 0.0, 0.0
        else:
            dt_l = (t - points[i - 1][0]) if i > 0 else 0
            dt_r = (points[i + 1][0] - t) if i + 1 < len(points) else 0
            lx = -round(dt_l * 0.33, 1) if dt_l > 0 else 0.0
            rx = round(dt_r * 0.33, 1) if dt_r > 0 else 0.0
        kl.append(
            {
                "id": new_id(),
                "curveType": curve,
                "time_offset": int(t),
                "left_control": {"x": lx, "y": 0.0},
                "right_control": {"x": rx, "y": 0.0},
                "values": [round(float(v), 4)],
                "string_value": "",
                "graphID": "",
            }
        )
    return {"id": new_id(), "material_id": "", "property_type": prop, "keyframe_list": kl}


def make_pop_bounce_keyframes(
    dur_us: int, base_scale: float = 1.0, anim_style: str = "pop-bounce", base_y: float = 0.0, base_x: float = 0.0
) -> list:
    """
    Generate native CapCut `common_keyframes` (`KFTypeScaleX`, `KFTypeScaleY`, `KFTypePositionY`)
    with clean, proportional Bezier handles (never crossing over on short captions) and 0 external cache dependency.
    """
    if not anim_style or anim_style == "none" or dur_us < 160000:
        return []
    t1 = min(dur_us // 3, 75000)  # 75ms gentle overshoot
    t2 = min(dur_us // 2, 130000)  # 130ms settle
    if anim_style == "shake":  # crisp +-5 px micro-shake (Line curve avoids Bezier overshoot)
        d = 5 / 540
        step = min(35000, (dur_us - 10000) // 3)
        pts = [(0, base_x + d), (step, base_x - d), (step * 2, base_x + d / 2), (step * 3, base_x)]
        return [_kf_track("KFTypePositionX", pts, curve="Line")]
    if anim_style == "bounce":  # subtle rise 18 px from below, 4 px overshoot, settle
        mid = min(dur_us // 3, 85000)
        end = min(dur_us - 10000, max(t2, mid + 45000))
        pts = [(0, base_y - 18 / 960), (mid, base_y + 4 / 960), (end, base_y)]
        return [_kf_track("KFTypePositionY", pts)]
    if anim_style == "burst":
        anim_style = "pop-bounce"
    kfs = []
    if anim_style in ("pop-bounce", "slide-up-pop"):
        scale_pts = [(0, base_scale * 0.90), (t1, base_scale * 1.04), (t2, base_scale)]
        for prop in ("KFTypeScaleX", "KFTypeScaleY"):
            kfs.append(_kf_track(prop, scale_pts))
    if anim_style == "slide-up-pop":
        kfs.append(_kf_track("KFTypePositionY", [(0, base_y - 0.03), (t2, base_y)]))
    elif anim_style == "punch-zoom":
        # Smooth camera punch-in zoom over the segment
        zoom_t = min(dur_us - 33333, 220000)
        zoom_pts = [(0, base_scale), (zoom_t, base_scale * 1.16)]
        for prop in ("KFTypeScaleX", "KFTypeScaleY"):
            kfs.append(_kf_track(prop, zoom_pts))
    return kfs


_CAPTION_STRIP = re.compile(r"[^\w\u0900-\u097F!?'%$-]")

_LEGACY_MODES = {"hormozi-pop": "bold-pop", "karaoke-word": "karaoke-highlight", "two-line-stack": "two-line-stack"}


def capcut_fields(st: CaptionStyle, active: str, font: str = "", emoji: bool = False) -> dict:
    """Per-caption CapCut text settings for a style (font, stroke, box, colours, tilt, glow fallback, emoji)."""
    f = {"font": font or st.font, "stroke_width": round(st.outline * 0.07 / 6, 3), "glow": st.glow}
    cmap = {}
    if st.primary != "white":
        cmap["white"] = st.primary
    if st.box:  # CapCut has no per-word box: the whole caption sits on the box, the spoken word in red
        f["background"] = NEON_PALETTE.get(active, NEON_PALETTE["yellow"])[1]
        cmap.update({"white": "black", active: "red"})
    if st.anim == "gradient":
        f["stroke_rgb"] = NEON_PALETTE["pink"][0]
        f["stroke_width"] = max(f["stroke_width"], 0.10)
    if st.anim == "neon":
        f["glow_fallback_color"] = active
    if st.anim == "burst":
        f["rotation"] = -4.0
    if cmap:
        f["colour_map"] = cmap
    if emoji:
        f["emoji"] = True
    return f


def hook_capcut_fields(hook_style: str) -> dict:
    """CapCut look of the hook banner styles (bold-box, outline-glow, top-bar)."""
    if hook_style == "top-bar":
        return {
            "font": "anton",
            "glow": False,
            "background": NEON_PALETTE["red"][1],
            "background_round_radius": 0.0,
            "colour_map": {"red": "yellow"},
            "line_max_width": 1.0,
        }
    if hook_style == "outline-glow":
        return {
            "font": "anton",
            "glow": True,
            "glow_preset": "neon",
            "glow_color": "yellow",
            "glow_fallback_color": "yellow",
        }
    return {
        "font": "anton",
        "glow": False,
        "background": NEON_PALETTE["yellow"][1],
        "background_round_radius": 0.08,
        "colour_map": {"white": "black", "yellow": "red"},
    }


MIN_WORD_SLOT = 0.12  # a word on screen for less than this (s) cannot be read: it joins the next word's caption
_MERGE_MAX_CHARS = 14
_MERGE_MAX_CHARS_SAME = 28  # words at one instant: longer token, the caption wraps at its spaces
_SAME_INSTANT = 0.04


def _merge_short_words(words: list) -> list:
    """
    Words whose caption slot (until the next word starts) is shorter than MIN_WORD_SLOT join the next word
    ("I" 5.45 + "got" 5.49 -> "I got"), so 1-word styles never drop them. Only adjacent words (gap < 0.18 s)
    are joined, up to 14 characters per caption token.
    """
    out = []
    i = 0
    while i < len(words):
        cur = dict(words[i])
        while i + 1 < len(words):
            nxt = words[i + 1]
            short = nxt["rel_s"] - cur["rel_s"] < MIN_WORD_SLOT and nxt["rel_s"] - cur["rel_e"] < 0.18
            # words Whisper put at the same instant ("we" "are" "heading" "to" all at 17.88) must share one
            # caption: a split there gives a caption of zero length that is never shown
            same = nxt["rel_s"] - cur["rel_s"] < _SAME_INSTANT
            limit = _MERGE_MAX_CHARS_SAME if same else _MERGE_MAX_CHARS
            if not short or len(cur["w"]) + 1 + len(nxt["w"]) > limit:
                break
            em = nxt.get("emoji") or cur.get("emoji")
            cur = {
                "w": f"{cur['w']} {nxt['w']}",
                "rel_s": cur["rel_s"],
                "rel_e": max(cur["rel_e"], nxt["rel_e"]),
                "hype": cur["hype"] or nxt["hype"],
                "masked": cur["masked"] or nxt["masked"],
            }
            if em:
                cur["emoji"] = em
            i += 1
        out.append(cur)
        i += 1
    return out


def _plain(text: str) -> str:
    t = re.sub(r"\{[a-z]+:([^{}]+)\}", r"\1", text)
    return t


def build_word_synced_captions(
    words: list,
    seg_src_start: float,
    seg_dur_s: float,
    tl_offset_s: float,
    caption_mode: str = "hormozi-pop",
    pos_y: float = 0.18,
    font_size: float = 11.0,
    hindi_script: str = "devanagari",
    style: str = "",
    case: str = "",
    words_per: int = 0,
    emoji=None,
    profanity: bool = False,
    highlight: str = "",
    font: str = "",
) -> list:
    """
    Word timestamps ([{"w", "s", "e"}], source seconds) -> word-synced captions in a caption style
    (captions.styles.STYLES). One caption per spoken word: the group of the active word with the active
    word coloured (style colour or --highlight-color) and hype/trigger words red.
    caption_mode is the old name (hormozi-pop, karaoke-word, two-line-stack), used when style is empty.
    case: upper|title|as-is on Latin letters; words_per: 1-4 words per caption (0 = the style's);
    emoji: keyword emoji on/off (None = the style's); profanity: mask common swear words as f***.
    hindi_script 'devanagari' keeps Hindi as transcribed (Baloo 2 is picked for it); 'roman' romanises.
    """
    if not words:
        return []
    st = get_style(style_name(style) or _LEGACY_MODES.get(caption_mode, "bold-pop"))
    case = case or st.case
    n_max = max(1, min(4, int(words_per or st.words)))
    use_emoji = st.emoji if emoji is None else bool(emoji)
    active_col = highlight or st.active
    clean_words = []
    for w in words:
        raw = str(w.get("w", ""))
        if hindi_script == "roman":
            txt = devanagari_to_hinglish(raw, strip_emojis=True).strip()
            txt = re.sub(r"[^A-Za-z0-9 !?'%$-]", "", txt)
            txt = re.sub(r"\s+", " ", txt).strip()
        else:
            txt = _CAPTION_STRIP.sub("", raw.replace("_", "")).strip()
        if not txt:
            continue
        masked = False
        if profanity:
            m = mask_profanity(txt)
            masked, txt = m != txt, m
        txt = apply_case(txt, case)
        rel_s = max(0.0, float(w["s"]) - seg_src_start)
        rel_e = min(seg_dur_s, float(w["e"]) - seg_src_start + 0.04)
        # Whisper gives some words zero length ("to" 21.36-21.36): keep every word that starts inside the piece
        if rel_s < seg_dur_s - 0.02 and float(w["e"]) >= float(w["s"]):
            clean_words.append({"w": txt, "rel_s": rel_s, "rel_e": max(rel_e, rel_s), "hype": False, "masked": masked})
    if not clean_words:
        return []
    for a, b, _ in find_triggers([x["w"] for x in clean_words], digits=True):
        for k in range(a, b + 1):
            clean_words[k]["hype"] = True
    if use_emoji:
        for k, em in emoji_positions([x["w"] for x in clean_words]).items():
            clean_words[k]["emoji"] = em
    clean_words = _merge_short_words(clean_words)

    def colour(gw, active: bool) -> str:
        if st.box and active:
            return active_col
        return st.hype if gw["hype"] else (active_col if active else "white")

    def tag(gw, col: str) -> str:
        if col == "white":
            out = f"{{white:{gw['w']}}}" if gw["masked"] else gw["w"]  # keeps f*** away from *WORD* markup
        else:
            out = f"{{{col}:{gw['w']}}}"
        return out + (f" {gw['emoji']}" if gw.get("emoji") else "")

    stack = st.anim == "stack"
    if stack:
        n = int(words_per or 4)
        groups = [clean_words[i : i + n] for i in range(0, len(clean_words), n)]
        glow_preset, end_pad = "deep", 0.32
    else:
        max_chars = {1: 99, 2: 11, 3: 15, 4: 20}[n_max]
        groups, i = [], 0
        while i < len(clean_words):
            grp = [clean_words[i]]
            while len(grp) < n_max and i + len(grp) < len(clean_words):
                nxt = clean_words[i + len(grp)]
                if sum(len(x["w"]) + 1 for x in grp) + len(nxt["w"]) > max_chars:
                    break
                if n_max <= 2 and nxt["rel_s"] - grp[-1]["rel_e"] >= 0.18:
                    break
                grp.append(nxt)
            groups.append(grp)
            i += len(grp)
        glow_preset, end_pad = "neon", 0.24

    fields = capcut_fields(st, active_col, font, use_emoji)
    size = font_size * st.size / 84.0
    out_caps = []
    for g_i, grp in enumerate(groups):
        g_end = min(seg_dur_s, max(grp[-1]["rel_e"], grp[0]["rel_s"] + end_pad))
        if g_i + 1 < len(groups):
            g_end = min(g_end, groups[g_i + 1][0]["rel_s"])
        mid = len(grp) // 2
        for a_i, aw in enumerate(grp):
            w_start = aw["rel_s"]
            w_end = grp[a_i + 1]["rel_s"] if a_i + 1 < len(grp) else g_end
            w_end = min(seg_dur_s, w_end)
            if w_end <= w_start + 0.01:  # nothing to show (short words were joined to their neighbour above)
                continue
            extra = {}
            if stack and len(grp) >= 2:
                l1 = " ".join(tag(gw, "white") for gw in grp[:mid])
                l2 = " ".join(tag(gw, colour(gw, j + mid == a_i)) for j, gw in enumerate(grp[mid:]))
                text = f"{l1}\n{l2}"
            elif st.anim == "typewriter":
                toks = [tag(gw, colour(gw, j == a_i)) for j, gw in enumerate(grp)]
                text = " ".join(toks[: a_i + 1])
                extra = {"full": " ".join(toks), "reveal": len(text.split())}
            else:
                text = " ".join(tag(gw, colour(gw, j == a_i)) for j, gw in enumerate(grp))
            cap = {
                "text": text,
                "tl_start": round(tl_offset_s + w_start, 3),
                "tl_end": round(tl_offset_s + w_end, 3),
                "font_size": round(size + (1.0 if st.name == "bold-pop" and len(grp) == 1 else 0.0), 3),
                "pos_y": pos_y,
                "glow_preset": glow_preset,
                "glow_color": colour(aw, True),
                "anim": st.capcut_anim if a_i == 0 else "none",
                **fields,
                **extra,
            }
            out_caps.append(cap)
            if st.anim == "glitch" and a_i == 0:
                g_e = round(min(tl_offset_s + w_end, tl_offset_s + w_start + 0.15), 3)
                for layer, dx, col in ((2, -4, "cyan"), (3, 4, "pink")):
                    out_caps.append(
                        dict(
                            cap,
                            text=_plain(text),
                            tl_end=g_e,
                            layer=layer,
                            pos_x=round(dx / 540, 4),
                            colour_map={"*": col},
                            glow=False,
                            anim="none",
                            capcut_only=True,
                        )
                    )
    return out_caps
