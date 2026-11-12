"""Threads post/comment cards, shared by the video renderer and Studio preview.

Author names and avatars use fixed flat shapes in rendered cards. Long text
continues on another card without shrinking the type.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from . import ffmpeg
from .overlays import _wrap, load_font
from .render import MediaInsert
from .timing import caption_chunks
from .utils import download


def prepare_attachments(scenes, starts, total, fps, work, screen):
    """Resolve each scene's attachments in the order they will play."""
    inserts = []
    for index, scene in enumerate(scenes):
        attachments = scene.thread.get("media")
        if attachments is None:
            continue
        if not isinstance(attachments, list):
            raise ValueError(f"Scene {index + 1}: thread.media must be a list")
        # Cut on a video frame just before the next line of speech.
        at = math.floor(starts[index+1] * fps) / fps if index+1 < len(starts) else total
        for number, attachment in enumerate(attachments, 1):
            label = f"Scene {index + 1}, attachment {number}"
            kind = attachment.get("kind") if isinstance(attachment, dict) else None
            src = attachment.get("src") if isinstance(attachment, dict) else None
            if kind not in ("image", "video") or not isinstance(src, str) or not src.strip():
                raise ValueError(f"{label}: expected kind (image or video) and src")
            dest = work / f"attachment-{index+1}-{number}"
            work.mkdir(parents=True, exist_ok=True)
            if src.startswith(("http://", "https://")):
                path = download(src, dest)
            else:
                path = Path(src).expanduser().resolve()
                if not path.is_file():
                    raise ValueError(f"{label}: media file does not exist: {path}")
            if kind == "image":
                with Image.open(path) as image:
                    rgba = ImageOps.exif_transpose(image).convert("RGBA")
                    rgb = Image.new("RGB", rgba.size)
                    rgb.paste(rgba, mask=rgba.getchannel("A"))
                    path = dest.with_suffix(".png")
                    rgb.save(path)
                    width, height = rgb.size
                duration, has_audio = 3.0, False
            else:
                info = ffmpeg.probe(path)
                duration, has_audio = info["duration"], info["has_audio"]
                width, height = info["width"], info["height"]
                if not (width and height) or not math.isfinite(duration) or duration <= 0:
                    raise ValueError(f"{label}: expected a video with a known duration")
            requested = attachment.get("duration")
            if requested is not None:
                if (not isinstance(requested, (float, int)) or isinstance(requested, bool)
                        or not math.isfinite(requested) or requested <= 0):
                    raise ValueError(f"{label}: duration must be a positive number of seconds")
                duration = min(duration, requested) if kind == "video" else requested
            duration = max(1, round(duration * fps)) / fps
            frame_path = dest.with_name(dest.name + "-card.png")
            fit_size = screen.media_card(width, height, frame_path)
            inserts.append(MediaInsert(at, duration, path, kind, frame_path, fit_size, has_audio))
    return inserts


def attachment_captions(words, inserts, max_words, total):
    """Keep subtitles in sync and leave attachment intervals free of narration captions."""
    chunks = []
    start = offset = 0.0
    durations = {}
    for item in inserts:
        durations[item.at] = durations.get(item.at, 0.0) + item.duration
    for end, duration in [*durations.items(), (total, 0.0)]:
        if end > start:
            group = [word for word in words if start <= word.start < end]
            for chunk in caption_chunks(group, max_words, total=end):
                chunk.start += offset
                chunk.end = min(chunk.end, end) + offset
                chunks.append(chunk)
        start = end
        offset += duration
    return chunks


def resolve_threads_font(settings):
    """Prefer a local UI font so opening the preview never waits for a font download."""
    candidates = [settings.caption_font, str(settings.data_path / "fonts/NotoSans-Variable.ttf"),
                  "C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    from .overlays import resolve_font
    return resolve_font(settings, "sans")[0]

ICONS = {
    "likes": "M 12 21 C 10 19 2 14 2 8 C 2 2 9 1 12 6 C 15 1 22 2 22 8 C 22 14 14 19 12 21 Z",
    "replies": "M 12 2 C 6 2 2 6 2 11 C 2 16 6 20 12 20 C 14 20 16 20 18 19 L 22 22 L 21 16 C 24 9 20 2 12 2 Z",
    "reposts": "M 2 3 L 2 9 L 8 9 M 2 9 C 5 0 17 0 21 8 M 22 21 L 22 15 L 16 15 M 22 15 C 19 24 7 24 3 16",
    "shares": "M 4 2 L 21 10 C 23 11 23 13 21 14 L 4 22 C 2 23 1 21 2 19 L 5 12 L 2 5 C 1 3 2 1 4 2 Z M 5 12 L 22 12",
}


def draw_icon(draw, path: str, x: float, y: float, size: float, fill, width: int):
    """Draw the same 24-unit outline paths used for the action row."""
    tokens = re.findall(r"[MLCZ]|-?\d+(?:\.\d+)?", path)
    points, current, origin = [], (0.0, 0.0), (0.0, 0.0)
    i = 0

    def flush():
        if len(points) > 1:
            draw.line([(x + a * size / 24, y + b * size / 24) for a, b in points],
                      fill=fill, width=width, joint="curve")

    while i < len(tokens):
        cmd = tokens[i]
        i += 1
        if cmd == "Z":
            points.append(origin)
            current = origin
        elif cmd in ("M", "L"):
            p = (float(tokens[i]), float(tokens[i + 1]))
            i += 2
            if cmd == "M":
                flush()
                points = [p]
                origin = p
            else:
                points.append(p)
            current = p
        elif cmd == "C":
            a, b, end = [(float(tokens[i + k]), float(tokens[i + k + 1])) for k in (0, 2, 4)]
            i += 6
            for step in range(1, 21):
                t = step / 20
                points.append(tuple((1-t)**3 * current[j] + 3*(1-t)**2*t*a[j]
                                    + 3*(1-t)*t*t*b[j] + t**3*end[j] for j in (0, 1)))
            current = end
    flush()


@dataclass
class ThreadPage:
    text: str
    meta: dict
    scene: int
    part: int = 1
    parts: int = 1


class ThreadsScreen:
    def __init__(self, settings, font_path: Path | None):
        self.s = settings
        self.fp = str(font_path) if font_path else None
        self.width = round(settings.width * .75 / 2) * 2
        self.height = int(settings.height * .76) // 2 * 2
        self.x = (settings.width - self.width) // 2
        self.y = (settings.height - self.height) // 2
        self.size = max(8, round(settings.unit * 25 / 1080))
        self.pad = round(self.size * 1.6)
        self.radius = round(self.size * 1.25)
        self.font = load_font(self.fp, self.size, 450)
        self.line_h = math.ceil(self.size * 1.4)
        self.avatar = round(self.size * 2.4)
        self.avatar_gap = round(self.size * .8)
        self.actions_gap = round(self.size * .85)
        self.icon_size = round(self.size * 1.13)
        self.footer_h = self.actions_gap + self.icon_size + self.pad
        self.max_lines = self._max_lines(False)

    def _body_top(self, comment):
        if comment:
            return self.pad + round(self.size * 1.45)
        return self.pad + self.avatar + round(self.size * .95)

    def _max_lines(self, comment):
        return max(1, (self.height - self._body_top(comment) - self.footer_h) // self.line_h)

    def lines(self, text, comment=False):
        width = self.width - self.pad - self._text_left(comment)
        lines = []
        for para in str(text).split("\n"):
            for line in _wrap(para, self.font, width):
                if self.font.getlength(line) > width:
                    lines.extend(_wrap(line, self.font, width, by_char=True))
                else:
                    lines.append(line)
        return lines

    def _text_left(self, comment):
        return self.pad + (self.avatar + self.avatar_gap if comment else 0)

    def pages(self, scenes, cast=()):
        pages = []
        for index, scene in enumerate(scenes):
            meta = dict(scene.thread)
            meta.setdefault("kind", "post" if index == 0 else "comment")
            meta.setdefault("author", (cast[0] if cast else "threads_user") if meta["kind"] == "post" else "reader")
            text = str(meta.get("text") or scene.narration)
            comment = meta["kind"] == "comment"
            lines = self.lines(text, comment)
            max_lines = self._max_lines(comment)
            parts = math.ceil(len(lines) / max_lines)
            for part in range(parts):
                pages.append(ThreadPage("\n".join(lines[part*max_lines:(part+1)*max_lines]),
                                        meta, index, part + 1, parts))
        return pages

    def _avatar(self, draw, x, y, follow):
        size = self.avatar
        draw.ellipse((x, y, x+size-1, y+size-1), fill="#3C4046")
        if follow:
            cx, cy = x+size*.83, y+size*.82
            r = size*.25
            draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill="#FFFFFF",
                         outline="#000000", width=max(1, round(self.size*.1)))
            arm = size*.11
            stroke = max(1, round(self.size*.08))
            draw.line((cx-arm, cy, cx+arm, cy), fill="#000000", width=stroke)
            draw.line((cx, cy-arm, cx, cy+arm), fill="#000000", width=stroke)

    def _header(self, draw, page, y, muted):
        x = self.pad + self.avatar + self.avatar_gap
        right = self.width - self.pad - round(self.size * 1.7)
        label = str(page.meta.get("timestamp") or "")
        verified = page.meta.get("verified") is True
        gap = self.size * .36
        badge = self.size * .78 if verified else 0
        name_width = round(self.size * 4.8)
        name_height = round(self.size * .76)
        top = round(y - name_height / 2)
        draw.rounded_rectangle((x, top, x+name_width-1, top+name_height-1),
                               radius=max(1, round(self.size * .12)), fill="#3C4046")
        x += name_width
        if verified:
            r = self.size*.24
            cx = x + gap + r
            draw.ellipse((cx-r, y-r, cx+r, y+r), fill="#0095F6")
            draw.line([(cx-r*.5, y), (cx-r*.1, y+r*.4), (cx+r*.55, y-r*.4)],
                      fill="white", width=max(1, self.size//16))
            x += badge
        label = self._ellipsize(label, self.font, right-x-gap)
        draw.text((x+gap, y), label, font=self.font, anchor="lm", fill=muted)
        for index in range(3):
            cx = self.width - self.pad - self.size*.44*index
            r = max(1, self.size*.1)
            draw.ellipse((cx-r, y-r, cx+r, y+r), fill=muted)

    @staticmethod
    def _ellipsize(text, font, width):
        if font.getlength(text) <= width:
            return text
        if font.getlength("…") > width:
            return ""
        while text and font.getlength(text+"…") > width:
            text = text[:-1]
        return text+"…"

    def _actions(self, draw, page, x, y):
        color = "#C7C7C7"
        gap = self.size * 1.25
        count_gap = self.size * .32
        available = self.width - self.pad - x
        count_budget = (available - (self.icon_size + count_gap) * len(ICONS)
                        - gap * (len(ICONS) - 1))
        counts = [page.meta.get(key) for key in ICONS]
        labels = [" " if count is None or str(count).strip() == "" else str(count) for count in counts]
        if sum(self.font.getlength(label) for label in labels) > count_budget:
            labels = [self._ellipsize(label, self.font, count_budget / len(ICONS)) for label in labels]
        for path, label in zip(ICONS.values(), labels):
            draw_icon(draw, path, x, y, self.icon_size, color, max(1, round(self.size*.105)))
            x += self.icon_size + count_gap
            draw.text((x, y+self.icon_size/2), label, font=self.font, anchor="lm", fill=color)
            x += self.font.getlength(label) + gap

    def _draw_card_background(self, draw, bounds):
        draw.rounded_rectangle(bounds, radius=self.radius, fill="#000000", outline="#262626",
                               width=max(1, round(self.size * .05)))

    def media_card(self, width: int, height: int, path: Path) -> tuple[int, int]:
        """Save the transparent shell and return the maximum dimensions of its media."""
        padding = self.pad
        content_width = max(2, int(self.width * .9) // 2 * 2)
        content_height = min(self.height - 2 * padding, math.ceil(height * content_width / width / 2) * 2)
        content_height = max(2, content_height // 2 * 2)
        card = Image.new("RGBA", (self.width, content_height + 2 * padding))
        self._draw_card_background(ImageDraw.Draw(card), (0, 0, card.width-1, card.height-1))
        card.save(path)
        return content_width, content_height

    def card_height(self, page: ThreadPage) -> int:
        comment = page.meta.get("kind") == "comment"
        line_count = page.text.count("\n") + 1 if page.text else 0
        return self._body_top(comment) + line_count * self.line_h + self.footer_h

    def render(self, page: ThreadPage):
        comment = page.meta.get("kind") == "comment"
        lines = page.text.split("\n") if page.text else []
        body_top = self._body_top(comment)
        card_h = self.card_height(page)
        img = Image.new("RGBA", (self.width, self.height))
        d = ImageDraw.Draw(img)
        top = (self.height - card_h) // 2
        bottom = top + card_h - 1
        ink, muted = "#F3F5F7", "#777777"
        bounds = (0, top, self.width-1, bottom)
        self._draw_card_background(d, bounds)
        av_y = top + self.pad
        self._avatar(d, self.pad, av_y, follow=not comment)
        header_y = av_y + (self.line_h/2 if comment else self.avatar/2)
        self._header(d, page, header_y, muted)
        x = self._text_left(comment)
        y = top + body_top
        for line in lines:
            d.text((x, y+self.line_h/2), line, font=self.font, anchor="lm", fill=ink)
            y += self.line_h
        self._actions(d, page, x, y+self.actions_gap)
        if page.parts > 1:
            d.text((self.width-self.pad, bottom-self.pad*.3), f"{page.part}/{page.parts}",
                   font=self.font, anchor="rs", fill=muted)
        return img

    def build(self, scenes, starts, total: float, work: Path, cast=()):
        work.mkdir(parents=True, exist_ok=True)
        pages = self.pages(scenes, cast)
        entries = []
        for scene_index in range(len(scenes)):
            scene_pages = [p for p in pages if p.scene == scene_index]
            start = starts[scene_index] if scene_index else 0.0
            end = starts[scene_index+1] if scene_index+1 < len(starts) else total
            weights = [max(1, len(p.text.split())) for p in scene_pages]
            for page, weight in zip(scene_pages, weights):
                name = f"threads-{len(entries):04d}.png"
                self.render(page).save(work/name)
                entries.append((name, (end-start)*weight/sum(weights)))
        lines = ["ffconcat version 1.0"]
        for name, duration in entries:
            lines += [f"file '{name}'", f"duration {duration:.6f}"]
        lines.append(f"file '{entries[-1][0]}'")
        dest = work / "threads.ffconcat"
        dest.write_text("\n".join(lines)+"\n", encoding="utf-8")
        return dest
