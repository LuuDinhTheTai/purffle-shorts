import json
from pathlib import Path
from unittest.mock import Mock

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


def test_threads_action_row_displays_all_four_counts(settings):
    settings = settings.with_overrides(resolution=(360, 640))
    screen = ThreadsScreen(settings, Path(settings.caption_font))
    pages = screen.pages(thread_script(settings).scenes)
    for page in pages:
        page.meta.update(likes="1,7K", replies=0, reposts=None, shares=405)
        draw = Mock()
        screen._actions(draw, page, screen._text_left(page.meta["kind"] == "comment"), 0)
        assert [call.args[1] for call in draw.text.call_args_list] == ["1,7K", "0", "—", "405"]


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
