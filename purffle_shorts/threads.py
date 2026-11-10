"""Threads post/comment cards, shared by the video renderer and Studio preview.

No remote avatars or engagement figures are invented. Each scene supplies its own
``thread`` metadata; long text continues on another card without shrinking the type.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from .overlays import _wrap, load_font


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
    "reposts": "M 3 9 L 3 6 C 3 4 5 3 7 3 L 18 3 L 15 0 M 18 3 L 15 6 M 21 15 L 21 18 C 21 20 19 21 17 21 L 6 21 L 9 24 M 6 21 L 9 18",
    "shares": "M 2 3 L 23 1 L 15 23 L 10 13 L 2 3 Z M 10 13 L 23 1",
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
        self.width = int(min(settings.width * .88, settings.unit * .95)) // 2 * 2
        self.height = int(settings.height * .76) // 2 * 2
        self.x = (settings.width - self.width) // 2
        self.y = (settings.height - self.height) // 2
        self.pad = max(12, round(settings.unit * .035))
        self.size = max(12, round(settings.unit * .036))
        self.font = load_font(self.fp, self.size, 450)
        self.name_font = load_font(self.fp, self.size, 700)
        self.small = load_font(self.fp, max(10, round(self.size * .78)), 450)
        self.line_h = math.ceil(self.size * 1.42)
        self.avatar = round(self.size * 1.7)
        self.body_y = self.pad + self.avatar + round(self.size * .65)
        self.footer_h = round(self.size * 2.1)
        self.max_lines = max(1, (self.height - self.body_y - self.footer_h - self.pad) // self.line_h)

    def lines(self, text):
        lines = []
        for para in str(text).split("\n"):
            # Character wrapping also handles URLs and CJK without overflowing the card.
            for line in _wrap(para, self.font, self.width - 2 * self.pad):
                if self.font.getlength(line) > self.width - 2 * self.pad:
                    lines.extend(_wrap(line, self.font, self.width - 2 * self.pad, by_char=True))
                else:
                    lines.append(line)
        return lines

    def pages(self, scenes, cast=()):
        pages = []
        for index, scene in enumerate(scenes):
            meta = dict(scene.thread)
            meta.setdefault("kind", "post" if index == 0 else "comment")
            meta.setdefault("author", (cast[0] if cast else "threads_user") if meta["kind"] == "post" else "reader")
            text = str(meta.get("text") or scene.narration)
            lines = self.lines(text)
            parts = math.ceil(len(lines) / self.max_lines)
            for part in range(parts):
                pages.append(ThreadPage("\n".join(lines[part*self.max_lines:(part+1)*self.max_lines]),
                                        meta, index, part + 1, parts))
        return pages

    def render(self, page: ThreadPage):
        lines = page.text.split("\n")
        card_h = self.body_y + len(lines) * self.line_h + self.footer_h + self.pad
        img = Image.new("RGBA", (self.width, self.height))
        d = ImageDraw.Draw(img)
        top = (self.height - card_h) // 2
        p = self.pad
        ink, grey = "#F3F5F7", "#777777"
        d.rounded_rectangle((0, top, self.width-1, top+card_h-1), radius=round(self.size*.65), fill="#101010")
        av_y = top+p
        d.ellipse((p, av_y, p+self.avatar, av_y+self.avatar), fill="#353535")
        author = str(page.meta.get("author") or "threads_user").lstrip("@")
        d.text((p+self.avatar/2, av_y+self.avatar/2), author[:1].upper(), font=self.name_font, anchor="mm", fill=ink)
        x = p+self.avatar+round(self.size*.45)
        name = author
        max_name = self.width-x-p-self.size*4
        while name and self.name_font.getlength(name) > max_name:
            name = name[:-1]
        if name != author:
            name = name[:-1] + "…"
        d.text((x, av_y+self.avatar*.3), name, font=self.name_font, anchor="lm", fill=ink)
        if page.meta.get("verified") is True:
            cx, cy, r = x+self.name_font.getlength(name)+self.size*.45, av_y+self.avatar*.3, self.size*.24
            d.ellipse((cx-r, cy-r, cx+r, cy+r), fill="#0095F6")
            d.line([(cx-r*.5, cy), (cx-r*.1, cy+r*.4), (cx+r*.55, cy-r*.4)], fill="white", width=max(1, self.size//16))
        label = str(page.meta.get("timestamp") or ("Bài viết" if page.meta.get("kind") == "post" else "Bình luận"))[:40]
        d.text((x, av_y+self.avatar*.85), label, font=self.small, anchor="lm", fill=grey)
        for k in range(3):
            cx, cy, r = self.width-p-self.size*.25*k, av_y+self.size*.45, max(1, self.size*.055)
            d.ellipse((cx-r, cy-r, cx+r, cy+r), fill=grey)
        y = top+self.body_y
        for line in lines:
            d.text((p, y), line, font=self.font, fill=ink)
            y += self.line_h
        y += round(self.size*.55)
        icon_size = self.size*.92
        slot = (self.width-2*p)/4
        for index, (key, path) in enumerate(ICONS.items()):
            x = p+index*slot
            draw_icon(d, path, x, y, icon_size, ink, max(1, round(self.size*.065)))
            count = str(page.meta.get(key, ""))[:12]
            available = slot-icon_size-self.size*.45
            if self.small.getlength(count) > available:
                while count and self.small.getlength(count+"…") > available:
                    count = count[:-1]
                count += "…"
            x += icon_size + self.size*.2
            if count:
                d.text((x, y+icon_size/2), count, font=self.small, anchor="lm", fill=ink)
        if page.parts > 1:
            d.text((self.width-p, top+card_h-self.pad*.45), f"{page.part}/{page.parts}",
                   font=self.small, anchor="rs", fill=grey)
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
