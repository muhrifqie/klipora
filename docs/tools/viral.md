# Viral Clips / Klip Viral (tool id `viral`)

Finds the moments of a long video that work as Shorts / TikTok / Reels clips, scores them 0-100, lets the user check
them in Tinjau, then turns the checked ones into **one new sub-sequence per clip** ("Viral 01 - <judul>") plus
**score-coloured markers** on the analysed sequence. Optional 9:16 version per clip through the Auto Resize tool.
The original sequence's content is never changed. Works without AI (rule fallback, labelled "tanpa AI").

| Part | File |
|---|---|
| Engine tool (analyze, apply) | `engine/ac/tools/viral.py` (port + hardening of the internal viral/rerank prototype) |
| Engine test | `engine/tests/test_viral.py` (`--live` = one real AI run on the 2-min file, about 3 requests) |
| Panel page | `panel/js/tools/viral.js`, `panel/css/tools/viral.css` |
| Host (sub-sequences, play, cleanup) | `panel/host/36_viral.jsx` |
| Headless test | `tools/ui_tests/viral.mjs` |
| Screenshots (gitignored `--shots` folder) | `build_viral_setup.png`, `build_viral.png` (Tinjau, AI), `build_viral_result.png` |

Background: internal research notes (not published): pipeline, presets, outputs; mm:ss + quotes instead of ids,
local expansion, rescore; degradation.


## Names, tags and language (Klipora)

- UI title "Viral Clips" (tab "Viral") in English, "Klip Viral" in Indonesian. Every panel text is a locale key
  `viral.*`, host error texts are `host.viral*` (via `acT`), engine texts (stage labels, warnings, review notes,
  marker comments "Score 77/100 (hook 80, flow ...)", summaries) are `viral.*` in `engine/ac/locales` and follow the
  job language.
- Premiere names are the same in both languages: bin "Klipora Viral" (`tlFindBin` also finds the old "AutoCut
  Viral"), sub-sequences "Viral 01 - <title>" (an empty title becomes "Klip" / "Clip"), markers "Viral 01 (77) <title>".
- Marker tag `[Klipora-VR]`; old `[AC-VR]` markers are cleared too (`bac_clearMarkersByTag` knows both tags).
- AI titles, hooks and "why" texts, transcript snippets and sequence names are content (`data-i18n-skip` in the
  review and result lists). AI prompts are not translated. `ui_tests/viral.mjs` ends with an English pass (setup,
  review, result: no Indonesian leftovers).

## What the user sees (panel, Bahasa Indonesia)

| Control | Default | Engine param |
|---|---|---|
| **Durasi klip** presets: 15-30 dtk (TikTok cepat), 20-60 dtk, 30-60 dtk (Reels/Shorts), 60-90 dtk (Cerita) | 20-60 dtk | `min`, `max` |
| Pengaturan lanjutan > **Durasi minimal** 5-120 / **maksimal** 15-180 dtk ("Custom" when no preset matches) | 20 / 60 | `min`, `max` |
| **Jumlah klip**: Otomatis / 3 / 5 / 10. Otomatis = about 1 clip per 3,5 mnt (max 20); short videos get what fits. Up to 1,5 x as many extra candidates are listed unchecked | Otomatis | `count` (0 = auto) |
| **Gaya** chips: Default, Edukasi, Lucu, Jualan, Cerita, Custom (Custom shows a text box; the run button stays disabled until it has text) | Default | `preset`, `prompt` |
| **Cari topik** (opsional), e.g. "domain cloudflare" | empty | `topic` |
| Hasil > **Buat sequence per klip** | on | panel only |
| Hasil > **Jadikan 9:16** (hidden unless the resize tool is available, see "9:16" below) | off | panel only |
| Pengaturan lanjutan > **Pakai AI** | on | `ai` |
| Source card scope: Seluruh sequence / In/Out / Clip terpilih | all | `scope` |

Above the settings: an alert when AI is off in Settings ("AI dimatikan di Pengaturan") or the proxy is down ("AI tidak
terhubung"), and "Lihat hasil terakhir (n klip, 04:21 WIB)" when a review of this sequence exists. The estimate line
says "Untuk 34:36: kira-kira 10 klip 20-60 dtk dipilih, sisanya cadangan." Primary: "Cari klip viral" (or "Cari klip
(tanpa AI)").

**Tinjau** (shared review list, class `viral-rv`): title "10 klip dipilih dari 15 ditemukan, total 6 mnt 42 dtk", a bar
with the source tag (Dinilai AI / Sebagian tanpa AI / Tanpa AI) and **Urut Skor | Waktu**. Each row: start timecode,
**score badge** (green 80+, yellow 60-79, orange below 60; the number is the information), "tanpa AI" / "topik"
tags, **title** (bold) and a **transcript snippet**, a note line ("Hook: <hook>. <why>", or a warning when the
title/hook contains a number the clip never says), duration + dipilih/dilewati. Click a row = Premiere playhead to the
clip start (`bac_seek`); active row buttons: Lompat, Simpan/Buang, **Putar** (plays the range, see host). Filters:
Semua, Dipilih, Dilewati, Skor 60+, Cocok topik (when a topic was given). "Tandai di timeline" = markers only (no
sequences). Primary: "Buat N klip" (or "Tandai N klip" when "Buat sequence per klip" is off).

**Hasil**: "9 klip viral siap", Klip / Total durasi / Skor tertinggi, a ribbon of the clips over the source, the
list of created sequences (each with **Buka**, the 9:16 version indented under its clip), "Buka yang asli", "Ubah
pilihan", **Hapus hasil** (two clicks: deletes only the sequences this panel created in this Premiere session and the
`[Klipora-VR]` markers, plus the "Klipora Viral" bin when this session created it and it is empty), "Lanjut ke Auto
Caption".

## Engine

### `analyze`

Stages: Baca transkrip, Ukur energi suara, Cari momen dengan AI (or "(tanpa AI)"), Nilai ulang dan urutkan (or
Urutkan), Siapkan tinjauan.

1. `Timeline.words_on_timeline()` (cache v3; transcribes once with the GPU lock when missing) -> display words ->
   utterance units (`ac.ai.text.sentences`, max unit length = min(20 s, max/2)). Units matching Whisper outro
   hallucinations (`transcript.HALLU_RE`) are dropped and split the material, as do scope range boundaries: a clip
   never crosses an unselected gap.
2. Energy: `envelope_on_timeline()` (cached, 10 ms); reference = Otsu threshold, median and p95 of voiced frames.
   Clip energy = 0.7 loudness (p75 vs median scaled to p95) + 0.3 dynamics (std / 8 dB). Tutorials measure 45-75.
3. **AI** (`ai.available()` and `ai` param): 7-min windows, 1-min overlap (34,6 mnt -> 6 windows), asked in parallel
   (`AI_MAX_PARALLEL`, 3) with grok-auto. Lines are `mm:ss-mm:ss text` without ids; the model returns
   `start/end/start_quote/end_quote/scores{hook,flow,value,trend}/title/hook/reason` for up to k clips
   (k = clamp(ceil(1,5 x count / windows) + 1, 2, 5)). Each window answer is cached (`ai.Cache`, key = window text +
   style + topic + min/max + k + glossary + `WIN_VERSION`): a re-click costs no quota.
4. Local post-processing of every answer: `prompts.resolve` (time + quote) inside the window, filler openers skipped
   (filler-only units and up to 3 leading words such as "Nah, jadi, oke, baik"), `fit()` grows/shrinks by whole
   units into [min, max] (keeps a valid model end; extends a mid-sentence end over gaps < 0,35 s; otherwise grows to
   about min + 40 % of the range while the speaker continues), pads 0,15 s before / 0,35 s after into the pauses.
   Window score = (0,35 hook + 0,2 flow + 0,25 value + 0,1 trend) / 0,9 scaled to 1 - w + w x energy
   (w = 0,1; Lucu 0,2; Cerita 0,15).
5. Windows that failed fall back to rules for their span (`source: "mixed"`, warning). When the model returned fewer
   candidates than count + extras, rule candidates fill the pool (`stats.rule_fill`).
6. NMS (overlap > 25 % of the shorter clip or IoU >= 0,3), keep count + max(2, count/2) (max 20), then ONE global
   **rescore** call (letters A..T, clip texts up to 600 chars, cached with `RESCORE_VERSION`): calibrated score
   ("at most one >= 85, most 40-75"), hook, title, why. Final = (0,9 - w) x rescore + 0,1 x window score + w x energy.
   A hook that only repeats the title is replaced by the window hook (or dropped). A degenerate rescore answer (every
   score 0, or one flat value for 4+ clips; seen live from grok-auto) is rejected by `rescore_sane`: window scores
   stay, warning "Penilaian ulang AI tidak wajar ...", nothing cached (an old cached one is asked again).
7. Topic: terms = topic words minus stopwords; match = share of terms said in the clip (prefix / fuzzy, misheard
   words tolerated). AI clips get +8 x match. Grounding: numbers in title/hook not said in the clip -> `ungrounded`,
   -10, warning note.
8. **Rules (no AI)**: every unit that starts after a 0,6 s pause (or with a hook/question word) is a start; fit as
   above; score 20 + 0,6 x (0,35 hook keywords in the first 8 s + 0,25 keyword density + w' energy + (0,4 - w')
   speech density) + topic (+15 x match, -12 when no term), clamped 1-85, `src: "rule"`, title = first words.
9. Review file `<workdir>\viral_review.json`: items `kind: "clip"` sorted best first, `on` = the best `count`.
   Extras per item: `score, scores{hook,flow,value,trend,energy[,density],ai}, title, hook, why, text, src (ai|rule),
   topic, ungrounded, rank, rescored, dur`. Doc extras: `source (ai|mixed|fallback), ai, warnings, fps, tag,
   settings, stats{windows, ai_calls, cached, failed, resolve, dropped, rescored, rule_fill, model, count, ms}`.
   A re-run with the same settings keeps the user's manual toggles (`review.carry_over`).
   Result: `{review, source, count, warnings, summary: "15 klip, 10 dipilih, skor tertinggi 77", top}`.
   Errors: `NO_SEQ`, `NO_WORDS` (no speech / scope without speech), `NO_CLIPS` ("Tidak ada klip 60-90 dtk yang cocok").

### `apply`

Edited review (`job.review`) -> checked items best first -> `{"plan": {"kind": "markers", "tag": "[Klipora-VR]",
"markers": [{t, end, name: "Viral 01 (77) <title>", comment: "Hook: ...\n<why>\nSkor 77/100 (hook 85, alur 90, ...)",
color, type: "Comment"}]}, "clips": [{id, t0, t1, name: "Viral 01 - <title>", title, hook, score, rank, color}],
"bin": "Klipora Viral", "summary"}`. Times are snapped outwards to frames (fps from `job.seq` or the review's `fps`).
Colour: 0 green (>= 80), 4 yellow (60-79), 3 orange (< 60). `NO_CLIPS` when nothing is checked.

### Measured (this PC, 2026-10-05, cached transcripts)

| Run | Result |
|---|---|
| 34,6 mnt, AI, cold | 6 windows + 1 rescore = 7 requests, 19-32 s; 15 clips 19-60 s, scores 39-77 (one window failed once: filled by rules, `mixed`) |
| 34,6 mnt, AI, cached | 0-1 requests, 5 s (rescore only when the pool changed) / 0,2 s fully cached |
| 2 mnt, AI (Jualan) | 1 window + rescore, 6 s |
| 34,6 mnt, rules | 0,1-0,2 s, 15 clips, best 69 |
| 49 s, rules | 1 clip (15,2-36,9 s) |
| Example AI picks | "Cloudflare Aman Website dari Hacker" (77, hook "Cloudflare lindungi webmu dari hacker"), "Cara Beli Domain dari Rumah Web" (71), "Cara Update Nameserver Domain .com" (70) |

## Host (`panel/host/36_viral.jsx`, ES3)

| Function | Does |
|---|---|
| `bac_viral_subseq(seqId, t0, t1, name, binName)` | Opens the source if needed, saves its In/Out, sets In = 0, Out = ceil(t1), In = floor(t0) (ticks), `seq.createSubsequence(true)` (true = ignore track targeting: every track), restores In/Out (`-400000` = unset), finds the new sequence (return value or new id), `acMarkMade`, unique name, moves it to bin "Klipora Viral" (created when missing), sets the new sequence's start time (zero point) to 0 (Premiere keeps the source timecode, e.g. ruler at 08:13:021), re-activates the source. Snap tolerance 0,01 frame (engine times have 6 decimals). `{ok, id, name, origId, origName, t0, t1, dur, ms}` |
| `bac_viral_play(t0, seqId)` | Frame-aligned seek, then tries QE `player.play(1)` or `qe.startPlayback()` when the sequence is active. `{ok, t, played, how}`; `played: false` -> panel toast "Tekan Spasi di Premiere untuk memutar." The panel stops it after the clip length with `bac_viral_stop()` |
| `bac_viral_cleanup(ids, binName)` | Deletes only sequences made by `bac_*` in this session; deletes the bin only when this session created it and it is empty. `{ok, deleted, refused, bin}` |
| `bac_viral_binInfo(name)` | `{ok, exists, n}` for a root bin (the panel checks "Auto Reframed Sequences" before the 9:16 stage) |
| `bac_viral_adopt(seqId, binName, dropBin)` | Moves a `bac_*`-made sequence (the 9:16 version) into "Klipora Viral"; deletes root bin `dropBin` when empty (passed only when it did not exist before the run). `{ok, moved, dropped}` |

Panel order in "Buat N klip": `bac_clearMarkersByTag("[Klipora-VR]")` FIRST (createSubsequence copies markers inside the
range), then one `bac_viral_subseq` per clip (progress, cancel between clips), then `bac_addMarkers` with the plan
(tag appended to the comment), then optional 9:16, then `bac_openSequence(source)`. Progress pane via `ctx.track`.
Esc (cancel) stops between clips. Nothing made yet: toast "Dibatalkan. Tidak ada yang berubah.". Some clips made: a
partial result card ("3 dari 15 klip dibuat", warning "Dibatalkan", Buka per clip, Hapus hasil) plus the toast
"Dibatalkan. 3 sequence klip sudah dibuat di bin Klipora Viral.", so nothing is left orphaned.

### 9:16

`AC.viral.verticalApi()`: (1) `AC.tools.get('resize').api.vertical({seqId, name, ratio: '9:16', tool: 'viral'})` ->
Promise `{id, name}` if the resize tool ever exposes it (preferred hook, not there today); (2) else, when the resize tool
is registered and its host file (`38_resize.jsx`) is loaded, its one-call Premiere Auto Reframe
`bac_resize_native(9, 16, "default", "<clip name> 9x16", clipSeqId)` (the switch then shows "Pakai Premiere Auto
Reframe. Untuk rekaman layar, hasil terbaik lewat Auto Resize > Pintar."); (3) else the switch is hidden. Each clip
sequence is opened before the call (Auto Reframe works on the timeline sequence); Auto Reframe analysis finishes in
the background. Premiere puts the result in a root bin "Auto Reframed Sequences"; the panel moves each one into
"Klipora Viral" (`bac_viral_adopt`) and drops that bin again when this run created it.

## Tests

```
python engine/tests/test_viral.py            # offline, ~3 s: helpers, rules on 34,6 mnt / 49 s / edited 15-clip
                                              # sequence, In/Out scope, topic, fake-AI AI path (anchoring, grounding,
                                              # rescore, per-window cache, one failed window -> mixed, rule fill),
                                              # proxy down, apply plan, NO_CLIPS, CLI protocol
python engine/tests/test_viral.py --live     # + real AI on the 2-min file (~3 requests)
node tools/ui_test.mjs tools/ui_tests/viral.mjs --seq long35           # 50 checks, real engine, AI off
AC_VIRAL_AI=1 node tools/ui_test.mjs tools/ui_tests/viral.mjs --seq long35   # AI on (uses the AI cache when warm)
```

## What the Premiere verifier must test (step by step, live 26.2.2)

Work on a sequence made from the 34,6-min test media (or the 2-min one) in bin `Klipora Test`; never save.

1. Reload the panel: no EXC / console lines, `AC.host.loadErrors` empty (36_viral.jsx loads), open `#tool/viral`.
2. Settings defaults: 20-60 dtk, Otomatis, Default. Estimate line matches the sequence length. If AI is up, run
   "Cari klip viral": stages arrive in order, Tinjau shows ~10 checked of ~15, badges/titles/snippets/hook notes.
3. Click a row: the CTI moves to the clip start (toast "Playhead Premiere ke ..."). Press **Putar** on the active row:
   record whether Premiere starts playing (`played`/`how` in the result: `AC.host.json('bac_viral_play', 12, '')`)
   and whether it stops after the clip length. If `played: false`, the toast must say "Tekan Spasi".
4. Set an In/Out on the source sequence first (e.g. 0:10-0:20) and note it. Untick a few rows, "Buat N klip":
   - one new sequence per checked clip named "Viral 01 - ...", in bin "Klipora Viral", 1:1 duration with the clip
     (frame exact: compare `dur` with t1 - t0), every video/audio track present, effects/captions of the source copied;
   - **the source sequence is unchanged** (clip count, duration, clip in/out) and its In/Out are back to 0:10-0:20;
     repeat with no In/Out set: both must be unset afterwards (`getInPoint()` = "-400000");
   - the source is the active sequence again at the end;
   - `[Klipora-VR]` markers on the source: one per clip, range = clip, colour green/yellow/orange by score, name
     "Viral 01 (77) ...", comment with Hook / why / Skor and the tag; the sub-sequences contain NO [Klipora-VR] markers;
   - run "Buat N klip" a second time: markers are replaced (not doubled), new sequences get unique names.
5. Check `createSubsequence(true)` on a sequence with untargeted / locked tracks and with a caption track: are all
   tracks copied? (Docs say true = ignore targeting.) Does Premiere open the new sequence (the panel re-activates the
   source either way)?
6. "Tandai di timeline" (Tinjau bulk action): markers only, no sequences.
7. Result card: **Buka** opens each clip; "Buka yang asli"; **Hapus hasil** (two clicks) deletes exactly the created
   sequences + `[Klipora-VR]` markers, refuses anything else, removes the bin only when empty and created this session.
8. With "Jadikan 9:16" on (visible because the resize tool is loaded): each clip gets a "<name> 9x16" Auto Reframe
   sequence (540x960 for 2292x960 media); the source is active again at the end; note the time per clip.
9. AI off (Settings or "Pakai AI"): run again -> "Tanpa AI" tags, warning line, plausible clips in < 1 s.
10. Cancel during "Buat N klip" (Esc): stops between clips, toast says how many were already made.
11. Restore: delete every test sequence/bin you made, restore the user's active sequence, never save.

## Live Premiere verification (2026-10-05, Premiere 26.2.2, verifier agent)

(Recorded before the Klipora rename: `[AC-VR]` / "AutoCut Viral" here are `[Klipora-VR]` / "Klipora Viral" today; the old names are still recognised.)

Test sequences from the 49 dtk and 34,6 mnt media in bin "Klipora Test" (the 34,6 mnt one decorated with an
untargeted V2/A2 clip, a locked V3 clip, Motion scale 150 on V1, a user marker and an SRT caption track) plus an
AutoCut-style cut (`bac_applyRemove`, 15 clips). Driven through the panel with real CDP mouse/keyboard input, checked
with ExtendScript; everything removed afterwards (sequence list and root items identical to before), user's sequence +
playhead (16,17 dtk) restored, project never saved. Screens (gitignored `--shots` folder): `viral.png` (Tinjau), `viral_result.png`,
`viral_timeline.png` (score-coloured [AC-VR] markers on the source), `viral_916.png` (clip vs Auto Reframe 9:16 frame),
`viral_result916.png`, `viral_cancel.png` (partial result after Esc), `viral_noai.png`, `viral_error.png` (NO_CLIPS).

| Check | Result |
|---|---|
| Reload, host load, console | OK: `loadErrors` [], `36_viral.jsx` loaded, no EXC, `AC.log.errors` empty all session |
| Defaults / estimate | 20-60 dtk, Otomatis, Default; "Untuk 34:37: kira-kira 10 klip"; "Untuk 0:49: kira-kira 1 klip" |
| Analyze AI, 34,6 mnt | 17,3 s (find 11,3 s + rescore 5,7 s), 15 found, 10 checked, scores 45-78; stages in order. Cached re-click 0,74 s |
| Analyze AI, 49 s / cut 15 clips | 6,3 s / 6,9 s, 1 clip each |
| Analyze no AI, 34,6 mnt | 0,25 s engine, "Tanpa AI" tag + warning line, rows tagged "tanpa AI" |
| Topic "cloudflare" (no AI) | topic clips on top, "Cocok topik 3" filter; Custom without text disables the run |
| In/Out scope 8:00-14:00 | all 5 clips inside; 0:10 range -> friendly NO_CLIPS error card |
| Row click | CTI to the clip start, frame aligned (2,8583) |
| Putar | `bac_viral_play` -> `{played: true, how: "qe.player"}`: Premiere plays; stopped after the clip (+0,3 s latency slack added) |
| Buat 8 klip (In/Out 0:10-0:20 set) | 1,2 s host (about 150 ms per clip); names "Viral 01 - ..", bin "AutoCut Viral"; durations frame exact; V1-V3/A1-A3 copied incl. untargeted + locked (lock kept), Motion 150 copied, caption track copied, user marker copied, no [AC-VR] inside; source content identical, In/Out back to 10/20, source active, no tab opened for the new sequences |
| No In/Out set | both "-400000" afterwards |
| Re-run | unique names ("... 2", "... 3"), markers replaced (1, not 2) |
| Markers | one per clip, range = clip, colour 0 (80+) / 4 (60-79) / 3 (<60), "Viral 01 (78) ..", comment Hook/why/Skor + [AC-VR] |
| Tandai di timeline | markers only, no sequences, re-run replaces |
| Cut sequence (15 clips) | sub-sequence has the 10 expected clips, in/out exact on V1 and A1 |
| Result card | Buka opens the clip; Buka yang asli re-activates the source; Hapus hasil deletes exactly the made sequences + [AC-VR] (user marker kept) + empty bin; a non-AutoCut sequence is refused |
| 9:16 | 2 clips 2,6 s total, 540x960 "<name> 9x16", reframe done after about 5 s, source active at the end |
| Esc during Buat 15 klip | stops between clips (13 made); now a partial result card + toast (was: toast only, 13 orphaned sequences) |

Fixed during verification:
- Sub-sequences were 2 frames too long (in -1 frame, out +1): engine times rounded to 6 decimals give 342,99996 frames
  and the 1e-6 snap tolerance floored/ceiled outwards. Tolerance now 0,01 frame; durations exact.
- Sub-sequence ruler started at the source timecode (08:13:021): zero point set to 0.
- The model (local proxy, `grok-auto`) returned a rescore with every score 0 (all "viewer skip"); it passed the schema, was cached, and the top clip
  scored 16. Now rejected (`rescore_sane`), not cached, window scores kept, warning shown.
- Esc after some clips left orphaned sequences with only a toast: partial result card with Buka / Hapus hasil.
- 9:16 sequences landed in Premiere's "Auto Reframed Sequences" bin and that bin stayed after Hapus hasil: moved into
  "Klipora Viral", the bin is dropped when this run created it.
- Active row button said "Simpan bagian ini" for a selected clip (it unchecks it): now "Lewati klip ini" / "Pilih klip ini".
- Hook note "menit!. Penjelasan" (double punctuation); In/Out estimate said "Video ini"; Riwayat lines for apply/make
  had no sequence name / used the work root.

## Known limits / UNVERIFIED

- The Auto Reframe 9:16 of a screen recording follows faces/subjects (Premiere's engine); Motion changes of the source
  clip are not kept in the 9:16 version.
- The source card picks up an In/Out change on the next Premiere event or panel focus (`AC.seq` refresh); setting
  In/Out from a script without either leaves the old scope.
- Titles from grok-auto are short and descriptive rather than catchy; scores on click-through tutorials are honestly low
  (40-77). The rules fallback titles are the first words of the clip.
- Proxy failures of single windows happen (seen once in 3 cold runs); they are filled by rules and flagged "mixed".
