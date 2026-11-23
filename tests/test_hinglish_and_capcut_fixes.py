"""Regression tests for Hinglish captions, CapCut font metadata, clean keyframes and live CLI wiring."""

import json
from types import SimpleNamespace

import pytest

from streamclipper.captions.capcut import (
    build_word_synced_captions,
    devanagari_to_hinglish,
    make_pop_bounce_keyframes,
)
from streamclipper.cli import build_parser
from streamclipper.clipplan import plan_clip_spec
from streamclipper.colab import _clip_argv
from streamclipper.dashboard.jobs import build_live_argv, build_vod_argv, validate_options
from streamclipper.draft import writer
from streamclipper.options import resolve_options


def test_devanagari_to_hinglish_handles_anusvara_chandra_digits_and_newlines():
    assert devanagari_to_hinglish("बंदा") == "banda"
    assert devanagari_to_hinglish("जंगल") == "jangal"
    assert devanagari_to_hinglish("रंग") == "rang"
    assert devanagari_to_hinglish("पंगा") == "panga"
    assert devanagari_to_hinglish("मैं") == "main"
    assert devanagari_to_hinglish("बॉस") == "boss"
    assert devanagari_to_hinglish("शॉट") == "shot"
    assert devanagari_to_hinglish("१२३") == "123"
    assert devanagari_to_hinglish("भाई\nक्या सीन") == "bhai\nkya scene"
    assert devanagari_to_hinglish("नहीं भाई क्यों") == "nahi bhai kyun"


def test_devanagari_to_hinglish_transliterates_urdu_whisper_output():
    out = devanagari_to_hinglish("بھائی کیا کر رہے ہو")
    assert out and all(ord(c) < 128 for c in out)
    assert "bh" in out and "k" in out


def test_build_word_synced_captions_roman_preserves_multiword_spaces():
    words = [{"w": "क्या हुआ", "s": 0.2, "e": 0.8}, {"w": "बंदा", "s": 0.9, "e": 1.3}]
    caps = build_word_synced_captions(words, 0.0, 2.0, 0.0, style="bold-pop", hindi_script="roman")
    joined = " ".join(c["text"] for c in caps)
    assert "KYA HUA" in joined
    assert "BANDA" in joined


@pytest.mark.parametrize("cmd", ["live", "clip-stream", "build", "render"])
def test_cli_accepts_hindi_script_and_hinglish_alias(cmd):
    parser = build_parser()
    base = {
        "live": ["live", "--url", "https://twitch.tv/x", "--out", "o"],
        "clip-stream": ["clip-stream", "--input", "a.mp4", "--out", "o"],
        "build": ["build", "--spec", "s.json"],
        "render": ["render", "--spec", "s.json"],
    }[cmd]
    args_roman = parser.parse_args(base + ["--hindi-script", "roman"])
    assert args_roman.hindi_script == "roman"
    args_hinglish = parser.parse_args(base + ["--hindi-script", "hinglish"])
    assert resolve_options(args_hinglish, {})["hindi_script"] == "roman"
    # Default CLI args must NOT override hindi_script set inside a spec JSON.
    args_default = parser.parse_args(base)
    assert args_default.hindi_script is None
    assert resolve_options(args_default, {"hindi_script": "roman"})["hindi_script"] == "roman"


def test_plan_clip_spec_romanizes_hook_when_hinglish():
    panels = {
        "main_src": "main.mp4",
        "main_pos_y": 0.0,
        "layout": "full",
        "caption_y": -0.2,
        "banner_y": 0.5,
    }
    segs = [{"cut_start": 0.0, "cut_end": 3.0, "words": [{"w": "बंदा", "s": 0.5, "e": 1.0}]}]
    opts = resolve_options(SimpleNamespace(hindi_script="hinglish", caption_style="bold-pop"), {})
    spec = plan_clip_spec("Hinglish_01", panels, segs, 0.0, opts, {"sfx": {}}, hook_text="अरे भाई क्या सीन")
    assert spec["caption_style"]["hindi_script"] == "roman"
    hook_caps = [c for c in spec["captions"] if c.get("layer") == 1]
    assert hook_caps and all(ord(ch) < 128 for ch in hook_caps[0]["text"])
    assert "BHAI" in hook_caps[0]["text"] and "SCENE" in hook_caps[0]["text"]


@pytest.mark.parametrize("anim", ["pop-bounce", "slide-up-pop", "shake", "bounce", "burst", "punch-zoom"])
@pytest.mark.parametrize("dur_us", [160000, 200000, 500000, 1500000])
def test_capcut_keyframes_never_cross_bezier_handles(anim, dur_us):
    kfs = make_pop_bounce_keyframes(dur_us, base_scale=1.0, anim_style=anim, base_y=0.1, base_x=0.0)
    assert kfs
    for track in kfs:
        kl = track["keyframe_list"]
        assert len(kl) >= 2
        for a, b in zip(kl, kl[1:], strict=False):
            dt = b["time_offset"] - a["time_offset"]
            assert dt > 0
            assert b["time_offset"] < dur_us
            assert abs(a["right_control"]["x"]) + abs(b["left_control"]["x"]) < dt
    assert make_pop_bounce_keyframes(120000, anim_style="pop-bounce") == []


def test_draft_writer_sets_real_font_metadata_and_glow_flag(tmp_path, monkeypatch):
    draft_root = tmp_path / "drafts"
    draft_root.mkdir()
    monkeypatch.setattr(writer, "find_capcut_local", lambda: tmp_path / "no_capcut")
    monkeypatch.setattr(writer, "guard_capcut", lambda dr, close=False: None)
    spec = {
        "project_name": "FontCheck_01",
        "draft_root": str(draft_root),
        "canvas_ratio": "9:16",
        "caption_style": {"font": "barlow", "hindi_script": "roman", "glow": True},
        "captions": [
            {"text": "अरे भाई", "tl_start": 0.0, "tl_end": 1.0, "font": "barlow", "glow": True},
        ],
    }
    proj_dir = writer.build_from_spec_dict(spec)
    dc = json.loads((proj_dir / "draft_content.json").read_text(encoding="utf-8"))
    tm = dc["materials"]["texts"][0]
    assert tm["font_name"] == "Barlow Condensed Black"
    assert tm["font_title"] == "Barlow Condensed Black"
    assert tm["group_id"] == ""
    assert tm["add_type"] == 0
    assert tm["recognize_text"] == ""
    assert tm["check_flag"] == 15  # no glow shader cached -> check_flag must stay 15, not 71
    content = json.loads(tm["content"])
    assert "bhai" in content["text"].lower()
    assert "BarlowCondensed-Black.ttf" in content["styles"][0]["font"]["path"]


def test_dashboard_and_colab_forward_hinglish_and_font(tmp_path):
    opts = validate_options(
        {"mode": "live", "language": "hi", "hindi_script": "hinglish", "caption_font": "barlow", "drafts": True}
    )
    live_argv = build_live_argv("https://twitch.tv/x", opts, tmp_path / "job")
    assert "--language" in live_argv and "hi" in live_argv
    assert "--hindi-script" in live_argv and "hinglish" in live_argv
    assert "--caption-font" in live_argv and "barlow" in live_argv

    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    vod_argv = build_vod_argv(str(src), opts, tmp_path / "job2")
    assert "--hindi-script" in vod_argv and "--caption-font" in vod_argv

    c_argv = _clip_argv(
        str(src),
        tmp_path / "out",
        {
            "prefix": "clip",
            "max_clips": 3,
            "min_score": 50,
            "llm": "none",
            "whisper_model": "auto",
            "device": "auto",
            "language": "hi",
            "hindi_script": "roman",
            "caption_font": "poppins",
            "render": True,
            "drafts": False,
            "resume": True,
        },
    )
    assert "--hindi-script" in c_argv and "roman" in c_argv
    assert "--caption-font" in c_argv and "poppins" in c_argv


def test_all_15_styles_and_3_hooks_render_cleanly_in_capcut_draft(tmp_path, monkeypatch):
    from streamclipper.captions.capcut import capcut_fields, hook_capcut_fields
    from streamclipper.captions.styles import HOOK_STYLES, STYLES

    draft_root = tmp_path / "drafts"
    draft_root.mkdir()
    monkeypatch.setattr(writer, "find_capcut_local", lambda: tmp_path / "no_capcut")
    monkeypatch.setattr(writer, "guard_capcut", lambda dr, close=False: None)
    caps = []
    t = 0.0
    for name, st in STYLES.items():
        caps.append({"text": f"TEST {name.upper()}", "tl_start": t, "tl_end": t + 0.5, **capcut_fields(st, st.active)})
        t += 0.6
    for hs in HOOK_STYLES:
        caps.append({"text": f"HOOK {hs.upper()}", "tl_start": t, "tl_end": t + 0.5, **hook_capcut_fields(hs)})
        t += 0.6
    proj = writer.build_from_spec_dict(
        {"project_name": "AllStyles_01", "draft_root": str(draft_root), "canvas_ratio": "9:16", "captions": caps}
    )
    dc = json.loads((proj / "draft_content.json").read_text(encoding="utf-8"))
    texts = dc["materials"]["texts"]
    assert len(texts) == len(STYLES) + len(HOOK_STYLES)
    by_name = {tm["name"]: tm for tm in texts}
    assert by_name["TEST GRADIENT-STROKE"]["border_color"] == "#FF2EC7"
    assert by_name["TEST NEON-GLOW"]["shadow_color"] == "#00F0FF"
    assert by_name["HOOK OUTLINE-GLOW"]["shadow_color"] == "#FFE600"
    assert by_name["HOOK TOP-BAR"]["background_round_radius"] == 0.0
    assert all(tm["font_name"] != "Classic" and tm["font_title"] != "Classic" for tm in texts)


def test_presets_webhooks_fillers_and_bleep():
    from streamclipper.captions.styles import is_profane, mask_profanity
    from streamclipper.fillers import filler_spans
    from streamclipper.live.notify import build_alert_request, send_clip_alert
    from streamclipper.render import bleep_spans
    from streamclipper.story import suggest_hook

    # Presets
    h_opts = resolve_options(SimpleNamespace(preset="indian-streamer-9x16"), {})
    assert h_opts["hindi_script"] == "roman" and h_opts["caption_style"] == "bold-pop"
    g_opts = resolve_options(SimpleNamespace(preset="viral-streamer-9x16"), {})
    assert g_opts["caption_style"] == "bold-pop" and g_opts["font"] == "barlow"

    # Hinglish fillers
    words = [
        {"w": "bhai", "s": 0.0, "e": 0.3},
        {"w": "matlab,", "s": 0.5, "e": 0.8},
        {"w": "kya", "s": 1.0, "e": 1.2},
        {"w": "iska", "s": 2.0, "e": 2.2},
        {"w": "matlab", "s": 2.25, "e": 2.5},
        {"w": "kya", "s": 2.55, "e": 2.8},
    ]
    spans = filler_spans(words)
    assert len(spans) == 1 and spans[0][2] == "matlab,"

    # Hinglish category hook fallback
    hook = suggest_hook(
        [{"w": "bhai", "s": 1.0, "e": 1.2}, {"w": "yaar", "s": 1.3, "e": 1.5}],
        {"start": 0.0, "end": 10.0, "peak": 5.0},
        category="clutch",
    )
    assert hook in ("INSANE CLUTCH BHAI", "NO WAY YE HO GAYA", "CLUTCH OF THE STREAM")

    # Webhooks (Discord + Telegram + Generic)
    entry = {
        "rank": 1,
        "score": 82,
        "category": "clutch",
        "peak": 125.0,
        "title": "Insane clutch",
        "hook_headline": "NO WAY",
    }
    d_url, d_body = build_alert_request(
        "https://discord.com/api/webhooks/1/abc", entry, channel="shroud", where="clip.mp4"
    )
    assert "discord.com" in d_url and d_body["embeds"][0]["fields"][0]["value"] == "82"
    t_url, t_body = build_alert_request("telegram:12345:999", entry, channel="scout")
    assert t_url == "https://api.telegram.org/bot12345/sendMessage" and t_body["chat_id"] == "999"
    sent = []
    assert send_clip_alert("https://example.com/hook", entry, post_fn=lambda u, p: sent.append((u, p)))
    assert sent[0][1]["event"] == "clip_ready"

    # Profanity mask & audio bleep spans (English + Hinglish)
    assert is_profane("fucking") and is_profane("chutiya") and not is_profane("clutch")
    assert mask_profanity("chutiya") == "c******"
    plan = {
        "caption_opts": {"profanity_filter": True},
        "segments": [{"src_s": 0.0, "src_e": 5.0, "tl_start": 0.0, "kind": "story"}],
        "words": [
            {"w": "bro", "s": 0.5, "e": 0.8},
            {"w": "chutiya", "s": 1.0, "e": 1.4},
            {"w": "fuck", "s": 2.0, "e": 2.3},
        ],
    }
    bs = bleep_spans(plan)
    assert len(bs) == 2 and bs[0][0] == 0.98 and bs[1][0] == 1.98

