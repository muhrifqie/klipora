# Auto Chapters / Bab Otomatis (tool id `chapters`)

YouTube chapters for a sequence: chapter starts and titles from the transcript, an editable list in the panel,
then cyan **Chapter markers** on the sequence (tag `[Klipora-CH]`, replaced on every re-run) plus the
`00:00 Judul` text for the YouTube description (clipboard + `.txt`). Markers are non-destructive: media, clips and
other markers are never touched, and the project is never saved.

Files: `engine/ac/tools/chapters.py`, `engine/tests/test_chapters.py`, `panel/js/tools/chapters.js`,
`panel/css/tools/chapters.css`, `panel/host/35_chapters.jsx`, `tools/ui_tests/chapters.mjs`.
Background: internal research notes and prototypes (not published) on AI chapters and prompting.


## Names, tags and language (Klipora)

- UI title "Auto Chapters" (tab "Chapters") in English, "Bab Otomatis" (tab "Bab") in Indonesian. Every panel text is a
  locale key `chapters.*` (`panel/locales/{id,en}.json`), host error texts are `host.chapters*` (via `acT`), engine
  texts (stage labels, warnings, YouTube rule issues, notes, summaries) are `chapters.*` in `engine/ac/locales`.
- Marker tag `[Klipora-CH]`. Markers made by older versions (`[AC-CH]`) are still read ("Edit existing chapters"),
  replaced and cleared (`acHasTag` / `acTagAlts` in the host, `chapters.has_tag` in the engine).
- Content vs UI: AI chapter titles, the transcript snippets and the `00:00 Title` lines are content and stay as
  written. Fixed words the engine adds itself follow the job language: the first chapter added when the AI missed it
  ("Pembuka" / "Intro"), keyword fallback "Bagian n" / "Part n", empty title on apply "Bab n" / "Chapter n".
  AI prompts are not translated.
- In the editor, chapter titles, the chapter strip, transcript snippets, the YouTube text and the generated
  title/description/hashtags are marked `data-i18n-skip` (user/AI content). `ui_tests/chapters.mjs` ends with an
  English pass (setup, editor, result: no Indonesian leftovers).

## What it does

1. **Analyze** (engine `analyze`): timeline words (scope aware, sequence time) -> sentence units -> Whisper outro
   hallucinations and word loops dropped -> ~30 s blocks -> the video is split into ~10-minute parts that are asked
   **in parallel** (grok-fast, `mm:ss text` lines; the model answers `mm:ss` + a quote and every answer is resolved
   locally to a Whisper sentence start, never trusted as a timestamp) -> YouTube rules (first at 00:00, sorted,
   >= 10 s apart, last chapter >= 10 s before the end) -> `merge_to_count` (deterministic: the count preset always
   holds, at most target + 1, shortest chapter folded into its shorter neighbour) -> one small "rapikan judul" call
   when there was more than one part (consistent title style). If the model under-segments (seen live once: 1 chapter
   for a 2-min, 3-step video) the list is topped up to YouTube's minimum of 3 with rule boundaries (titles flagged).
2. **Without AI** (proxy down, Settings AI off, or "Pakai AI" off): topic segmentation from the pause before each
   sentence, the vocabulary shift between the 60 s before and after, and cue words ("selanjutnya", "langkah",
   "sekarang kita", ...). Titles are the 3 most distinctive words of each chapter (tf-idf, filler/function words
   removed, e.g. "Deposit, Toko, Webhook") and are **flagged** "Judul otomatis tanpa AI, cek dulu".
3. **Edit** (panel `#tool/chapters/edit`): title inline edit, time edit (`4:05`, `1:02:03`, `245`, `4.05`), jump to a
   chapter (moves the Premiere playhead), "Pakai posisi playhead", "Gabung dengan bab sebelumnya" (keeps the longer
   part's title), delete, "Tambah bab di playhead", undo button (+ Ctrl+Z when Premiere passes it), live YouTube
   warnings (< 3 chapters, chapter < 10 s, first not at 00:00 with a one-click fix, empty title), chapter map strip,
   YouTube text preview + copy, "Tulis ulang judul" (AI, all titles, same count), and **"Judul, deskripsi &
   hashtag"** (one AI call: 3 video titles, description with the chapter list + 3 hashtags, 5-10 hashtags, tags).
   Every edit is saved to `chapters_review.json` in the workdir, so the list survives navigation and panel reloads
   ("Lanjutkan edit" card on the setup view).
4. **Apply** ("Tambah N bab"): engine `apply` validates the edited list (first forced to 00:00 with a note, empty
   titles -> "Bab N"), writes `<workdir>\chapters_youtube.txt` and returns a `markers` plan; host
   `bac_chapters_apply` deletes the old `[Klipora-CH]` markers of the **analysed** sequence and adds the new ones in one
   call (type Chapter, colour 7 cyan, range = until the next chapter, comment = first words spoken + `[Klipora-CH]`), then
   reads them back. The YouTube text goes to the clipboard (switch "Salin teks YouTube otomatis", default on).
   Result card: count, average length, YouTube "Siap"/"Belum valid", the text, "Salin teks YouTube", "Edit bab",
   "Buka folder", "Hapus hasil" (two-step, removes only `[Klipora-CH]` markers).
5. **Existing chapters**: when the sequence already has `[Klipora-CH]` markers the setup view says so and "Edit bab yang
   ada" loads them into the editor (no engine, no AI).

## Settings (panel state, per viewer) and defaults

| UI | Default | Engine param |
|---|---|---|
| Jumlah bab: Sedikit / **Normal** / Banyak | Normal = clamp(round(menit/4), 3, 15); Sedikit x0,6 (>= 3); Banyak x1,6 (<= 25) | `count` `less`/`normal`/`more` (or an int) |
| Instruksi tambahan (opsional, 300 char) | empty | `hint` (sent to every part prompt and to "Tulis ulang judul") |
| Gaya judul: **Langkah** / Netral / Menarik | Langkah (kata kerja + objek) | `style` `langkah`/`netral`/`menarik` |
| Panjang bab minimal | 10 dtk (also never below 25 % of the average chapter) | `min_len` |
| Pakai AI | on (also needs Settings > AI on) | `ai` |
| Salin teks YouTube otomatis | on | panel only |
| Cakupan (source card) | Seluruh sequence | `scope` (In/Out: markers in sequence time, YouTube text relative to In) |

Settings read by the engine: `ai`, `aiModel` ("" = grok-fast), `glossary` (brand spellings for the prompts),
`workRoot`.

## Engine actions (`engine/ac/tools/chapters.py`)

| Action | Input | Result |
|---|---|---|
| `analyze` | seq + params above | `{review, chapters:[{t,end,title,generic}], youtube, offset, end, source: ai/mixed/rule, warnings, issues, target, stats, summary}`; review file `chapters_review.json` (items `kind:"chapter"`, `t0` start, `t1` next start, `title`/`label`, `ctx` first words, `generic`, `src`; doc extras `offset`, `end`, `source`, `warnings`, `issues`, `youtube`, `model`, later `meta`) |
| `apply` | `job.review` (edited) | `{plan:{kind:"markers", tag:"[Klipora-CH]", markers:[{t,end,name,comment,tag,color:7,type:"Chapter"}], timebase:"sequence"}, youtube, txt, notes, issues, youtube_ok, n, seq}` |
| `meta` | `params.chapters` [{t,title}] (+ `offset`, `scope`, `ai`) | `{titles, description, hashtags, tags, ai, source, warnings}`; without AI: no titles, description = chapter list |
| `retitle` | `params.chapters` + `style`, `hint` | `{titles, chapters}`; error `NO_AI` without AI |

Stages (analyze): `words` Transkrip, `units` Susun kalimat, `find` Tulis bab dengan AI / Cari pergantian topik,
`check` Cek aturan YouTube. Errors: `NO_SEQ`, `NO_AUDIO`, `NO_SPEECH`, `NO_CHAPTERS`, `NO_AI`, `AI_FAILED`.
AI answers are cached per part in `%LOCALAPPDATA%\AutoCutBOT\ai_cache` (key = prompt version + model + messages):
re-running with the same settings costs no AI request. A failed part falls back to rule picks for that part only
(`source: "mixed"`).

## Host functions (`panel/host/35_chapters.jsx`, ES3)

`bac_chapters_apply(list, seqId)` -> `{ok, id, name, removed, added, n, chapters}`;
`bac_chapters_read(seqId)` -> `{ok, id, name, duration, chapters:[{t,end,name,comment,type,color,guid}]}`;
`bac_chapters_clear(seqId)` -> `{ok, id, name, removed}`. `seqId` = the analysed sequence (empty = active); a
missing id returns "Sequence untuk bab ini tidak ditemukan lagi...". Built on `tlAddMarker` (lab-verified).

## Measured (this PC, 2026-10-05, real media, cached transcripts)

| Case | Result |
|---|---|
| 34,6 min, AI, Normal | 7,6 s wall (3 parts in parallel 3,9 / 4,5 / 4,6 s + polish), 10 chapters (target 9), 34 raw picks resolved 25 time+quote / 6 by quote / 3 by time, last chapter 32:03 (no front-loading). `00:00 Isi Formulir Pendaftaran Reseller`, `02:30 Isi Data Pembayaran Akun`, `08:17 Beli Domain TokoKita.com`, `13:07 Akses Cloudflare Lindungi Website`, `15:37 Ubah Name Server ke Cloudflare`, `20:02 Atur Harga Produk di Toko`, `23:02 Simpan dan Tunggu Review`, `25:34 Atur Harga Produk GPT`, `28:04 Atur Data Pembeli Order`, `32:03 Atur Notifikasi Telegram` |
| 2 min, AI | 3,3 s, 3 chapters (one run returned 1 chapter -> now topped up to 3) |
| 49 s, AI | 3,7 s, 2 chapters -> warning "YouTube butuh minimal 3 bab" (markers still added) |
| 34,6 min, no AI | 0,03 s, 9 chapters, e.g. `02:30 Deposit, Toko, Webhook`, `13:17 Verifikasi, Nameserver, Pengaturan`, `33:11 Telegram, Stok, Setting` |
| Meta (34,6 min) | 1 request; 3 titles, description with chapters, 10 hashtags, tags |

## Tests

- `python engine/tests/test_chapters.py` (offline, ~3 s, no quota): rules (count, titles, timestamps >= 1 h,
  YouTube issues, merge determinism, hallucination filter), rule fallback on all 3 media (counts Sedikit < Normal <
  Banyak, keyword titles without filler words), In/Out scope, cut sequence, NO_AUDIO, mocked parallel AI (3 parts,
  resolve, polish, cache hit = 0 requests, one failed part -> mixed, under-segmentation top-up), apply (first moved
  to 00:00, edited title/time, deleted/off rows, markers chain, txt), meta/retitle without AI, CLI JSON lines +
  `cli.py tools`. `--live` adds one real analyze (2 min) + one meta call.
- `node tools/ui_test.mjs tools/ui_tests/chapters.mjs --engine live` (42 checks, AI off = no quota; also passes at
  `--width 280`): setup, analyze -> editor, title/time edit, bad time refused, merge + warning, merge keeps a real
  title over a flagged one, undo, add at playhead, delete + undo of the focused new row, seek with the analysed seq
  id, review file saved, meta without AI, apply -> markers (type, colour, chain, tag) + txt + result, re-run
  replaces, existing markers -> "Edit bab yang ada", "Hapus hasil" (back to the editor), resume card, no
  horizontal overflow. `AC_CH_AI=1 ... --seq long35` runs the AI path + meta (screenshots).
- Screenshots (gitignored `--shots` folder): `build_chapters.png` (AI editor, 34,6 min), `build_chapters_setup.png`,
  `build_chapters_rule.png` (no AI, flagged titles), `build_chapters_warn.png`, `build_chapters_result.png`,
  `build_chapters_meta.png`.

## What the Premiere verifier must test (step by step)

Work in bin `Klipora Test`, restore the user's active sequence at the end, never save, delete only what you made.

1. `node tools/reload.mjs`: no EXC/console lines; `AC.host.loadErrors` is `[]` and `AC.host.files` lists
   `35_chapters.jsx`. `AC.host.json('bac_chapters_read','')` returns `{ok:true, chapters:[]}` on a clean sequence.
2. Make a test sequence from `seq_2m.mp4` (2 min) in the test bin and activate it. Add one manual marker
   (`tlAddMarker(seq, 5, "manual", "keep me", "Comment", 1, 0)` via `tools/ev.mjs`) to prove other markers survive.
3. Panel: Home > Bab (or `#tool/chapters`). Normal, AI on. Click "Buat bab". Expect the editor within ~10 s
   (`#tool/chapters/edit`), >= 3 rows, YouTube text starting `00:00`. AI cost: 1 request (0 if cached).
4. Click the play button of row 2: Premiere's playhead jumps to that chapter (check `bac_seqInfo('lite').player`).
5. Move the Premiere playhead to ~1:00, click "Tambah bab di playhead": a row at 01:00 appears with the title field
   focused; type a title; then delete that row (trash) and press the undo button once (row comes back), delete again.
6. Click "Tambah N bab". Result card "N bab ditambahkan". With `tools/ev.mjs` + `tlReadMarkers(app.project.activeSequence)`
   check: N markers with `[Klipora-CH]` in the comment, `type == "Chapter"`, `getColorByIndex() == 7` (cyan), first start
   0, each end = next start, last end = sequence end, names = the titles; the manual marker is still there.
   Screenshot the timeline/Markers panel to confirm cyan chapter markers with ranges.
7. Clipboard (PowerShell `Get-Clipboard`) equals the YouTube text; `<workRoot>\<seq name>\chapters_youtube.txt` has
   the same lines. (UNVERIFIED: `document.execCommand('copy')` inside CEP 12.)
8. "Edit bab" > change one title > "Tambah N bab" again: still N `[Klipora-CH]` markers (result says "N marker bab lama
   diganti"), the new title is on the marker.
9. Back to the setup view: the info "Sequence ini sudah punya N marker bab" appears; "Edit bab yang ada" opens them.
10. Result card "Hapus hasil" (two clicks): all `[Klipora-CH]` markers gone, the manual marker stays.
11. In/Out: set In 0:30 / Out 1:30, choose scope In/Out, "Buat bab": first marker at 0:30, YouTube text starts
    `00:00`, no marker outside 0:30-1:30.
12. Switch the active sequence to another one while the editor is open: notice "Bab ini untuk sequence ...";
    "Tambah N bab" still writes markers only on the analysed sequence.
13. Settings > AI off: "Buat bab" works without any proxy request, rows show "Judul otomatis tanpa AI, cek dulu";
    "Judul, deskripsi & hashtag" shows "AI dimatikan" and a description with the chapter list.
14. Ctrl+Z in the editor (not in a text field): undo works if Premiere passes the key (UNVERIFIED; the undo button is
    the supported path).
15. Cleanup: delete test sequence + bin, re-activate the user's sequence, never save.

## Live Premiere verification (2026-10-05, Premiere 26.2.2, verifier agent)

(Recorded before the Klipora rename: `[AC-CH]` here is `[Klipora-CH]` today; old markers are still recognised.)

Test sequences from the 2 mnt, 49 dtk and 34,6 mnt media in bin "Klipora Test", driven through the panel UI
(real CDP mouse/keyboard input), checked with ExtendScript; all removed afterwards (project items and sequence list
identical to before), user's sequence + playhead (16,17 dtk) restored, project never saved. Screens (gitignored `--shots` folder): `chapters.png` (timeline: cyan chapter ranges + the red manual marker), `chapters_setup.png`,
`chapters_edit.png`, `chapters_added.png`, `chapters_result.png`, `chapters_existing.png`, `chapters_inout.png`,
`chapters_otherseq.png`, `chapters_49s_warn.png`, `chapters_merge_warn.png` (before the merge fix),
`chapters_noai_meta.png`, `chapters_error_noaudio.png`, `chapters_35min_edit.png`, `chapters_meta.png`,
`chapters_deleted.png` (before the clear fix: stale "3 bab ditambahkan" card), `chapters_after_clear.png`,
`chapters_error_missing.png`.

| Check | Result |
|---|---|
| Reload, host load, console | OK: `loadErrors` [], `35_chapters.jsx` loaded, no EXC, `AC.log.errors` empty all session; `bac_chapters_read` on a clean sequence `{ok, chapters: []}` |
| Buat bab, 2 mnt, AI | 4,3 s click -> editor, 3 bab (`00:00 Daftar Akun Reseller`, `00:38 ...`, `01:08 ...`), source AI |
| Row play button | CTI 38,1917 s (frame aligned), toast "Playhead ke 0:38,19" |
| Tambah bab di playhead (1:00) | row 01:00 inserted, title focused, "Terlalu pendek: 9 dtk" + "1 bab perlu dicek" |
| Delete focused new row + undo | FIXED: the row did not come back (the removed row's "change" pushed the post-delete list); now restored |
| Tambah 3 bab | 0,3-0,4 s; 3 markers type Chapter, colour 7, `[AC-CH]` after a newline in the comment, 0 -> 38,19 -> 68,81 -> 126,8917 (= sequence end), names = titles; manual marker untouched |
| Clipboard / txt | `execCommand('copy')` works in CEP 12: `Get-Clipboard` = YouTube text (CRLF); `chapters_youtube.txt` same lines |
| Edit title + re-apply | still 3 markers, "3 marker bab lama diganti", new title on the marker |
| Setup view | "Sequence ini sudah punya 3 marker bab"; "Edit bab yang ada" -> 3 rows, "Dari timeline" |
| Hapus hasil (2 clicks) | only `[AC-CH]` removed, manual marker stays. FIXED: the result card stayed ("3 bab ditambahkan", live delete button); now back to the editor |
| In/Out 0:30-1:30 | markers 30 -> 48,89 -> 63,05 -> 90, nothing outside, YouTube text `00:00 / 00:18 / 00:33` |
| Other sequence active | notice "Bab ini untuk sequence ..."; markers written only on the analysed sequence (other: 0) |
| 49 dtk, AI | 3,1 s, 2 AI + 1 topped-up rule chapter (flagged), warning shown; merge keeps "Mulai Tutorial ..." (FIXED: the flagged "Daftar" of the 0,6 s longer part replaced it) |
| AI off (Settings) | 0,2 s, source rule, all rows "Judul otomatis tanpa AI, cek dulu", no "Tulis ulang judul", ai_cache unchanged; meta = chapter list |
| Muted A1 | error card "Tidak ada audio" (NO_AUDIO) in Indonesian, "Coba lagi" |
| Analysed sequence deleted, Tambah | "Marker bab gagal ditambahkan: Sequence untuk bab ini tidak ditemukan lagi..." |
| Cancel (34,6 mnt) | "Batalkan" during analyze: toast "Dibatalkan. Tidak ada yang berubah.", no engine python left |
| 34,6 mnt, AI | 7,7 s, 10 bab, last 27:40; apply 10 markers 0,4 s, chain exact, last end = 2076,74 |
| Judul, deskripsi & hashtag (AI) | FIXED: failed live (8,5 s, 2 requests): grok-fast wrote hashtags without "#", schema `^#\w+$` rejected them even after the repair. Now 3,4 s, 1 request: 3 titles, description + chapters + 3 hashtags, 7 hashtags, 15 tags. The alert now says why (dimatikan / gagal / tidak terhubung) |
| Tulis ulang judul (AI) | 2,7 s, 1 request, 3 titles, undo toast |
| Ctrl+Z (panel side) | CDP key event outside a text field -> "Perubahan dibatalkan" |

## Known gaps / UNVERIFIED

- Ctrl+Z from the real keyboard: Ctrl+Z is not in `AC.keys` key interest, so Premiere most likely takes it (undoes
  its own last action). Only the panel side was verified (CDP key event). The undo button is the supported path.
- Chapter times are sequence seconds at analysis time; if the sequence is cut afterwards the editor warns
  ("Sequence berubah sejak bab dibuat") but does not remap.
- The meta titles are sometimes over 70 characters (prompt asks for 70; counted in `stats.titles_over_70`).
- No MP4 chapter metadata (only useful when we render the file; YouTube ignores it anyway).
