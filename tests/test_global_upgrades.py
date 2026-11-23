"""Tests for BGM auto-ducking, multi-language translation, pacing presets and dashboard batch actions."""

import io
import json
import shutil
import subprocess
import zipfile
from types import SimpleNamespace

import pytest

from dashboard_util import start, stop
from streamclipper.cli import build_parser
from streamclipper.clipplan import plan_clip_spec
from streamclipper.dashboard import library
from streamclipper.dashboard.jobs import build_live_argv, build_vod_argv, validate_options
from streamclipper.llm import translate_words
from streamclipper.options import pace_min_pause, resolve_bgm, resolve_options
from streamclipper.render import _finish_audio_mix
from streamclipper.util import ToolError, find_triggers


def test_resolve_bgm_file_folder_and_errors(tmp_path):
    f1 = tmp_path / "a.mp3"
    f1.write_bytes(b"mp3")
    assert resolve_bgm(str(f1)) == str(f1.resolve())

    mdir = tmp_path / "music"
    mdir.mkdir()
    with pytest.raises(ToolError, match="No audio files"):
        resolve_bgm(str(mdir))
    (mdir / "01_chill.mp3").write_bytes(b"a")
    (mdir / "02_hype.wav").write_bytes(b"b")
    pick1 = resolve_bgm(str(mdir), seed="clip_01")
    assert pick1 == resolve_bgm(str(mdir), seed="clip_01")  # deterministic
    assert pick1.endswith((".mp3", ".wav"))

    with pytest.raises(ToolError, match="not found"):
        resolve_bgm(str(tmp_path / "nope.mp3"))


def test_bgm_capcut_audio_tracks_and_ffmpeg_sidechain_graph(tmp_path):
    bgm = tmp_path / "track.wav"
    if shutil.which("ffmpeg"):
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=220:d=2", str(bgm)],
            check=True,
        )
    else:
        bgm.write_bytes(b"wav")
    opts = resolve_options(SimpleNamespace(bgm=str(bgm), bgm_volume=0.18, caption_style="bold-pop"), {})
    panels = {"main_src": "main.mp4", "main_pos_y": 0.0, "layout": "full", "caption_y": -0.2, "banner_y": 0.5}
    segs = [{"cut_start": 0.0, "cut_end": 5.0, "words": [{"w": "hello", "s": 0.5, "e": 1.0}]}]
    spec = plan_clip_spec("BgmClip_01", panels, segs, 0.0, opts, {"sfx": {}})
    assert spec["audio_tracks"]
    assert spec["audio_tracks"][0]["volume"] == 0.18
    assert spec["audio_tracks"][0]["audio_type"] == "extract_music"

    # Verify FFmpeg accepts the exact sidechaincompress + bleep + amix filter graph!
    lines, out_label = _finish_audio_mix("[0:a]", [], [(0.5, 0.8)], 1, 0.16, 2.0)
    assert out_label == "[abgm]"
    assert any("sidechaincompress" in ln for ln in lines)
    if shutil.which("ffmpeg"):
        graph = ";".join(lines)
        res = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=f=440:d=2",
                "-f",
                "lavfi",
                "-i",
                "sine=f=220:d=2",
                "-filter_complex",
                graph,
                "-map",
                out_label,
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, res.stderr


def test_translate_words_preserves_timing_and_global_triggers():
    words = [
        {"w": "भाई", "s": 0.0, "e": 0.3},
        {"w": "क्या", "s": 0.32, "e": 0.6},
        {"w": "शॉट", "s": 0.62, "e": 1.0},
        {"w": "लगा", "s": 2.0, "e": 2.5},
    ]

    def fake_llm(_prov, _model, _prompt):
        return json.dumps({"lines": ["BRO WHAT A SHOT", "IT LANDED"], "hook": "INSANE SHOT"})

    t_words, t_hook = translate_words(words, "en", "gemini", hook="अरे भाई", call_fn=fake_llm)
    assert t_hook == "INSANE SHOT"
    assert [w["w"] for w in t_words] == ["BRO", "WHAT", "A", "SHOT", "IT", "LANDED"]
    assert t_words[0]["s"] == 0.0 and t_words[3]["e"] == 1.0
    assert t_words[4]["s"] == 2.0 and t_words[-1]["e"] == 2.5

    # Global multilingual viral power words
    hits = {h[2] for h in find_triggers(["vamos", "bro", "caralho", "incroyable", "krank", "gila"])}
    assert {"vamos", "caralho", "incroyable", "krank", "gila"} <= hits


def test_pacing_presets_cli_and_dashboard_argv(tmp_path):
    assert pace_min_pause("fast") == 0.28
    assert pace_min_pause("normal") == 0.55
    assert pace_min_pause("chill") == 0.95
    assert pace_min_pause("", fallback=0.42) == 0.42

    parser = build_parser()
    args = parser.parse_args(
        ["live", "--url", "https://twitch.tv/x", "--out", "o", "--pace", "fast", "--translate-to", "es"]
    )
    assert args.pace == "fast" and args.translate_to == "es"

    opts = validate_options({"mode": "live", "pace": "fast", "translate_to": "es", "language": "hi"})
    live_argv = build_live_argv("https://twitch.tv/x", opts, tmp_path / "j1")
    assert "--pace" in live_argv and "fast" in live_argv
    assert "--translate-to" in live_argv and "es" in live_argv

    src = tmp_path / "v.mp4"
    src.write_bytes(b"v")
    vod_argv = build_vod_argv(str(src), opts, tmp_path / "j2")
    assert "--pace" in vod_argv and "--translate-to" in vod_argv


def test_dashboard_batch_zip_and_import_all_capcut(tmp_path, monkeypatch):
    d = start(tmp_path, monkeypatch)
    try:
        # Check new options exposed by /api/options
        st, opt_data = d.json("GET", "/api/options")
        assert st == 200
        assert any(p[0] == "fast" for p in opt_data["paces"])
        assert any(t[0] == "en" for t in opt_data["translate_langs"])

        # Seed a finished job with 2 clips (MP4s + CapCut draft folders)
        job = d.srv.store.create("vod", "Agency_Stream", ["clip-stream"], {"mode": "vod", "drafts": True})
        jid = job["id"]
        out = d.srv.store.job_dir(jid) / "out"
        out.mkdir(parents=True)
        (out / "clip_01.mp4").write_bytes(b"MP4DATA1")
        (out / "clip_02.mp4").write_bytes(b"MP4DATA2")
        (out / "clips.csv").write_text("rank,title\n1,One\n2,Two\n", encoding="utf-8")
        clips_doc = {
            "clips": [
                {"rank": 1, "mp4": "clip_01.mp4", "title": "One", "score": 80, "start": 0, "end": 10, "duration": 10},
                {"rank": 2, "mp4": "clip_02.mp4", "title": "Two", "score": 75, "start": 20, "end": 30, "duration": 10},
            ]
        }
        (out / "clips.json").write_text(json.dumps(clips_doc), encoding="utf-8")
        for key in ("clip_01", "clip_02"):
            (out / f"{key}.clip.json").write_text(json.dumps({"name": key, "draft": key}), encoding="utf-8")
            ddir = d.srv.store.job_dir(jid) / "capcut_drafts" / key
            ddir.mkdir(parents=True)
            (ddir / "draft_content.json").write_text("{}", encoding="utf-8")

        # 1. Batch Download ZIP
        st, hdrs, raw_zip = d.req("GET", f"/api/jobs/{jid}/download-zip")
        assert st == 200
        assert "zip" in hdrs.get("Content-Type", "")
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            names = set(zf.namelist())
            assert {"clip_01.mp4", "clip_02.mp4", "clips.csv", "clips.json"} <= names

        # 2. Batch Import All to CapCut
        imported = []
        monkeypatch.setattr(
            library,
            "open_in_capcut",
            lambda store, job_id, key, draft_root: imported.append(key) or f"{key}_imported",
        )
        st, body = d.json("POST", f"/api/jobs/{jid}/import-all-capcut", {})
        assert st == 200
        assert body["count"] == 2
        assert imported == ["clip_01", "clip_02"]
    finally:
        stop(d)
