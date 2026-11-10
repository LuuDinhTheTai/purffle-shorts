# Threads videos from script.json

In Studio, choose **Threads → script.json**, load the JSON returned by your AI,
review/edit each scene, and select **Render this script**. This skips AI writing
and review. The preview uses the same font, layout and icon renderer as the video;
use the arrows to inspect every card, including continuation pages.

Use `"style": "threads"`. Each scene is one part of the post or one comment,
in reading order. Keep post sections separate from comments. Each scene needs
`narration` (spoken text) and optionally `search_query` for background footage.
The optional `thread` object accepts:

| Field | Meaning |
| --- | --- |
| `kind` | `post` or `comment`; defaults to post for the first scene, comment afterwards |
| `author` | Username; repeated post sections should use the same author |
| `text` | Exact visible text; defaults to narration |
| `timestamp` | Visible age, e.g. `20 giờ` |
| `likes`, `replies`, `reposts`, `shares` | Visible counts, e.g. `1.4K`; omitted counts stay blank |
| `verified` | Boolean; optional verified badge |

Cards use dark Threads styling with an avatar initial, username, age, overflow
menu and outline heart/reply/repost/share icons. Long text is paginated at a fixed
readable font size. Each scene starts with its voice line; continuation pages
divide that line's duration by their text length. If `thread.text` differs from
narration, keep both in the same reading order. Hook, captions, emoji and end CTA
are replaced by cards so they do not overlap. SRT subtitles are still exported.

An existing background video works as usual. No scraping, AI writing, publishing
or remote avatar download is part of this format.

Example: [threads-example.json](../purffle_shorts/web/threads-example.json).

CLI:

```powershell
.\.venv\Scripts\python.exe -m purffle_shorts make --script purffle_shorts/web/threads-example.json
```
