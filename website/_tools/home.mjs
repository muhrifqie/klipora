import { ic, code, href, GH } from './lib.mjs';

const TPL = ['kuning-3d', 'neon', 'flash-sale', 'esports', 'pita-diskon', 'stiker-viral', 'goyang-hijau', 'bold-pop',
  'judul-emas', 'komik-ledak', 'promo-spanduk', 'neon-gamer', 'papan-kapur', 'hijau-viral', 'gradasi-api', 'catatan-stabilo'];
const tplName = (s) => s.split('-').map((w) => (w === '3d' ? '3D' : w[0].toUpperCase() + w.slice(1))).join(' ');

export function home(lang) {
  const t = (en, id) => (lang === 'en' ? en : id);
  const IMG = lang === 'en' ? '' : '-id';   // panel screenshots: English UI on EN pages, Indonesian on ID pages

  const tools = (list) => list.map(([icon, h, p]) => `
      <div class="tool"><span class="tool-ic">${ic(icon)}</span><div><h3>${h}</h3><p>${p}</p></div></div>`).join('');

  const figs = (alt) => TPL.map((s) => `<figure><img src="/assets/img/tpl-${s}.webp" width="200" height="282" decoding="async" alt="${alt} ${tplName(s)}"><figcaption>${tplName(s)}</figcaption></figure>`).join('');
  // The second copy only exists to make the loop seamless; hidden from assistive tech.
  const figsDup = TPL.map((s) => `<figure aria-hidden="true"><img src="/assets/img/tpl-${s}.webp" width="200" height="282" decoding="async" alt=""><figcaption>${tplName(s)}</figcaption></figure>`).join('');

  const faq = [
    [t('Is Klipora really free?', 'Klipora beneran gratis?'),
      t('<p>Yes. The panel and the engine are open source on GitHub. There is no account, no subscription and no watermark. The only possible cost is an AI provider you choose to plug in yourself, and every AI step is optional.</p>',
        '<p>Ya. Panel dan engine-nya open source di GitHub. Tanpa akun, tanpa langganan, tanpa watermark. Biaya hanya muncul kalau kamu sendiri memasang penyedia AI berbayar, dan semua langkah AI itu opsional.</p>')],
    [t('Does it work on macOS?', 'Bisa dipakai di macOS?'),
      t('<p>Not yet. Klipora is Windows only for now: the engine uses Windows features such as DPAPI for key storage, and it is tested on Windows 11 with Premiere Pro 2026 (26.x).</p>',
        '<p>Belum. Untuk sekarang Klipora khusus Windows: engine-nya memakai fitur Windows seperti DPAPI untuk menyimpan kunci, dan dites di Windows 11 dengan Premiere Pro 2026 (26.x).</p>')],
    [t('Will it touch my original sequence?', 'Apakah sequence asli saya diubah?'),
      t('<p>No. Cutting tools work on a cloned sequence placed next to yours, overlays go on their own named track, and markers are tagged so a re-run replaces them. Klipora never saves your project for you.</p>',
        '<p>Tidak. Alat potong bekerja di salinan sequence yang ditaruh di samping aslinya, overlay ditaruh di track sendiri, dan marker diberi tanda supaya diganti saat dijalankan ulang. Klipora juga tidak pernah menyimpan project kamu.</p>')],
    [t('Do I need a GPU?', 'Harus punya GPU?'),
      t('<p>No, but it helps. Transcription uses faster-whisper on an NVIDIA GPU (CUDA 12) when available, about 1 minute per 30 minutes of video on the test machine. Without a GPU it falls back to the CPU and is slower.</p>',
        '<p>Tidak wajib, tapi sangat membantu. Transkripsi memakai faster-whisper di GPU NVIDIA (CUDA 12) kalau ada, sekitar 1 menit per 30 menit video di PC uji. Tanpa GPU, otomatis pindah ke CPU dan lebih lambat.</p>')],
    [t('Do I need an AI subscription?', 'Perlu langganan AI?'),
      t('<p>No. Every tool has rule-based logic that works offline. AI only adds extras such as chapter titles, viral clip picks or reading a style from an image. If you want it, bring your own provider, including a fully local one with Ollama.</p>',
        '<p>Tidak. Semua alat punya logika berbasis aturan yang jalan offline. AI cuma menambah bonus seperti judul bab, pilihan klip viral, atau membaca gaya dari gambar. Kalau mau, pasang penyedia sendiri, termasuk yang sepenuhnya lokal lewat Ollama.</p>')],
    [t('What language is the panel in?', 'Panelnya pakai bahasa apa?'),
      t('<p>The panel interface is in Bahasa Indonesia today, and the text rules (fillers, numbers, Rupiah) are tuned for Indonesian speech. Whisper itself transcribes many languages, and you can change the spoken language in Settings.</p>',
        '<p>Tampilan panel saat ini berbahasa Indonesia, dan aturan teksnya (filler, angka, Rupiah) disetel untuk ucapan bahasa Indonesia. Whisper sendiri bisa mentranskripsi banyak bahasa, dan bahasa ucapan bisa diganti di Pengaturan.</p>')],
    [t('Why is the extension not in the Window menu?', 'Kenapa ekstensinya tidak muncul di menu Window?'),
      t(`<p>Klipora is an unsigned CEP extension, so Premiere hides it until debug mode is on. Set <code>PlayerDebugMode</code> for <code>CSXS.12</code> and restart Premiere. The <a href="${href(lang, 'docs', '#cep')}">install guide</a> has the exact command.</p>`,
        `<p>Klipora adalah ekstensi CEP tanpa tanda tangan, jadi Premiere menyembunyikannya sampai mode debug dinyalakan. Atur <code>PlayerDebugMode</code> untuk <code>CSXS.12</code> lalu buka ulang Premiere. Perintah lengkapnya ada di <a href="${href(lang, 'docs', '#cep')}">panduan pasang</a>.</p>`)],
  ];

  return `
<section class="hero">
  <div class="wrap hero-grid">
    <div>
      <span class="eyebrow">${t('Free and open source', 'Gratis dan open source')}</span>
      <h1>${t('<em>Premiere&nbsp;Pro</em>, minus the boring parts.', '<em>Premiere&nbsp;Pro</em>, tanpa bagian membosankan.')}</h1>
      <p class="lede">${t('A panel that cuts silence and retakes, captions every word and reframes for Shorts. Runs on your own PC.',
        'Panel yang memotong jeda dan take ulang, memberi caption per kata, dan membuat versi Shorts. Jalan di PC kamu sendiri.')}</p>
      <div class="cta-row">
        <a class="btn btn-primary" href="${GH}">${ic('brand-github')}${t('Get it on GitHub', 'Ambil di GitHub')}</a>
        <a class="btn btn-ghost" href="${href(lang, 'docs')}">${t('Install guide', 'Panduan pasang')}${ic('arrow-right')}</a>
      </div>
    </div>
    <div class="stage">
      <img class="shot-panel" src="/assets/img/panel-home${IMG}.webp" width="380" height="720" alt="${t('Klipora panel home screen in Premiere Pro, listing the cut, text and camera tools', 'Layar utama panel Klipora di Premiere Pro, berisi alat potong, teks, dan kamera')}" fetchpriority="high">
      <div class="phone" role="img" aria-label="${t('Preview of an animated word-by-word caption', 'Pratinjau caption animasi per kata')}">
        <p class="phone-cap" aria-hidden="true">${t('<span>Cut</span> <span>the</span> <span>silence,</span> <span>keep</span> <span>the</span> <span>story</span>', '<span>Buang</span> <span>jedanya,</span> <span>simpan</span> <span>cerita</span> <span>yang</span> <span>penting</span>')}</p>
        <span class="phone-tag">${t('word-by-word caption', 'caption per kata')}</span>
      </div>
    </div>
  </div>
</section>

<div class="facts">
  <div class="wrap">
    <ul>
      <li>${ic('brand-windows')}<span><strong>Windows 10/11</strong>${t('Premiere Pro 2026 (26.x)', 'Premiere Pro 2026 (26.x)')}</span></li>
      <li>${ic('cpu')}<span><strong>${t('Runs locally', 'Jalan lokal')}</strong>${t('faster-whisper on your GPU', 'faster-whisper di GPU kamu')}</span></li>
      <li>${ic('shield-check')}<span><strong>${t('Non-destructive', 'Tidak merusak')}</strong>${t('works on a cloned sequence', 'bekerja di salinan sequence')}</span></li>
      <li>${ic('sparkles')}<span><strong>${t('AI is optional', 'AI opsional')}</strong>${t('bring your own provider', 'pakai penyedia sendiri')}</span></li>
    </ul>
  </div>
</div>

<section id="features" aria-labelledby="cut-h">
  <div class="wrap cut-grid">
    <div>
      <div class="sec-head reveal">
        <span class="group-tag">${ic('scissors')}${t('Cut', 'Potong')}</span>
        <h2 id="cut-h">${t('Rough cut in seconds, not an afternoon.', 'Rough cut dalam hitungan detik, bukan seharian.')}</h2>
        <p class="lede">${t('Every cut is listed for review first. Uncheck anything, then apply to a clone of your sequence.', 'Setiap potongan ditampilkan dulu untuk ditinjau. Hapus centang yang tidak perlu, lalu terapkan ke salinan sequence.')}</p>
      </div>
      <div class="tool-list reveal">${tools([
        ['wave-sine', t('Silence cutting', 'Potong silence'), t('Threshold is computed from your audio, so it adapts to every mic. Word guard keeps quiet syllables: on a 34.6 min tutorial, 0 words were clipped in every preset.', 'Ambang dihitung otomatis dari audio, jadi cocok untuk mic apa pun. Pelindung kata menjaga suku kata pelan: di tutorial 34,6 menit, 0 kata terpotong di semua preset.')],
        ['message-circle-off', t('Filler word removal', 'Hapus filler'), t('Finds "eee", "emm", "hmm" from the transcript, a second listening pass and the sound itself. Habit words like "ya" or "oke" only when you turn them on.', 'Mencari "eee", "emm", "hmm" dari transkrip, dengar ulang, dan bentuk suaranya. Kata kebiasaan seperti "ya" atau "oke" hanya kalau kamu nyalakan.')],
        ['repeat-off', t('Repeated takes', 'Potong pengulangan'), t('Stutters, restarts, corrections and full retakes. The earlier attempt goes, your last take stays. Hear the edit before you apply it.', 'Gagap, mulai ulang, ralat, dan take ulang. Percobaan awal dibuang, take terakhir disimpan. Dengar hasil potongannya sebelum diterapkan.')],
        ['volume-off', t('Profanity censor', 'Sensor kata kasar'), t('Beep, mute or duck, with a loudness-matched tone. Context rules (and optional AI votes) tell a swear from an innocent word.', 'Bip, senyap, atau kecilkan, dengan nada yang menyesuaikan volume suara. Aturan konteks (dan voting AI opsional) membedakan makian dari kata biasa.')],
      ])}</div>
    </div>
    <div class="shot-col reveal">
      <img class="shot" src="/assets/img/panel-silence${IMG}.webp" width="380" height="720" loading="lazy" decoding="async" alt="${t('Silence tool showing the audio waveform, the automatic threshold line and the Natural preset', 'Alat potong silence dengan gelombang audio, garis ambang otomatis, dan preset Natural')}">
      <p class="caption">${t('Drag the threshold line on the waveform. The count updates live.', 'Geser garis ambang di gelombang audio. Jumlahnya langsung berubah.')}</p>
    </div>
  </div>
</section>

<section class="text-sec" aria-labelledby="text-h">
  <div class="wrap">
    <div class="cap-head reveal">
      <span class="group-tag">${ic('badge-cc')}${t('Text', 'Teks')}</span>
      <h2 id="text-h">${t('Captions that look designed, word by word.', 'Caption yang terlihat didesain, kata demi kata.')}</h2>
      <p class="lede">${t('51 built-in templates, previewed over your real frame and rendered as a transparent overlay on its own track.', '51 template bawaan, dipratinjau di atas frame asli, lalu dirender sebagai overlay transparan di track sendiri.')}</p>
    </div>
  </div>
  <div class="marquee" aria-label="${t('Caption template examples', 'Contoh template caption')}">
    <div class="marquee-track">${figs(t('Caption template', 'Template caption'))}${figsDup}</div>
  </div>
  <div class="wrap">
    <div class="cap-features reveal">
      <div><h3>${t('Per-word styling', 'Gaya per kata')}</h3><p>${t('Fonts, gradients, outlines, 3D and long shadows, glow, 10 background shapes, brush highlights on the active word.', 'Font, gradasi, garis tepi, 3D dan bayangan panjang, cahaya, 10 bentuk latar, sorotan kuas di kata aktif.')}</p></div>
      <div><h3>${t('Animation studio', 'Studio animasi')}</h3><p>${t('40 ready animations for in, active word, out and loop, plus a keyframe editor for your own.', '40 animasi siap pakai untuk masuk, kata aktif, keluar, dan berulang, plus editor keyframe sendiri.')}</p></div>
      <div><h3>${t('Sound effects per word', 'Efek suara per kata')}</h3><p>${t('Pops on key words, a cash sound on numbers, 12 built-in sounds or your own, mixed to one SFX track.', 'Pop di kata penting, bunyi kaching di angka, 12 suara bawaan atau suara sendiri, digabung ke satu track SFX.')}</p></div>
      <div><h3>${t('Copy style from an image', 'Tiru gaya dari gambar')}</h3><p>${t('Paste a screenshot of a caption you like. Klipora matches colors, a similar font, case, position and highlight.', 'Tempel screenshot caption yang kamu suka. Klipora meniru warna, font mirip, kapital, posisi, dan sorotan.')}</p></div>
      <div><h3>${t('Brand kit and saved styles', 'Brand Kit dan gaya tersimpan')}</h3><p>${t('Brand colors, fonts and always-correct brand words. Save styles, and new videos start from your last-used one.', 'Warna, font, dan kata brand yang selalu dieja benar. Simpan gaya, dan video baru mulai dari gaya terakhir dipakai.')}</p></div>
      <div><h3>${t('Auto chapters', 'Bab otomatis')}</h3><p>${t('YouTube-ready chapter markers and the 00:00 description text, editable before anything is added.', 'Marker bab siap YouTube plus teks 00:00 untuk deskripsi, bisa diedit sebelum ditambahkan.')}</p></div>
    </div>
  </div>
</section>

<section aria-labelledby="cam-h">
  <div class="wrap">
    <div class="sec-head reveal">
      <span class="group-tag">${ic('video')}${t('Camera', 'Kamera')}</span>
      <h2 id="cam-h">${t('Movement for footage that has none.', 'Gerakan kamera untuk rekaman yang diam.')}</h2>
    </div>
    <div class="bento reveal">
      <article class="cell c-zoom">
        <div class="zoom-copy">
          <span class="tool-ic">${ic('zoom-in')}</span>
          <h3>${t('Auto zoom that follows the action', 'Auto zoom yang mengikuti aksi')}</h3>
          <p>${t('For screen recordings it zooms toward clicks, typing and menus. For talking heads it punches in on emphasis and faces. Keyframes stay editable.', 'Untuk rekaman layar, zoom mengarah ke klik, ketikan, dan menu. Untuk wajah, punch-in di kata penekanan. Keyframe tetap bisa diedit.')}</p>
          <ul class="mini-list">
            <li>${ic('check')}<span>${t('Three modes: screen action, speech emphasis, rhythm', 'Tiga mode: aksi layar, penekanan bicara, ritmis')}</span></li>
            <li>${ic('check')}<span>${t('Ignores the taskbar and notifications', 'Mengabaikan taskbar dan notifikasi')}</span></li>
            <li>${ic('check')}<span>${t('Max zooms per minute keeps it calm', 'Batas zoom per menit supaya tetap tenang')}</span></li>
          </ul>
        </div>
        <img class="shot" src="/assets/img/panel-zoom${IMG}.webp" width="380" height="720" loading="lazy" decoding="async" alt="${t('Auto Zoom settings with the Follow screen action mode selected', 'Pengaturan Auto Zoom dengan mode Ikuti aksi layar terpilih')}">
      </article>
      <article class="cell c-angles">
        <span class="tool-ic">${ic('focus-centered')}</span>
        <h3>${t('Virtual camera angles', 'Angle kamera virtual')}</h3>
        <p>${t('Wide, medium and close crops on every cut, never the same twice in a row.', 'Crop lebar, sedang, dan dekat di tiap potongan, tidak pernah sama dua kali berturut-turut.')}</p>
        <div class="angle-boxes" aria-hidden="true"><span>100%</span><span>118%</span><span>140%</span></div>
      </article>
      <article class="cell c-podcast">
        <span class="tool-ic">${ic('microphone-2')}</span>
        <h3>${t('Podcast multicam', 'Podcast multicam')}</h3>
        <p>${t('The camera follows whoever is talking, with a wide shot for crosstalk. Audio is never cut.', 'Kamera mengikuti siapa yang bicara, dengan shot lebar saat bersahutan. Audio tidak pernah dipotong.')}</p>
      </article>
      <article class="cell c-broll">
        <span class="tool-ic">${ic('photo-video')}</span>
        <h3>${t('B-roll that leaves your clicks visible', 'B-roll yang tidak menutupi klikmu')}</h3>
        <p>${t('Picks moments from the transcript and fills them from your folder, Pexels or an AI still. Quiet screens get full-screen b-roll, busy ones get a corner card.', 'Memilih momen dari transkrip lalu mengisinya dari folder kamu, Pexels, atau gambar AI. Layar tenang dapat b-roll penuh, layar sibuk dapat kartu di pojok.')}</p>
      </article>
    </div>
  </div>
</section>

<section aria-labelledby="pub-h" style="padding-top:0">
  <div class="wrap duo">
    <div class="duo-col reveal">
      <span class="group-tag">${ic('upload')}${t('Publish', 'Publikasi')}</span>
      <h2 id="pub-h">${t('One long video, many short ones.', 'Satu video panjang, banyak video pendek.')}</h2>
      <ul class="mini-list">
        <li>${ic('flame')}<span><strong>${t('Viral clip finder', 'Klip viral')}</strong>${t('Scores moments 0 to 100 and makes one sub-sequence per clip you keep.', 'Memberi skor momen 0 sampai 100 dan membuat satu sub-sequence per klip yang kamu pilih.')}</span></li>
        <li>${ic('device-mobile')}<span><strong>${t('Auto resize to 9:16', 'Auto resize ke 9:16')}</strong>${t('Also 1:1 and 4:5. Framing follows the cursor, the action or the speaker, or use focus + blur for screen content.', 'Juga 1:1 dan 4:5. Framing mengikuti kursor, aksi, atau pembicara, atau pakai fokus + blur untuk konten layar.')}</span></li>
      </ul>
      <div class="clips">${['hijau-viral', 'kuning-3d', 'stiker-viral'].map((s) => `<img src="/assets/img/tpl-${s}.webp" width="200" height="282" loading="lazy" decoding="async" alt="${t('Vertical 9:16 clip with captions', 'Klip vertikal 9:16 dengan caption')}">`).join('')}</div>
      <p class="caption">${t('Each clip can come out vertical, captioned with the template you pick.', 'Tiap klip bisa langsung vertikal, dengan caption dari template pilihanmu.')}</p>
    </div>
    <div class="duo-col voice reveal">
      <span class="group-tag">${ic('ear')}${t('Voice', 'Suara')}</span>
      <h3 style="font-size:clamp(26px,3vw,36px)">${t('Clear Voice', 'Suara Jernih')}</h3>
      <p class="muted">${t('AI denoise with DeepFilterNet3, click and breath removal, loudness per platform and music ducking. Rendered to a new track, originals stay.', 'Denoise AI dengan DeepFilterNet3, hapus klik dan napas, volume per platform, dan musik mengecil saat bicara. Dirender ke track baru, aslinya tetap ada.')}</p>
      <div class="lufs"><b class="from">-20,4</b>${ic('arrow-right')}<b class="to">-14,0 LUFS</b><small>${t('Measured on a test recording, YouTube target.', 'Hasil ukur di rekaman uji, target YouTube.')}</small></div>
      <div class="duo-shots one">
        <img class="shot" src="/assets/img/panel-voice${IMG}.webp" width="380" height="720" loading="lazy" decoding="async" alt="${t('Clear Voice settings: style, target loudness, click and breath cleanup, AI denoise', 'Pengaturan Suara Jernih: gaya, target volume, hapus klik dan napas, denoise AI')}">
      </div>
    </div>
  </div>
</section>

<section id="how" aria-labelledby="how-h" class="text-sec">
  <div class="wrap">
    <div class="sec-head reveal"><h2 id="how-h">${t('How it works', 'Cara kerjanya')}</h2></div>
    <ol class="flow reveal">
      <li><h3>${t('Read the timeline', 'Baca timeline')}</h3><p>${t('The panel reads your active sequence: tracks, clips, In/Out or selected clips.', 'Panel membaca sequence aktif: track, clip, In/Out, atau clip terpilih.')}</p></li>
      <li><h3>${t('Analyze locally', 'Analisis lokal')}</h3><p>${t('A Python engine on your PC transcribes with Whisper, measures audio and looks at frames. Results are cached.', 'Engine Python di PC kamu mentranskripsi dengan Whisper, mengukur audio, dan membaca frame. Hasil disimpan di cache.')}</p></li>
      <li><h3>${t('Review the list', 'Tinjau daftarnya')}</h3><p>${t('Every cut, zoom or caption is a row you can check, uncheck and jump to in Premiere.', 'Setiap potongan, zoom, atau caption jadi baris yang bisa dicentang, dibatalkan, dan dilompati di Premiere.')}</p></li>
      <li><h3>${t('Apply to a clone', 'Terapkan ke salinan')}</h3><p>${t('Changes land on a copy of the sequence or a named track. One click opens the original again.', 'Perubahan masuk ke salinan sequence atau track khusus. Satu klik membuka yang asli lagi.')}</p></li>
    </ol>
  </div>
</section>

<section id="privacy" aria-labelledby="priv-h">
  <div class="wrap privacy">
    <div class="sec-head reveal">
      <h2 id="priv-h">${t('Your footage never leaves your PC.', 'Rekamanmu tidak pernah keluar dari PC.')}</h2>
      <p class="lede">${t('Transcription, audio analysis, rendering and denoise all run locally. Nothing is uploaded unless you turn on a cloud AI provider.', 'Transkripsi, analisis audio, render, dan denoise semuanya jalan lokal. Tidak ada yang diunggah kecuali kamu menyalakan penyedia AI cloud.')}</p>
    </div>
    <div class="priv-points reveal">
      <div>${ic('cloud-off')}<h3>${t('Offline by default', 'Offline sejak awal')}</h3><p>${t('Every tool has rule-based logic, so it works with AI switched off.', 'Semua alat punya logika berbasis aturan, jadi tetap jalan saat AI dimatikan.')}</p></div>
      <div>${ic('key')}<h3>${t('Keys encrypted', 'Kunci terenkripsi')}</h3><p>${t('API keys are stored with Windows DPAPI for your Windows user only and are never shown again.', 'Kunci API disimpan dengan Windows DPAPI khusus user Windows kamu dan tidak pernah ditampilkan lagi.')}</p></div>
      <div>${ic('eye')}<h3>${t('You see what is sent', 'Kamu tahu apa yang dikirim')}</h3><p>${t('With a cloud provider on, only transcript text (and a style image, if you use that feature) is sent.', 'Kalau penyedia cloud nyala, yang dikirim hanya teks transkrip (dan gambar gaya, kalau fitur itu dipakai).')}</p></div>
      <div>${ic('shield-check')}<h3>${t('No account, no telemetry', 'Tanpa akun, tanpa pelacakan')}</h3><p>${t('No sign-up and no usage tracking. The code is open, so you can check.', 'Tanpa daftar dan tanpa pelacakan pemakaian. Kodenya terbuka, jadi bisa kamu cek.')}</p></div>
    </div>
  </div>
</section>

<section id="ai" aria-labelledby="ai-h" style="padding-top:0">
  <div class="wrap ai-grid">
    <div class="reveal">
      <div class="sec-head" style="margin-bottom:28px">
        <h2 id="ai-h">${t('Bring your own AI, or none.', 'Pakai AI sendiri, atau tanpa AI.')}</h2>
        <p class="lede">${t('Set a primary provider and backups. If one fails, the next one takes over. If all fail, the rules still run.', 'Atur penyedia utama dan cadangan. Kalau satu gagal, yang berikutnya mengambil alih. Kalau semua gagal, aturan bawaan tetap jalan.')}</p>
      </div>
      <ul class="providers" aria-label="${t('Supported AI providers', 'Penyedia AI yang didukung')}">
        ${[['LP', t('Local proxy', 'Proxy lokal'), 1], ['OR', 'OpenRouter'], ['G', 'Gemini'], ['AI', 'OpenAI'], ['X', 'xAI'], ['A', 'Anthropic'], ['DS', 'DeepSeek'], ['GQ', 'Groq'], ['OL', t('Ollama (local)', 'Ollama (lokal)'), 1], ['{}', t('Custom endpoint', 'Endpoint custom')]]
          .map(([m, n, local]) => `<li${local ? ' class="local"' : ''}><span class="mono" aria-hidden="true">${m}</span>${n}</li>`).join('')}
      </ul>
    </div>
    <div class="reveal" style="display:grid;gap:16px">
      <p class="note">${t('Each provider has a Fast model, a Smart model and an optional Vision model. Pick them from the live model list.', 'Tiap penyedia punya model Cepat, model Pintar, dan model Vision opsional. Pilih dari daftar model yang diambil langsung.')}</p>
      <p class="note">${t('AI results are cached, so running a tool again on the same video does not cost another request.', 'Hasil AI disimpan di cache, jadi menjalankan alat lagi di video yang sama tidak memakan permintaan baru.')}</p>
      <p class="note">${t('AI never sets timestamps. Its answers are matched back to Whisper words and shown as suggestions you approve.', 'AI tidak pernah menentukan waktu. Jawabannya dicocokkan ke kata Whisper dan ditampilkan sebagai saran yang kamu setujui.')}</p>
    </div>
  </div>
</section>

<section id="install" aria-labelledby="inst-h" class="text-sec">
  <div class="wrap install">
    <div class="reveal">
      <div class="sec-head" style="margin-bottom:28px">
        <h2 id="inst-h">${t('Requirements', 'Kebutuhan')}</h2>
      </div>
      <dl class="req">
        <div><dt>${t('System', 'Sistem')}</dt><dd>${t('Windows 10 or 11, 64-bit (tested on 11)', 'Windows 10 atau 11, 64-bit (dites di 11)')}</dd></div>
        <div><dt>Premiere</dt><dd>Premiere Pro 2026 (26.x)</dd></div>
        <div><dt>Python</dt><dd>${t('3.14 (the version it is tested on)', '3.14 (versi yang dites)')}</dd></div>
        <div><dt>FFmpeg</dt><dd>${t('8.x full build on PATH', '8.x full build di PATH')}</dd></div>
        <div><dt>GPU</dt><dd>${t('Optional. NVIDIA with CUDA 12 for fast transcription', 'Opsional. NVIDIA dengan CUDA 12 untuk transkripsi cepat')}</dd></div>
        <div><dt>${t('Disk', 'Disk')}</dt><dd>${t('A few GB for the Whisper model and renders', 'Beberapa GB untuk model Whisper dan hasil render')}</dd></div>
      </dl>
    </div>
    <div class="reveal">
      <h2 class="sr-only">${t('Install steps', 'Langkah pasang')}</h2>
      <ol class="steps">
        <li><div><h3>${t('Get the code', 'Ambil kodenya')}</h3>${code(['git clone https://github.com/muhrifqie/klipora.git C:\\klipora'], t)}</div></li>
        <li><div><h3>${t('Install the engine', 'Pasang engine')}</h3><p>${t('Python packages plus FFmpeg.', 'Paket Python plus FFmpeg.')}</p>${code(['py -3.14 -m pip install --no-cache-dir -r C:\\klipora\\requirements.txt', 'winget install Gyan.FFmpeg'], t)}</div></li>
        <li><div><h3>${t('Allow unsigned extensions', 'Izinkan ekstensi tanpa tanda tangan')}</h3>${code(['reg add "HKCU\\Software\\Adobe\\CSXS.12" /v PlayerDebugMode /t REG_SZ /d 1 /f'], t)}</div></li>
        <li><div><h3>${t('Link the panel into Premiere', 'Hubungkan panel ke Premiere')}</h3>${code(['New-Item -ItemType Junction -Path "$env:APPDATA\\Adobe\\CEP\\extensions\\com.klipora.panel" -Target "C:\\klipora\\panel"'], t)}<p style="margin-top:12px">${t('Restart Premiere, then open <strong>Window &gt; Extensions</strong>. The full guide covers GPU setup and first run.', 'Buka ulang Premiere, lalu <strong>Window &gt; Extensions</strong>. Panduan lengkap membahas GPU dan pemakaian pertama.')} <a href="${href(lang, 'docs', '#install')}">${t('Read the docs', 'Baca dokumentasi')}</a></p></div></li>
      </ol>
    </div>
  </div>
</section>

<section id="faq" aria-labelledby="faq-h">
  <div class="wrap">
    <div class="sec-head reveal"><h2 id="faq-h">${t('Questions', 'Pertanyaan')}</h2></div>
    <div class="faq reveal">
      ${faq.map(([q, a]) => `<details><summary>${q}${ic('chevron-down')}</summary><div class="ans">${a}</div></details>`).join('\n      ')}
    </div>
  </div>
</section>

<section class="final" style="padding-top:0">
  <div class="wrap">
    <div class="final-box reveal">
      <div>
        <h2>${t('Spend your time on the story.', 'Pakai waktumu untuk ceritanya.')}</h2>
        <p>${t('Klipora is free and open source. Star it, report bugs, or send a pull request.', 'Klipora gratis dan open source. Kasih bintang, laporkan bug, atau kirim pull request.')}</p>
      </div>
      <a class="btn btn-primary" href="${GH}">${ic('brand-github')}${t('Get it on GitHub', 'Ambil di GitHub')}</a>
    </div>
  </div>
</section>

<script type="application/ld+json">${JSON.stringify({
    '@context': 'https://schema.org', '@type': 'SoftwareApplication', name: 'Klipora',
    applicationCategory: 'MultimediaApplication', operatingSystem: 'Windows 10, Windows 11',
    description: t('Open-source Adobe Premiere Pro panel with a local Python engine for silence cutting, filler removal, animated captions, auto zoom, reframing and voice cleanup.', 'Panel Adobe Premiere Pro open source dengan engine Python lokal untuk potong silence, hapus filler, caption animasi, auto zoom, reframe, dan pembersih suara.'),
    offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' }, downloadUrl: GH, inLanguage: lang,
  })}</script>
`;
}
