import json
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from purffle_shorts import ffmpeg
from purffle_shorts.pipeline import Studio
from purffle_shorts.script import Script, script_from_data
from purffle_shorts.threads import ThreadsScreen

from .conftest import needs_ffmpeg


def thread_script(settings):
    return script_from_data({
        "style": "threads", "title": "Post and replies", "language": "vi",
        "scenes": [
            {"narration": "Hôm nay mình thử sống chậm lại.", "speaker": "B",
             "thread": {"author": "author", "kind": "post", "likes": "1.4K"}},
            {"narration": "Mình cũng muốn thử như vậy.",
             "thread": {"author": "reader", "kind": "comment", "replies": 0}},
        ],
    }, settings)


def test_import_keeps_threads_metadata_and_all_card_boundaries(settings):
    raw = thread_script(settings).to_dict()
    raw["scenes"] *= 8
    script = script_from_data(raw, settings)
    assert script.style == "threads" and len(script.scenes) == 16
    assert script.scenes[0].thread["likes"] == "1.4K"
    assert Script.from_dict(script.to_dict()) == script
    assert not script.multi_speaker


def test_long_post_paginates_without_losing_text_or_author(settings, tmp_path):
    screen = ThreadsScreen(settings, Path(settings.caption_font))
    script = thread_script(settings)
    script.scenes[0].narration = "Một câu chuyện rất dài với đầy đủ dấu tiếng Việt. " * 130
    pages = screen.pages(script.scenes)
    post_pages = [p for p in pages if p.scene == 0]
    assert len(post_pages) > 1
    assert " ".join(" ".join(p.text.split()) for p in post_pages) == " ".join(script.scenes[0].narration.split())
    assert all(p.meta["author"] == "author" for p in post_pages)
    assert all(len(p.text.splitlines()) <= screen.max_lines for p in pages)
    playlist = screen.build(script.scenes, [0, 12], 18, tmp_path)
    durations = [float(line.split()[1]) for line in playlist.read_text().splitlines() if line.startswith("duration")]
    assert abs(sum(durations)-18) < .0001
    assert abs(sum(durations[:-1])-12) < .0001
    assert screen.render(pages[0]).getbbox() is not None


def test_threads_cards_hide_identity_without_leaking_name_length(settings):
    screen = ThreadsScreen(settings, Path(settings.caption_font))
    script = thread_script(settings)
    for page in screen.pages(script.scenes):
        original = screen.render(page)
        page.meta.update(author="a_much_longer_username", avatar="private-avatar.jpg")
        assert screen.render(page).tobytes() == original.tobytes()


def test_threads_card_uses_black_background_and_scaled_dimensions(settings):
    settings = settings.with_overrides(resolution=(1080, 1920))
    screen = ThreadsScreen(settings, Path(settings.caption_font))
    assert (screen.width, screen.font.size, screen.radius) == (810, 30, 31)
    for page in screen.pages(thread_script(settings).scenes):
        image = screen.render(page)
        _, top, _, bottom = image.getbbox()
        assert abs(screen.y + (top + bottom) / 2 - 672) <= 1
        assert image.getpixel((0, top))[3] == 0
        assert image.getpixel((screen.width - 1, bottom - 1))[3] == 0
        assert image.getpixel((screen.width // 2, bottom - 3)) == (0, 0, 0, 255)
        avatar_center = (screen.pad + screen.avatar // 2, top + screen.pad + screen.avatar // 2)
        assert image.getpixel(avatar_center) == (60, 64, 70, 255)


def test_threads_action_row_displays_all_four_counts(settings):
    settings = settings.with_overrides(resolution=(360, 640))
    screen = ThreadsScreen(settings, Path(settings.caption_font))
    pages = screen.pages(thread_script(settings).scenes)
    for page in pages:
        page.meta.update(likes="1,7K", replies=0, reposts=None, shares=405)
        draw = Mock()
        screen._actions(draw, page, screen._text_left(page.meta["kind"] == "comment"), 0)
        assert [call.args[1] for call in draw.text.call_args_list] == ["1,7K", "0", " ", "405"]


@needs_ffmpeg
def test_threads_render_from_json_skips_ai_and_uses_timed_cards(settings, monkeypatch):
    from purffle_shorts import pipeline
    calls = []
    real = pipeline.build_overlays

    def overlays(*args, **kwargs):
        plan = real(*args, **kwargs)
        calls.append(plan)
        return plan

    def no_draft(*args, **kwargs):
        raise AssertionError("Rendering script.json must not ask AI to draft")

    monkeypatch.setattr(pipeline, "build_overlays", overlays)
    monkeypatch.setattr(Studio, "draft", no_draft)
    settings = settings.with_overrides(offline=True, tts_engine="silent", resolution=(360, 640),
                                       music_volume=0, fps=24, transition="none")
    result = Studio(settings).make(script=thread_script(settings), upload=False)
    assert result.ok, result.error
    assert calls[0].threads and not calls[0].captions and not calls[0].hook and not calls[0].cta
    assert ffmpeg.probe(result.video)["height"] == 640
    assert "Hôm nay" in (result.folder/"captions.srt").read_text(encoding="utf-8")
    assert json.loads((result.folder/"script.json").read_text(encoding="utf-8"))["scenes"][1]["thread"]["author"] == "reader"


@needs_ffmpeg
def test_threads_attachments_play_after_their_card(settings, tmp_path, monkeypatch):
    from purffle_shorts import pipeline
    from purffle_shorts.render import extract_frame

    Image.new("RGB", (160, 90), "red").save(tmp_path / "photo.png")
    ffmpeg.run(["-f", "lavfi", "-i", "color=blue:s=160x90:r=24",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1.5",
                "-c:v", "libx264", "-c:a", "aac", str(tmp_path / "clip.mp4")])
    ffmpeg.run(["-f", "lavfi", "-i", "color=green:s=360x640:r=24:d=1",
                "-c:v", "libx264", str(tmp_path / "background.mp4")])
    raw = thread_script(settings).to_dict()
    raw["scenes"][0]["thread"]["media"] = [
        {"kind": "image", "src": "photo.png"},
        {"kind": "video", "src": "clip.mp4", "duration": 1},
    ]
    raw["scenes"].append({"thread": {
        "kind": "comment", "media": [{"kind": "image", "src": "photo.png", "duration": .5}],
    }})
    script_file = tmp_path / "script.json"
    script_file.write_text(json.dumps(raw), encoding="utf-8")
    settings = settings.with_overrides(offline=True, tts_engine="silent", resolution=(360, 640),
                                       music_volume=0, fps=24, transition="none")
    settings = settings.with_overrides(background_video=str(tmp_path / "background.mp4"))
    capture = {}
    render = pipeline.render_video

    def record(*args, **kwargs):
        capture["total"], capture["inserts"] = args[4], kwargs["inserts"]
        return render(*args, **kwargs)

    monkeypatch.setattr(pipeline, "render_video", record)
    result = Studio(settings).make(script_file=script_file, upload=False)
    assert result.ok, result.error
    still, clip, final = capture["inserts"]
    assert [item.duration for item in capture["inserts"]] == [3, 1, .5]
    assert clip.has_audio and still.at == clip.at < final.at == capture["total"]
    assert abs(ffmpeg.duration(result.video) - capture["total"] - 4.5) < .15

    def pixel(at, xy):
        frame = extract_frame(result.video, at, tmp_path / "frame.png")
        with Image.open(frame) as image:
            return image.convert("RGB").getpixel(xy)

    screen = ThreadsScreen(settings, Path(settings.caption_font))
    center = (settings.width // 2, 224)
    card_edge = (screen.x + 6, 224)
    assert max(pixel(still.at - .25, card_edge)) < 40
    r, g, b = pixel(still.at + .5, center)
    assert r > 200 and max(g, b) < 30
    for at, item, channel in ((still.at + .5, still, 0),
                              (clip.at + still.duration + .5, clip, 2)):
        assert item.fit_size[0] == int(screen.width * .9) // 2 * 2
        assert pixel(at, center)[channel] > 200
        assert max(pixel(at, card_edge)) < 40  # black frame around media
        r, g, b = pixel(at, (screen.x - 8, 224))
        assert g > 100 and max(r, b) < 30
    audio = ffmpeg.run(["-ss", str(clip.at + still.duration + .2), "-i", str(result.video), "-t", "0.5",
                        "-vn", "-af", "volumedetect", "-f", "null", "-"], loglevel="info")
    peak = next(line for line in audio.splitlines() if "max_volume:" in line)
    assert float(peak.split()[-2]) > -30
    assert max(pixel(final.at + 4 - .25, card_edge)) < 40
    r, g, b = pixel(final.at + 4 + .4, center)
    assert r > 200 and max(g, b) < 30

    subtitles = (result.folder / "captions.srt").read_text(encoding="utf-8")

    def seconds(timestamp):
        h, m, s = timestamp.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    ranges = [tuple(map(seconds, line.split(" --> "))) for line in subtitles.splitlines() if " --> " in line]
    assert any(start >= still.at + 4 - .001 for start, _ in ranges)
    assert all(end <= still.at + .001 or start >= still.at + 4 - .001 for start, end in ranges)
