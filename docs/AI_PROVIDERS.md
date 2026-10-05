# Penyedia AI (profil, kunci, model)

Bagian 1 untuk pemakai (Bahasa Indonesia). Bagian 2 teknis untuk agen/dev (English).
Kode: `engine/ac/ai/providers.py`, `engine/ac/ai/keystore.py`, `engine/ac/ai/client.py`, `engine/ac/ai/tasks.py`,
`engine/cli.py ai ...`, panel `panel/js/core/ai_settings.js` + `panel/css/ai_settings.css`.
Tes: `python engine/tests/test_ai_profiles.py` (offline, server palsu) dan
`node tools/ui_test.mjs tools/ui_tests/ai_settings.mjs` (headless, engine asli + server palsu).

---

## 1. Panduan pemakai

### 1.1 Intinya
- AI di Klipora sekarang bisa memakai **beberapa penyedia**: Proxy lokal (kompatibel OpenAI, bawaan), OpenRouter, Gemini, OpenAI,
  xAI, Anthropic (Claude), DeepSeek, Groq, Ollama (lokal), atau endpoint lain yang kompatibel OpenAI.
- Kamu atur **urutan**: penyedia paling atas = **Utama**, sisanya **Cadangan**. Kalau yang utama mati, kunci
  ditolak, kuota habis, atau jawabannya rusak, Klipora otomatis pindah ke cadangan berikutnya. Kalau semua gagal,
  alat tetap jalan pakai **aturan bawaan** (tanpa AI).
- Tiap penyedia punya dua model: **Cepat** (bab, highlight, b-roll, rapikan teks, judul) dan **Pintar** (klip
  viral, pengulangan, filler). Opsional **Vision** untuk fitur yang membaca gambar.
- Bagian ini ikut bahasa panel (Indonesia atau English, Pengaturan > Tampilan). Contoh di English: "AI providers",
  "Add provider", "Local proxy (OpenAI-compatible)".
- Kalau belum pernah mengatur apa pun, Klipora memakai Proxy lokal dari `.env` (`AI_BASE_URL`, bawaan `http://127.0.0.1:8168/v1`).

### 1.2 Menambah penyedia
Pengaturan (ikon slider) > bagian **AI** > **Penyedia AI** > **Tambah penyedia**:
1. **Pilih penyedia.** Alamat URL langsung terisi.
2. **Hubungkan.** Tempel kunci API di kolom kunci lalu klik **Simpan kunci** (atau langsung lanjut, kunci ikut
   tersimpan). Kolom langsung dikosongkan. Kunci disimpan terenkripsi (Windows DPAPI, hanya user Windows kamu di
   PC ini yang bisa membuka) dan **tidak pernah ditampilkan lagi**. Panel hanya tahu "Tersimpan" atau "Belum diisi".
3. **Pilih model.** Klik **Ambil daftar model**, cari dengan kolom **Cari model**, lalu klik model untuk mengisi
   kolom yang sedang aktif (Cepat, lalu Pintar, lalu Vision). Kolom bisa juga diketik manual. Label di daftar:
   `128K`/`1M` = panjang konteks, `gambar` = bisa baca gambar, `gratis` = model gratis (OpenRouter).
4. **Tes koneksi** (mengirim 1 permintaan kecil, plus 1 gambar kecil kalau Vision diisi), lalu **Simpan**.
   Penyedia baru masuk ke akhir urutan sebagai cadangan.

Batal = draf dan kuncinya dihapus. Draf yang tertinggal lebih dari 1 jam dibersihkan otomatis.

### 1.3 Mengatur daftar
- Panah atas/bawah = ubah urutan. **Jadikan utama** = pindah ke paling atas.
- Klik baris untuk detail: jenis, alamat, status kunci, model, hasil tes terakhir.
- Saklar **Pakai penyedia ini** = keluarkan/masukkan ke urutan tanpa menghapus.
- **Ubah** = buka formulir lagi (kunci lama tetap, isi kolom kunci hanya kalau mau ganti).
- **Hapus kunci** / **Hapus** (klik dua kali untuk konfirmasi).
- Titik status: hijau = terhubung, merah = gagal (alasan di baris kecil), kuning = kunci belum diisi, abu-abu =
  belum dicek, lingkaran kosong = dimatikan. **Cek status** mengecek semua penyedia (gratis, tanpa kuota).

### 1.4 Penyedia
| Penyedia | Kunci | Catatan |
|---|---|---|
| Proxy lokal (kompatibel OpenAI) | opsional, dari `.env` `AI_API_KEY` (atau isi di sini) | Endpoint apa pun yang kompatibel OpenAI di PC ini, misalnya proxy lokal. Bawaan `http://127.0.0.1:8168/v1` |
| OpenRouter | openrouter.ai/keys | Satu kunci untuk banyak model, ada yang gratis |
| Gemini | aistudio.google.com/apikey | Ada kuota gratis harian |
| OpenAI | platform.openai.com/api-keys | Berbayar |
| xAI (Grok API) | console.x.ai | API resmi, berbayar |
| Anthropic (Claude) | console.anthropic.com/settings/keys | Berbayar, lewat Messages API asli |
| DeepSeek | platform.deepseek.com/api_keys | Murah, tidak bisa baca gambar |
| Groq | console.groq.com/keys | Sangat cepat, ada kuota gratis |
| Ollama (lokal) | tidak perlu | Model jalan di PC ini, transkrip tidak keluar |
| Custom | opsional | Endpoint lain yang kompatibel OpenAI (`/chat/completions`) |

Model bawaan tiap penyedia hanya saran. Klik **Ambil daftar model** untuk memilih yang benar-benar tersedia.

### 1.5 Privasi, biaya, risiko
- **Privasi:** transkrip (teks ucapan video) dikirim ke penyedia yang dipakai. Penyedia cloud bisa menyimpan data
  sesuai kebijakannya. Ollama memproses semuanya di PC ini.
- **Biaya:** penyedia berbayar memotong saldo per permintaan. Semua hasil AI disimpan di cache, jadi klik ulang
  pada video yang sama tidak bayar lagi. AI hanya jalan saat kamu klik tombolnya.
- **Proxy lokal:** kemampuan dan batasnya tergantung proxy yang kamu jalankan sendiri. Siapkan satu cadangan
  (misalnya Gemini atau OpenRouter) kalau proxy sedang penuh atau mati.

### 1.6 Kalau ada masalah
| Gejala | Artinya | Yang dilakukan |
|---|---|---|
| "kunci ditolak (HTTP 401/403)" | Kunci salah atau dicabut | Ubah > tempel kunci baru > Simpan kunci |
| "saldo atau kuota habis (HTTP 402)" / "dibatasi (HTTP 429)" | Kuota habis | Tunggu, isi saldo, atau pindah urutan |
| "tidak merespons" | Server mati / internet putus / Ollama belum jalan | Cek koneksi, lalu Cek status |
| "Penyedia ini tidak memberi daftar model" | Server tidak punya `/models` | Ketik nama model manual |
| Tes berhasil tapi "JSON lewat prompt" | Server tidak punya mode JSON | Normal, Klipora memperbaiki JSON sendiri |
| Penyedia dilewati beberapa menit | Pengaman: 3 kali gagal berturut-turut | Otomatis aktif lagi setelah 2 menit |

---

## 2. Technical reference

### 2.0 Language (Indonesian / English)
- Panel texts: locale keys `ai.*` (`panel/locales/{id,en}.json`, via `AC.t`). `AC.aiSettings.mount()` builds a fresh
  section on every call (Settings re-renders after `AC.i18n.set()`); profiles and an open wizard survive, and the
  list is re-read from the engine so engine texts follow the new language.
- Engine texts (errors + hints, health/test reasons, warnings, preset names and notes): `ac.i18n.tr("ai.*")`
  (`engine/ac/locales/{id,en}.json`). `cli.py ai ...` inherits `AC_LANG` from the panel (`AC.sys.childEnv`).
- Preset defaults stay Indonesian in `PRESETS` and in stored profiles (the `grok_local` preset's Indonesian name);
  `presets_public()` and `display_name(p)` translate an untouched default ("Local proxy (OpenAI-compatible)" in English), and `validate()` stores a translated
  default back as the Indonesian one. Cached health reasons (`ai_health.json`) keep the language of the probe that
  wrote them until the next probe ("Check status").
- HTTP `User-Agent: Klipora/2.0 (+stdlib urllib)`. Files moved with the rename to `%APPDATA%\Klipora` (migrated
  once from `%APPDATA%\AutoCutBOT` by `ac.util.appdata_dir`).

### 2.1 Files (no secrets anywhere but the encrypted key file)
| File | Content |
|---|---|
| `%APPDATA%\Klipora\ai_profiles.json` | `{"v":1, "order":[ids], "profiles":[{id, name, kind, base_url, model_fast, model_smart, vision_model, enabled, json_mode, extra_headers, draft?, created, touched?}]}`. Re-read on mtime change (worker stays current). Missing file = one synthesized `grok_local` profile from `.env` (never written until the first save) |
| `%APPDATA%\Klipora\ai_keys.bin` | `{"v":1, "keys": {profile_id: base64(DPAPI blob)}}`. `CryptProtectData`, CurrentUser scope, `CRYPTPROTECT_UI_FORBIDDEN`, app entropy `AutoCutBOT.ai.v1` (unchanged by the Klipora rename: changing it would make saved keys undecryptable; description "Klipora AI key"). No plaintext fallback (non-Windows raises) |
| `%APPDATA%\Klipora\ai_health.json` | `{profile_id: {ok, ms, why, at, probe?, tested?, json?, format?, vision?, model?, models?}}` (status dots) |
| `<repo>\.env` | Still read for the `grok_local` kind: `AI_BASE_URL`, `AI_API_KEY` (used when no keystore key is set for that profile), `AI_MODEL`/`AI_MODEL_SMART` (synthesized defaults), `AI_TIMEOUT`, `AI_MAX_PARALLEL`, `AI_DISABLED`, `AI_LOG`. Process env wins (tests set `AI_BASE_URL`) |

Validation (`providers.validate`): `kind` in the presets; `id` `^[a-z0-9][a-z0-9_-]{0,39}$` (generated from the kind:
`gemini`, `gemini_2`, ...); `base_url` http(s), no user/password, no key-like query, `localhost` -> `127.0.0.1`
(Windows: +2 s per request otherwise); plain `http://` to a non-local host -> warning; model names <= 200 chars;
`json_mode` `auto|on|off`; `extra_headers` <= 10, names like key/token/secret/authorization/cookie refused. Any
field whose name looks like a key (`api_key`, `key`, `token`, ...) -> error `AI_KEY_IN_PROFILE`.

### 2.2 Presets (`providers.PRESETS`)
| kind | base_url | adapter | needs key | json_fmt (auto) | fast / smart / vision (suggestions) |
|---|---|---|---|---|---|
| grok_local (Local proxy, OpenAI-compatible) | `.env` (`http://127.0.0.1:8168/v1`) | openai | optional (`.env` `AI_API_KEY`, loopback only) | no (proxy ignores it) | grok-fast / grok-auto / grok-auto |
| openrouter | `https://openrouter.ai/api/v1` + `HTTP-Referer: https://github.com/klipora/klipora`, `X-Title: Klipora` | openai | yes | yes | google/gemini-2.5-flash / google/gemini-2.5-pro / gemini-2.5-flash |
| gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | openai | yes | yes | gemini-2.5-flash / gemini-2.5-pro / gemini-2.5-flash |
| openai | `https://api.openai.com/v1` | openai | yes | yes | gpt-5-mini / gpt-5 / gpt-5-mini |
| xai | `https://api.x.ai/v1` | openai | yes | yes | grok-3-mini / grok-4 / grok-4 |
| anthropic | `https://api.anthropic.com/v1` | **anthropic** (native `/v1/messages`) | yes | n/a | claude-haiku-4-5 / claude-opus-5-5 / claude-opus-5-5 |
| deepseek | `https://api.deepseek.com/v1` | openai | yes | yes | deepseek-chat / deepseek-reasoner / - |
| groq | `https://api.groq.com/openai/v1` | openai | yes | yes | llama-3.1-8b-instant / llama-3.3-70b-versatile / - |
| ollama | `http://127.0.0.1:11434/v1` | openai | no | yes | (pick from the local list) |
| custom | (user) | openai | optional | no | (user) |

UNVERIFIED (no keys on this PC, by rule no paid calls were made): every cloud preset's live behaviour, the
suggested model names, `response_format` support per provider (a 400 mentioning `response_format`/JSON turns it off
for that profile and the request is retried without it), Anthropic's native endpoint with a real key (headers
`x-api-key` + `anthropic-version: 2023-06-01`, `max_tokens` 16000, text blocks joined, thinking blocks ignored,
`stop_reason: "refusal"` -> AIError -> next provider; JSON is enforced by prompt + local repair, no structured-output
parameter is sent). Verified offline against fake servers: request shapes, auth headers, model-list parsing
(OpenAI/OpenRouter/Groq-style `data[]`, Anthropic `data[]` with `max_input_tokens`, Gemini `models/` prefix strip,
404 -> "no list"), fallbacks, breaker, vision routing. Verified live: the local proxy path (`ai test grok_local`,
1 request, 3,4 s, JSON correct).

### 2.3 Routing (`ac.ai.client`)
- `route()` = enabled, non-draft profiles in `order`. `chat()` tries them in order; a profile is skipped when it has
  no credentials, its circuit is open, or its `/models` probe failed in the last 30 s. Per provider: 401/403 ->
  `AIUnavailable` + circuit 10 min; 402 -> 10 min; 429 -> 2 min; local proxy 503 (proxy has no capacity) -> 5 min; connection refused/DNS -> 1 min;
  400/404/413/422 (bad model, image unsupported, too big) -> next provider; 5xx/empty -> `retries` then next;
  bad JSON after the repair round -> next provider. 3 failed calls in a row open the circuit for 2 min. When every
  provider fails the caller gets `AIError` and runs its rule fallback (unchanged contract).
- Hardening (review AISEC, 2026-10-05, test `engine/tests/test_ai_security.py`):
  - Redirects are **never followed** (one opener with a no-redirect handler for chat, probe and model list). urllib
    would copy `Authorization`/`x-api-key` to the Location host (even plain http) and turn the POST into a GET. A 3xx
    = "alamat dialihkan ke tempat lain, cek URL": probe not ok, chat -> next provider + circuit 10 min, `ai models`
    -> `AI_REDIRECT`.
  - A read **timeout is not retried** (the provider may still be working and billing the first request): next
    provider at once, circuit 90 s.
  - The repair request budget (`repair`, default 1) is **shared by the whole route**, so N providers that all answer
    prose cost N + 1 requests, not 2N. A profile whose reply is not JSON at all (prose, refusal) in 2 calls in a row
    rests 5 min (schema/check slips do not count).
  - `{"error": {"code": 401|402|403|429}}` inside a 200 body (OpenRouter) = the same policy as that HTTP status. A
    non-JSON 200 body (HTML login, captive portal, wrong URL) -> next provider at once + circuit 2 min.
  - Probe: only 404/405/501 (no `/models`) and other 4xx count as reachable; 5xx = "server bermasalah (HTTP 5xx)".
  - A successful `ai test` writes `test_ok_at` to `ai_health.json`; a circuit opened before that time is closed in
    every process (the worker keeps its own breakers).
  - `.env` `AI_API_KEY` is sent only to the built-in profile id `grok_local` when its URL is loopback or equals the
    `.env` URL. A second local-proxy profile or an edited remote URL needs its own key (the wizard hides the
    local-proxy preset while that profile exists).
  - A stored key this Windows user cannot decrypt (other user, migrated profile, roaming copy) counts as missing:
    `keystore.key_error(id)`, `has_key=false` + `key_error` in `ai profiles`, skipped in routing with that reason, the
    panel shows "Kunci tidak bisa dibuka, simpan ulang". No unauthenticated request is sent.
  - Wizard drafts get `touched` on every save and `set-key`; `prune_drafts` removes drafts untouched for 1 h. If the
    key still vanished, the wizard's Simpan says "Kunci hilang, tempel lagi" instead of "disimpan".
- Models are **slots**: `chat(..., model="fast"|"smart"|"vision")`. `resolve_model(profile, model, first, vision)`:
  slot -> the profile's model (fast <-> smart fill in for each other); on `grok_local` any concrete name goes to the
  proxy unchanged; the preset's default aliases and common names (`grok-fast`, `grok-3-mini`, `grok-2`, `grok-3` -> fast; `grok-auto`, `grok-4`,
  `gpt-4o`, `gpt-4` -> smart) map to the slot on other providers (old tool code that hardcodes `grok-auto` keeps
  working); another concrete name is used on the first provider only, later providers use their smart model.
  Messages with image parts always use the `vision` slot; profiles without `vision_model` are skipped.
- `tasks.DEFAULT_MODEL` is slot based (`chapters/emphasis/moments/broll/cleanup/meta` -> fast; `viral/repeats/filler`
  -> smart). `tasks.run()` returns `model` (concrete) and `profile` (the one that answered); when an earlier
  provider failed it adds a warning like "<first provider> gagal, dijawab Gemini.".
- Result cache key = task + PROMPT_VERSION + `<profile>:<model>` + params + words. The `grok_local` profile keeps the
  old layout (just the model name), so answers cached before profiles existed still hit. Lookup uses the provider
  that would answer first; the answer is stored under the provider that actually answered. Rule fallbacks are never
  cached. (Tools with their own cache keys, e.g. chapters/viral/profanity/broll, key on the model name they pass.)
- `available()` = any provider in the route answers `GET /models` (free, cached 30 s per profile; 404/405/5xx on
  `/models` still counts as reachable). `health()` probes every route profile in parallel:
  `{ok, ms, base_url, model, has_key, disabled, why?, note?, circuit?, profile: {id, name, kind}, route: [{id, name,
  kind, has_key, ok, ms, why, circuit?}]}` (old keys unchanged; `ok` = at least one provider answers).
- `config()` keeps the old keys: `base_url`/`model` = the `.env` local proxy (the optional b-roll image generation in
  `tools/broll.py` posts to that proxy's image endpoint with the `.env` key, so that key is never sent to another provider); `has_key` = any route profile has
  credentials; new `active`, `route`.
- Vision for other agents: `ai.chat_vision(prompt, [path | bytes | data URL], schema=..., system=...)` or
  `ai.chat(messages_with_image_url_parts, model="vision")`. `ai.image_part(src)` builds an OpenAI `image_url` part
  (Pillow downscale to 1280 px, max 8 MB). The Anthropic adapter converts `image_url` data URLs to base64 image blocks.
- Other new helpers: `ai.list_models(pid)`, `ai.test_profile(pid, vision, quick)`, `ai.cache_identity(model)`,
  `ai.metrics_mark()` / `ai.served_since(mark)`, `ai.forget(pid=None)`; `chat(..., profile=pid)` asks one profile
  only (works while AI is off in Pengaturan, used by connection tests). `METRICS` rows now carry `profile`.

### 2.4 Secrets
- Keys enter only through `cli.py ai set-key <id>` on **stdin** (never argv, never a profile field), are encrypted at
  once, and are read back only inside the engine at request time (`_key_for`). Every key decrypted or stored by a
  process is registered in `keystore.revealed()`, which `ac.util.redact()` scrubs from logs, errors and events
  (an echoed `Incorrect API key provided: sk-...` never reaches the panel). `METRICS`/`AI_LOG` rows hold no key.
- Panel: the key field is `type=password`, `autocomplete=new-password`; its value is read and the field cleared in the
  same tick, then written to the child's stdin and dropped. Nothing about keys is stored in `localStorage`, the DOM,
  `AC.log` or job files (asserted by `ai_settings.mjs`).

### 2.5 `cli.py ai` (JSON lines: exactly one `result` or `error`; exit 0/1)
| Command | stdin | Result |
|---|---|---|
| `ai profiles [--probe]` | | `{profiles: [{id, name, kind, base_url, base_url_effective?, model_fast, model_smart, vision_model, enabled, json_mode, extra_headers, draft, has_key, key_error, key_from_env, needs_key, active, paid, health}], order, route, active, synthesized, disabled, presets: [{kind, name, base_url, model_fast, model_smart, vision_model, needs_key, key_url, note, paid, adapter}]}`. `--probe` refreshes `health` (free). Prunes drafts untouched for 1 h |
| `ai save [file]` | profile JSON, or `{profile, order}` | `{saved, warnings, order, profile}`. Partial patches work (`{"id":"gemini","enabled":false}`). `draft: true` keeps a wizard profile out of the route |
| `ai order` | JSON list of ids | `{order}` (unknown ids dropped, missing ids appended) |
| `ai activate <id>` | | `{active, order}` (moved to the front, enabled) |
| `ai remove <id>` | | `{removed, order}` (key + health row removed too) |
| `ai set-key <id>` | the key (first line) | `{id, has_key}` |
| `ai delete-key <id>` | | `{id, deleted, has_key}` (`grok_local` falls back to `.env`) |
| `ai models <id>` | | `{id, models: [{id, name?, ctx?, vision?, free?}], n, ms, unsupported}` |
| `ai test <id> [--vision] [--quick]` | | `{id, name, ok, why, reach: {ok, ms, why}, chat: {ok, ms, model, json, right, format: "response_format"|"prompt", repairs} , vision?: {ok, ms, model, answer}}`. `--quick` = probe only (free); otherwise 1 chat request (+1 image request with `--vision`) |
| `ai health` | | `client.health()` with the health cache written |

Error codes: `AI_BAD_ARGS`, `AI_BAD_PROFILE`, `AI_BAD_URL`, `AI_KEY_IN_PROFILE`, `AI_NO_PROFILE`, `AI_NO_KEY`,
`AI_AUTH`, `AI_HTTP`, `AI_OFFLINE`, `AI_STDIN`, `AI_KEYSTORE`, `INTERNAL`.

### 2.6 Panel (`AC.aiSettings`, mounted by `pages/settings_page.js` in the AI section)
`mount()` -> element; `refresh(probe)` -> Promise of the `ai profiles` snapshot (called on every Settings show);
`cli(args, stdinText, timeoutMs)` -> Promise of the result data (spawns `python -X utf8 engine/cli.py ai ...`, always
closes stdin); `idle()` -> Promise when queued commands finished (commands are serialized); `state()` for tests.
It also listens to `AC.bus 'health'` and paints the dots from `data.ai.route`. Wizard flow: preset -> URL + key
(first engine action saves a `draft` profile and `set-key`) -> `ai models` -> slot fill (search, click fills the
active slot and advances) -> `ai test` -> `ai save` with `draft:false, enabled:true` (appended as fallback). Cancel
removes the draft (new) or restores the old fields (edit).
