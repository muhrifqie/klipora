import { ic, code, href, GH } from './lib.mjs';

export function docs(lang) {
  const t = (en, id) => (lang === 'en' ? en : id);
  const call = (kind, html) => `<div class="callout${kind === 'warn' ? ' warn' : ''}">${ic(kind === 'warn' ? 'alert-triangle' : 'info-circle')}<div>${html}</div></div>`;
  const shot = (img, alt) => `<img class="shot shot-inline" src="/assets/img/${img}${lang === 'en' ? '' : '-id'}.webp" width="380" height="720" loading="lazy" decoding="async" alt="${alt}">`;
  // Panel labels are Indonesian today; in English pages we show "Label (meaning)".
  const L = (idLabel, en) => (lang === 'en' ? `<strong>${idLabel}</strong> (${en})` : `<strong>${idLabel}</strong>`);

  const toolSec = (id, icon, title, body) => `<div class="tool-head" id="${id}"><span class="tool-ic">${ic(icon)}</span><h3>${title}</h3></div>${body}<div class="clear"></div>`;

  const TOOLS = [
    ['silence', t('Silence cutting', 'Potong silence')], ['fillers', t('Filler words', 'Hapus filler')],
    ['repeat', t('Repeated takes', 'Potong pengulangan')], ['profanity', t('Profanity censor', 'Sensor kata kasar')],
    ['captions', t('Animated captions', 'Caption animasi')], ['chapters', t('Auto chapters', 'Bab otomatis')],
    ['viral', t('Viral clip finder', 'Klip viral')], ['zoom', 'Auto zoom'], ['resize', t('Auto resize / reframe', 'Auto resize / reframe')],
    ['angles', t('Virtual angles', 'Angle otomatis')], ['podcast', 'Podcast multicam'], ['broll', 'B-roll'], ['voice', t('Clear Voice', 'Suara Jernih')],
  ];
  const tocList = (items) => `<ul>${items.map(([id, l]) => `<li><a href="#${id}">${l}</a></li>`).join('')}</ul>`;
  const toc = `
    <p>${t('Get started', 'Mulai')}</p>${tocList([['requirements', t('Requirements', 'Kebutuhan')], ['install', t('Install the engine', 'Pasang engine')], ['gpu', t('GPU setup', 'Atur GPU')], ['cep', t('Enable the panel', 'Aktifkan panel')], ['first-run', t('First run', 'Pemakaian pertama')]])}
    <p>${t('Tools', 'Alat')}</p>${tocList([['basics', t('Shared basics', 'Dasar bersama')], ...TOOLS])}
    <p>${t('More', 'Lainnya')}</p>${tocList([['ai', t('AI providers', 'Penyedia AI')], ['troubleshooting', t('Troubleshooting', 'Pemecahan masalah')], ['update', t('Update and uninstall', 'Update dan hapus')]])}`;

  const body = `
<div class="doc-hero">
  <div class="wrap">
    <span class="eyebrow">${t('Documentation', 'Dokumentasi')}</span>
    <h1>${t('Install and use Klipora', 'Pasang dan pakai Klipora')}</h1>
    <p class="lede">${t('From a clean Windows PC to your first automatic cut, then every tool in the panel.', 'Dari PC Windows yang masih bersih sampai potongan otomatis pertama, lalu semua alat di panel.')}</p>
    <details class="toc-mobile"><summary>${t('On this page', 'Di halaman ini')}</summary><nav class="toc" aria-label="${t('Contents', 'Daftar isi')}">${toc}</nav></details>
  </div>
</div>
<div class="wrap doc-layout">
  <nav class="toc" aria-label="${t('Contents', 'Daftar isi')}">${toc}</nav>
  <article class="prose">

<section id="requirements">
  <h2>${t('Requirements', 'Kebutuhan')}</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>${t('Part', 'Bagian')}</th><th>${t('Needed', 'Yang dibutuhkan')}</th></tr></thead>
    <tbody>
      <tr><td>${t('Operating system', 'Sistem operasi')}</td><td>${t('Windows 10 or 11, 64-bit. Tested on Windows 11. macOS is not supported yet.', 'Windows 10 atau 11, 64-bit. Dites di Windows 11. macOS belum didukung.')}</td></tr>
      <tr><td>Premiere Pro</td><td>${t('2026 (26.x). The panel is a CEP 12 extension.', '2026 (26.x). Panel ini ekstensi CEP 12.')}</td></tr>
      <tr><td>Python</td><td>${t('3.14 (the tested version), with pip.', '3.14 (versi yang dites), dengan pip.')}</td></tr>
      <tr><td>FFmpeg</td><td>${t('8.x full build (libass and NVENC) available on PATH.', '8.x full build (libass dan NVENC) tersedia di PATH.')}</td></tr>
      <tr><td>GPU</td><td>${t('Optional. An NVIDIA GPU with a recent driver runs Whisper on CUDA 12. Tested with 8 GB VRAM. Without it, the CPU is used.', 'Opsional. GPU NVIDIA dengan driver baru menjalankan Whisper di CUDA 12. Dites dengan VRAM 8 GB. Tanpa GPU, CPU yang dipakai.')}</td></tr>
      <tr><td>${t('Disk', 'Disk')}</td><td>${t('A few GB free for the Whisper model, caches and renders. Klipora checks free space before every render.', 'Beberapa GB kosong untuk model Whisper, cache, dan render. Klipora mengecek ruang kosong sebelum tiap render.')}</td></tr>
    </tbody>
  </table></div>
</section>

<section id="install">
  <h2>${t('Install the engine', 'Pasang engine')}</h2>
  <p>${t('Klipora has two parts: the panel that lives inside Premiere, and a Python engine that does the heavy work on your PC. Open <strong>PowerShell</strong> and run the commands below.', 'Klipora punya dua bagian: panel di dalam Premiere, dan engine Python yang mengerjakan proses berat di PC kamu. Buka <strong>PowerShell</strong> lalu jalankan perintah di bawah.')}</p>
  <h3>${t('1. Get the code', '1. Ambil kodenya')}</h3>
  <p>${t(`Clone the repository, or download the ZIP from <a href="${GH}">GitHub</a> and extract it. The examples use <code>C:\\klipora</code>.`, `Clone repository, atau unduh ZIP dari <a href="${GH}">GitHub</a> lalu ekstrak. Contoh di sini memakai <code>C:\\klipora</code>.`)}</p>
  ${code(['git clone https://github.com/muhrifqie/klipora.git C:\\klipora'], t)}
  <h3>${t('2. Python and packages', '2. Python dan paketnya')}</h3>
  <p>${t('Install Python 3.14 if you do not have it, then install the engine packages. <code>--no-cache-dir</code> keeps pip from filling drive C.', 'Pasang Python 3.14 kalau belum ada, lalu pasang paket engine. <code>--no-cache-dir</code> supaya pip tidak memenuhi drive C.')}</p>
  ${code(['winget install Python.Python.3.14', 'py -3.14 -m pip install --no-cache-dir -r C:\\klipora\\requirements.txt', '# NVIDIA GPU only (optional): CUDA 12 libraries for faster transcription', 'py -3.14 -m pip install --no-cache-dir -r C:\\klipora\\requirements-gpu.txt'], t)}
  <h3>${t('3. FFmpeg', '3. FFmpeg')}</h3>
  <p>${t('Klipora needs a full FFmpeg build (with libass for captions and NVENC for fast video renders). Open a new terminal afterwards so PATH is updated.', 'Klipora butuh FFmpeg full build (libass untuk caption, NVENC untuk render video cepat). Buka terminal baru setelahnya supaya PATH ter-update.')}</p>
  ${code(['winget install Gyan.FFmpeg', 'ffmpeg -version'], t)}
</section>

<section id="gpu">
  <h2>${t('GPU setup', 'Atur GPU')}</h2>
  <p>${t('Transcription uses faster-whisper. On an NVIDIA GPU it runs about 1 minute per 30 minutes of video on the test machine. The CUDA 12 libraries (cuBLAS, cuDNN) come from the pip packages in <code>requirements-gpu.txt</code>, so you only need a current NVIDIA driver.', 'Transkripsi memakai faster-whisper. Di GPU NVIDIA, prosesnya sekitar 1 menit per 30 menit video di PC uji. Library CUDA 12 (cuBLAS, cuDNN) terpasang dari paket pip di <code>requirements-gpu.txt</code>, jadi kamu cukup punya driver NVIDIA terbaru.')}</p>
  ${code(['# check that Windows sees the GPU and the driver', 'nvidia-smi'], t)}
  <ul>
    <li>${t('Default model: <code>large-v3-turbo</code>. The first transcription downloads it, so the first run takes longer.', 'Model bawaan: <code>large-v3-turbo</code>. Transkripsi pertama akan mengunduhnya, jadi run pertama lebih lama.')}</li>
    <li>${t('Only one Whisper job uses the GPU at a time. Others wait instead of running out of VRAM.', 'Hanya satu proses Whisper yang memakai GPU dalam satu waktu. Yang lain menunggu, bukan kehabisan VRAM.')}</li>
    <li>${t('No NVIDIA GPU, or low VRAM? Turn off <strong>Pakai GPU untuk transkripsi</strong> in Settings and the CPU is used.', 'Tidak punya GPU NVIDIA, atau VRAM kecil? Matikan <strong>Pakai GPU untuk transkripsi</strong> di Pengaturan, CPU akan dipakai.')}</li>
    <li>${t('Transcripts are cached next to the media, so every other tool reuses them for free.', 'Transkrip disimpan di cache di samping file media, jadi alat lain memakainya ulang tanpa proses lagi.')}</li>
  </ul>
</section>

<section id="cep">
  <h2>${t('Enable the panel in Premiere', 'Aktifkan panel di Premiere')}</h2>
  <p>${t('Klipora is not signed or sold through Adobe Exchange, so Premiere hides it until you allow unsigned extensions. Premiere Pro 26.x uses CEP 12, which reads the <code>CSXS.12</code> registry key.', 'Klipora tidak ditandatangani dan tidak dijual lewat Adobe Exchange, jadi Premiere menyembunyikannya sampai ekstensi tanpa tanda tangan diizinkan. Premiere Pro 26.x memakai CEP 12, yang membaca kunci registry <code>CSXS.12</code>.')}</p>
  <h3>${t('1. Turn on PlayerDebugMode', '1. Nyalakan PlayerDebugMode')}</h3>
  ${code(['reg add "HKCU\\Software\\Adobe\\CSXS.12" /v PlayerDebugMode /t REG_SZ /d 1 /f'], t)}
  ${call('info', t('This only changes your Windows user (HKCU), needs no admin rights, and only allows unsigned CEP panels to load. To undo it, set the value back to <code>0</code>.', 'Ini hanya mengubah user Windows kamu (HKCU), tidak perlu hak admin, dan hanya mengizinkan panel CEP tanpa tanda tangan dimuat. Untuk membatalkan, ubah nilainya kembali ke <code>0</code>.'))}
  <h3>${t('2. Link the panel folder', '2. Hubungkan folder panel')}</h3>
  <p>${t('Create a junction from the CEP extensions folder to the <code>panel</code> folder of the repo. A junction keeps one copy, so a <code>git pull</code> updates the panel too.', 'Buat junction dari folder ekstensi CEP ke folder <code>panel</code> di repo. Junction menjaga satu salinan saja, jadi <code>git pull</code> ikut meng-update panel.')}</p>
  ${code(['New-Item -ItemType Directory -Force "$env:APPDATA\\Adobe\\CEP\\extensions" | Out-Null', 'New-Item -ItemType Junction -Path "$env:APPDATA\\Adobe\\CEP\\extensions\\com.klipora.panel" -Target "C:\\klipora\\panel"'], t)}
  <h3>${t('3. Open it', '3. Buka panelnya')}</h3>
  <p>${t('Restart Premiere Pro, then choose <strong>Window &gt; Extensions</strong> and pick the Klipora panel. Dock it wherever you like. It works from 280 px wide.', 'Buka ulang Premiere Pro, lalu pilih <strong>Window &gt; Extensions</strong> dan klik panel Klipora. Taruh di mana saja. Panel tetap rapi mulai lebar 280 px.')}</p>
</section>

<section id="first-run">
  <h2>${t('First run', 'Pemakaian pertama')}</h2>
  <ol>
    <li>${t('Open <strong>Pengaturan</strong> (Settings, the slider icon). Under <strong>Mesin</strong> (engine), set <strong>Python</strong> to <code>py</code>, <code>python</code> or the full path to <code>python.exe</code>, then click <strong>Tes</strong>.', 'Buka <strong>Pengaturan</strong> (ikon slider). Di bagian <strong>Mesin</strong>, isi <strong>Python</strong> dengan <code>py</code>, <code>python</code>, atau path lengkap <code>python.exe</code>, lalu klik <strong>Tes</strong>.')}</li>
    <li>${t('Click <strong>Cek ulang</strong> (check again). Python, FFmpeg and GPU should turn green. The AI dot stays grey until you add a provider, which is fine.', 'Klik <strong>Cek ulang</strong>. Python, FFmpeg, dan GPU harus hijau. Titik AI tetap abu-abu sampai kamu menambah penyedia, dan itu normal.')}</li>
    <li>${t('Pick a <strong>Folder kerja</strong> (work folder) on a drive with free space. Reviews, renders and XML files go there, one folder per sequence.', 'Pilih <strong>Folder kerja</strong> di drive yang masih lega. File tinjauan, render, dan XML disimpan di sana, satu folder per sequence.')}</li>
    <li>${t('Open any sequence and start with <strong>Potong Silence</strong>. It is the fastest way to see the whole flow.', 'Buka sequence apa saja dan mulai dari <strong>Potong Silence</strong>. Ini cara tercepat melihat alur lengkapnya.')}</li>
  </ol>
</section>

<section id="basics">
  <h2>${t('Shared basics', 'Dasar bersama')}</h2>
  <p>${t('Every tool follows the same pattern, so once you know one you know them all.', 'Semua alat mengikuti pola yang sama, jadi kalau sudah paham satu, yang lain ikut mudah.')}</p>
  <ul>
    <li>${t('<strong>Scope</strong>: the source card at the top picks <em>Seluruh sequence</em> (whole sequence), <em>In/Out</em> or <em>Clip terpilih</em> (selected clips).', '<strong>Cakupan</strong>: kartu sumber di atas memilih <em>Seluruh sequence</em>, <em>In/Out</em>, atau <em>Clip terpilih</em>.')}</li>
    <li>${t('<strong>Presets first</strong>: each tool opens with sensible presets. Fine-tuning hides under <em>Pengaturan lanjutan</em> (advanced).', '<strong>Preset dulu</strong>: tiap alat dibuka dengan preset yang masuk akal. Pengaturan detail ada di <em>Pengaturan lanjutan</em>.')}</li>
    <li>${t('<strong>Tinjau</strong> (review): results come as a checklist. Click a row to move the Premiere playhead there, press Space to listen, uncheck what should stay. <em>Kirim ke marker</em> sends rows to timeline markers.', '<strong>Tinjau</strong>: hasil muncul sebagai daftar centang. Klik baris untuk memindah playhead Premiere ke sana, tekan Spasi untuk mendengar, hapus centang yang mau disimpan. <em>Kirim ke marker</em> mengubah baris jadi marker.')}</li>
    <li>${t('<strong>Apply</strong>: <kbd>Ctrl</kbd>+<kbd>Enter</kbd> runs the main button. Cuts land on a cloned sequence next to yours; <em>Buka yang asli</em> opens your original again.', '<strong>Terapkan</strong>: <kbd>Ctrl</kbd>+<kbd>Enter</kbd> menjalankan tombol utama. Potongan masuk ke salinan sequence di samping aslinya; <em>Buka yang asli</em> membuka sequence asli lagi.')}</li>
    <li>${t('<strong>Re-runs keep your choices</strong>: rows you toggled by hand stay that way when you analyze again.', '<strong>Jalankan ulang tetap ingat pilihanmu</strong>: baris yang kamu ubah manual tetap seperti itu saat dianalisis lagi.')}</li>
    <li>${t('<kbd>Ctrl</kbd>+<kbd>K</kbd> opens the command palette to jump to any tool.', '<kbd>Ctrl</kbd>+<kbd>K</kbd> membuka palet perintah untuk lompat ke alat mana pun.')}</li>
  </ul>

  <h2 style="margin-top:48px">${t('Tools', 'Alat')}</h2>

  ${toolSec('silence', 'wave-sine', t('Silence cutting', 'Potong silence') + (lang === 'en' ? ' <span class="muted" style="font-weight:500">(Potong Silence)</span>' : ''), `
  ${shot('panel-silence', t('Silence tool with waveform and presets', 'Alat potong silence dengan gelombang audio dan preset'))}
  <p>${t('Finds pauses across every unmuted audio track and removes them with a ripple on a clone. A frame only counts as silent when all included tracks are silent.', 'Mencari jeda di semua track audio yang tidak di-mute lalu membuangnya (ripple) di salinan. Sebuah frame dihitung diam hanya kalau semua track yang ikut juga diam.')}</p>
  <ol>
    <li>${t('Pick a style: <em>Santai</em>, <em>Natural</em> (default), <em>Cepat</em> or <em>Kilat</em>, from relaxed to tight.', 'Pilih gaya: <em>Santai</em>, <em>Natural</em> (bawaan), <em>Cepat</em>, atau <em>Kilat</em>, dari longgar ke rapat.')}</li>
    <li>${t('The threshold is automatic (calculated from the whole source file). Drag the line on the waveform or use the arrow keys to nudge it.', 'Ambang dihitung otomatis dari seluruh file sumber. Geser garis di gelombang audio atau pakai tombol panah untuk menyesuaikan.')}</li>
    <li>${t('Keep <strong>Lindungi kata</strong> (word guard) on. It needs a transcript; tick <em>Buat transkrip dulu</em> if there is none yet.', 'Biarkan <strong>Lindungi kata</strong> menyala. Fitur ini butuh transkrip; centang <em>Buat transkrip dulu</em> kalau belum ada.')}</li>
    <li>${t('Choose the result: remove pauses (ripple), markers only, or mute the pauses. Then review and apply.', 'Pilih hasil: buang jeda (ripple), tandai saja (marker), atau senyapkan jeda. Lalu tinjau dan terapkan.')}</li>
  </ol>
  <p>${t('Running it again on an already cut sequence finds no new gaps, because the threshold comes from the source media and not the cut timeline.', 'Menjalankan ulang di sequence yang sudah dipotong tidak menemukan jeda baru, karena ambang diambil dari media sumber, bukan dari timeline yang sudah dipotong.')}</p>`)}

  ${toolSec('fillers', 'message-circle-off', t('Filler words', 'Hapus filler') + (lang === 'en' ? ' <span class="muted" style="font-weight:500">(Hapus Filler)</span>' : ''), `
  ${shot('panel-fillers', t('Filler tool with hesitation and habit word options', 'Alat hapus filler dengan opsi suara ragu dan kata kebiasaan'))}
  <p>${t('Removes hesitations such as eh, ehm, emm, eee, hmm, uh and um. Three detectors work together: the main transcript, a second verbatim listening pass, and an acoustic detector that catches hesitations Whisper did not write down.', 'Membuang suara ragu seperti eh, ehm, emm, eee, hmm, uh, dan um. Tiga detektor bekerja bersama: transkrip utama, dengar ulang verbatim, dan detektor akustik yang menangkap suara ragu yang tidak ditulis Whisper.')}</p>
  <ul>
    <li>${t('<strong>Kepekaan</strong> (sensitivity): careful 80 %, balanced 65 % (default), or clean 50 %.', '<strong>Kepekaan</strong>: Hati-hati 80 %, Seimbang 65 % (bawaan), atau Bersih total 50 %.')}</li>
    <li>${t('Habit words (ya, oke, gitu, apa namanya...) are off until you pick them. After a run each chip shows how often the word sits at a phrase boundary.', 'Kata kebiasaan (ya, oke, gitu, apa namanya...) mati sampai kamu pilih. Setelah dijalankan, tiap chip menunjukkan seberapa sering kata itu ada di batas frasa.')}</li>
    <li>${t('Rows marked <em>Mepet</em> are glued to a real word. Listen before cutting those.', 'Baris bertanda <em>Mepet</em> menempel ke kata lain. Dengarkan dulu sebelum dibuang.')}</li>
    <li>${t('<em>Sekalian potong jeda</em> also removes pauses in the same pass, using your silence settings.', '<em>Sekalian potong jeda</em> ikut membuang jeda di proses yang sama, memakai pengaturan potong silence kamu.')}</li>
  </ul>`)}

  ${toolSec('repeat', 'repeat-off', t('Repeated takes', 'Potong pengulangan') + (lang === 'en' ? ' <span class="muted" style="font-weight:500">(Potong Pengulangan)</span>' : ''), `
  <p>${t('Finds stutters, restarts, corrections ("sorry, I mean...") and full retakes, and removes the earlier attempt so your last take always stays.', 'Mencari gagap, mulai ulang, ralat ("eh maaf, maksudnya...") dan take ulang, lalu membuang percobaan awal sehingga take terakhir selalu disimpan.')}</p>
  <ul>
    <li>${t('Presets <em>Ketat</em>, <em>Normal</em>, <em>Longgar</em> set the minimum confidence, sentence similarity and the maximum gap between takes.', 'Preset <em>Ketat</em>, <em>Normal</em>, <em>Longgar</em> mengatur keyakinan minimal, kemiripan kalimat, dan jarak maksimal antar take.')}</li>
    <li>${t('<strong>Dengar</strong> plays 2 seconds before and after the cut with the cut applied, so you hear the edit before committing.', '<strong>Dengar</strong> memutar 2 detik sebelum dan sesudah potongan dengan potongan sudah diterapkan, jadi kamu dengar hasilnya sebelum memutuskan.')}</li>
    <li>${t('Whisper hallucinations (text over silence) are flagged, never cut. <em>Minta pendapat AI</em> adds an opinion per row but never changes a checkbox.', 'Halusinasi Whisper (teks di atas keheningan) hanya ditandai, tidak dipotong. <em>Minta pendapat AI</em> menambah pendapat per baris tapi tidak pernah mengubah centang.')}</li>
  </ul>`)}

  ${toolSec('profanity', 'volume-off', t('Profanity censor', 'Sensor kata kasar') + (lang === 'en' ? ' <span class="muted" style="font-weight:500">(Sensor Kata Kasar)</span>' : ''), `
  ${shot('panel-profanity', t('Profanity tool with level and censor mode', 'Alat sensor kata kasar dengan tingkat dan cara sensor'))}
  <p>${t('Finds swear words (Indonesian, regional and English) in the transcript and a second listening pass, then censors the ones you keep checked.', 'Mencari kata kasar (Indonesia, daerah, dan Inggris) di transkrip dan dengar ulang, lalu menyensor yang tetap kamu centang.')}</p>
  <ul>
    <li>${t('Level: <em>Longgar</em>, <em>Normal</em> or <em>Ketat</em>. Words with an innocent meaning (for example animal names) are judged by context, with optional AI votes.', 'Tingkat: <em>Longgar</em>, <em>Normal</em>, atau <em>Ketat</em>. Kata yang punya arti biasa (misalnya nama hewan) dinilai dari konteks, dengan voting AI opsional.')}</li>
    <li>${t('Mode: <em>Bip</em> (a 1 kHz tone matched to your voice level), <em>Senyap</em> (mute), <em>Kecilkan</em> (duck by 20 dB) or your own sound.', 'Cara: <em>Bip</em> (nada 1 kHz menyesuaikan volume suaramu), <em>Senyap</em>, <em>Kecilkan</em> (turun 20 dB), atau suara kustom sendiri.')}</li>
    <li>${t('Keep personal lists: always censor, always allow. Captions can mask the same words automatically.', 'Simpan daftar sendiri: selalu sensor, selalu izinkan. Caption bisa ikut menyamarkan kata yang sama secara otomatis.')}</li>
  </ul>`)}

  ${toolSec('captions', 'badge-cc', t('Animated captions', 'Caption animasi') + (lang === 'en' ? ' <span class="muted" style="font-weight:500">(Auto Caption)</span>' : ''), `
  <p>${t('Word-by-word captions from the local transcript, previewed over your real frame and rendered as a transparent overlay on its own captions track. Edits re-render only the changed part.', 'Caption per kata dari transkrip lokal, dipratinjau di atas frame asli dan dirender sebagai overlay transparan di track caption sendiri. Edit hanya merender ulang bagian yang berubah.')}</p>
  <ol>
    <li>${t('<strong>Setup</strong>: pick a starting template, hide fillers in captions, tidy numbers and Rupiah, add glossary words that are often misheard. Then <strong>Buat caption</strong>.', '<strong>Setup</strong>: pilih template awal, sembunyikan filler di caption, rapikan angka dan Rupiah, tambah kata kamus yang sering salah dengar. Lalu <strong>Buat caption</strong>.')}</li>
    <li>${t('<strong>Template</strong>: 51 templates in groups (Shorts, tutorial, podcast, promo, cinematic, gaming and more). Hover to see the animation. <em>Cocok</em> filters templates that fit this video shape. <em>Acak gaya</em> and <em>Gabung gaya</em> mix styles.', '<strong>Template</strong>: 51 template dalam grup (Shorts, tutorial, podcast, promo, sinematik, gaming, dan lainnya). Arahkan mouse untuk melihat animasinya. <em>Cocok</em> memfilter template yang pas dengan bentuk video. <em>Acak gaya</em> dan <em>Gabung gaya</em> mencampur gaya.')}</li>
    <li>${t('<strong>Teks</strong>: edit words, split or merge pages, style a single word. Edits are tied to stable word ids and survive re-runs.', '<strong>Teks</strong>: edit kata, pisah atau gabung halaman, beri gaya satu kata saja. Edit terikat ke id kata yang stabil dan bertahan saat dijalankan ulang.')}</li>
    <li>${t('<strong>Gaya</strong> (style): fonts with real previews, colors and gradients, outline, shadow and 3D, glow, 10 background shapes, active word, position. Quick effects and a reset per group. <kbd>Ctrl</kbd>+<kbd>Z</kbd> works.', '<strong>Gaya</strong>: font dengan contoh asli, warna dan gradasi, garis tepi, bayangan dan 3D, cahaya, 10 bentuk latar, kata aktif, posisi. Ada efek cepat dan reset per grup. <kbd>Ctrl</kbd>+<kbd>Z</kbd> bisa dipakai.')}</li>
    <li>${t('<strong>Animasi</strong>: 40 animations (in, active word, out, loop), a keyframe <em>Studio</em>, and <em>Efek suara</em>: sounds on numbers, key words, emoji or every page, placed on one dedicated SFX track.', '<strong>Animasi</strong>: 40 animasi (masuk, kata aktif, keluar, berulang), <em>Studio</em> keyframe, dan <em>Efek suara</em>: bunyi di angka, kata penting, emoji, atau tiap halaman, ditaruh di satu track SFX khusus.')}</li>
    <li>${t('<strong>Ekspor</strong>: apply the overlay, or export native SRT captions. <em>Perbarui di timeline</em> swaps in a new version in place.', '<strong>Ekspor</strong>: terapkan overlay, atau ekspor caption SRT bawaan Premiere. <em>Perbarui di timeline</em> mengganti versi baru di tempat yang sama.')}</li>
  </ol>
  <h3>${t('Copy style from an image', 'Tiru gaya dari gambar')}</h3>
  <p>${t('Take a screenshot of a caption you like (<kbd>Win</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd>), open <strong>Template</strong> and use <em>Tiru gaya dari gambar</em> &gt; <em>Tempel</em>. Compare "your image" with the result, then use it or save it. With the AI image reader off, matching is free and offline; with it on, it uses one Vision request.', 'Screenshot caption yang kamu suka (<kbd>Win</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd>), buka <strong>Template</strong> lalu <em>Tiru gaya dari gambar</em> &gt; <em>Tempel</em>. Bandingkan gambar kamu dengan hasilnya, lalu pakai atau simpan. Dengan pembaca gambar AI mati, prosesnya gratis dan offline; kalau nyala, memakai satu permintaan Vision.')}</p>
  <h3>${t('Brand kit and saved styles', 'Brand Kit dan gaya tersimpan')}</h3>
  <p>${t('In <strong>Gaya</strong>, the Brand Kit bar stores 4 colors, a title font, brand words that are always spelled right, emoji and tone. <em>Sorot kata penting (AI)</em> suggests words to color. <em>Simpan gaya</em> saves the current look to <em>Gaya Saya</em> (my styles), with export and import as .json. New videos start from the style you used last, remembered separately for vertical and horizontal.', 'Di <strong>Gaya</strong>, bar Brand Kit menyimpan 4 warna, font judul, kata brand yang selalu dieja benar, emoji, dan gaya bahasa. <em>Sorot kata penting (AI)</em> menyarankan kata yang diwarnai. <em>Simpan gaya</em> menyimpan tampilan sekarang ke <em>Gaya Saya</em>, bisa ekspor dan impor .json. Video baru mulai dari gaya terakhir dipakai, diingat terpisah untuk vertikal dan horizontal.')}</p>`)}

  ${toolSec('chapters', 'list-numbers', t('Auto chapters', 'Bab otomatis') + (lang === 'en' ? ' <span class="muted" style="font-weight:500">(Bab Otomatis)</span>' : ''), `
  <p>${t('Creates YouTube chapters from the transcript: an editable list, chapter markers on the sequence, and the <code>00:00 Title</code> text for your description (copied to the clipboard and saved as .txt).', 'Membuat bab YouTube dari transkrip: daftar yang bisa diedit, marker bab di sequence, dan teks <code>00:00 Judul</code> untuk deskripsi (disalin ke clipboard dan disimpan sebagai .txt).')}</p>
  <ul>
    <li>${t('Edit titles and times, merge chapters, add one at the playhead. Live warnings follow YouTube rules (first at 00:00, at least 3 chapters, at least 10 seconds each).', 'Edit judul dan waktu, gabung bab, tambah bab di playhead. Peringatan langsung mengikuti aturan YouTube (pertama di 00:00, minimal 3 bab, masing-masing minimal 10 detik).')}</li>
    <li>${t('With AI: better titles, plus <em>Judul, deskripsi &amp; hashtag</em> for video titles, description and tags. Without AI: topic shifts and cue words, with titles flagged for you to check.', 'Dengan AI: judul lebih bagus, plus <em>Judul, deskripsi &amp; hashtag</em> untuk judul video, deskripsi, dan tag. Tanpa AI: pergantian topik dan kata penanda, dengan judul ditandai untuk kamu cek.')}</li>
  </ul>`)}

  ${toolSec('viral', 'flame', t('Viral clip finder', 'Klip viral') + (lang === 'en' ? ' <span class="muted" style="font-weight:500">(Klip Viral)</span>' : ''), `
  <p>${t('Finds the moments of a long video that work as Shorts, TikTok or Reels, scores them 0 to 100, and turns the ones you keep into one sub-sequence per clip, plus score-colored markers.', 'Mencari momen di video panjang yang cocok jadi Shorts, TikTok, atau Reels, memberi skor 0 sampai 100, lalu mengubah yang kamu pilih jadi satu sub-sequence per klip, plus marker berwarna sesuai skor.')}</p>
  <ul>
    <li>${t('Clip length presets from 15 to 90 seconds, count (automatic, 3, 5, 10), a style (education, funny, sales, story or custom) and an optional topic.', 'Preset durasi 15 sampai 90 detik, jumlah klip (otomatis, 3, 5, 10), gaya (edukasi, lucu, jualan, cerita, atau custom), dan topik opsional.')}</li>
    <li>${t('<em>Jadikan 9:16</em> also makes a vertical version of each clip with Auto resize.', '<em>Jadikan 9:16</em> sekalian membuat versi vertikal tiap klip dengan Auto Resize.')}</li>
    <li>${t('Works without AI (labelled "tanpa AI"), scoring by keywords, voice energy and speaking density.', 'Tetap jalan tanpa AI (diberi label "tanpa AI"), skor dari kata kunci, energi suara, dan kepadatan bicara.')}</li>
  </ul>`)}

  ${toolSec('zoom', 'zoom-in', 'Auto zoom', `
  ${shot('panel-zoom', t('Auto Zoom modes and strength', 'Mode dan kekuatan Auto Zoom'))}
  <p>${t('Punch-in zooms as editable Motion Scale and Position keyframes on a clone of your sequence.', 'Zoom punch-in sebagai keyframe Motion Scale dan Position yang bisa diedit, di salinan sequence.')}</p>
  <ul>
    <li>${t('<em>Ikuti aksi layar</em> (follow screen action, default): for screen recordings. Zooms toward clicks, typing, menus and cursor pauses, from 1.25x up to 2.2x.', '<em>Ikuti aksi layar</em> (bawaan): untuk rekaman layar. Zoom ke klik, ketikan, menu, dan kursor yang berhenti, dari 1,25x sampai 2,2x.')}</li>
    <li>${t('<em>Penekanan bicara</em> (speech emphasis): punch in on action and emphasis words, numbers and louder words, centred on the biggest face.', '<em>Penekanan bicara</em>: punch-in di kata aksi dan penekanan, angka, dan kata yang lebih keras, berpusat di wajah terbesar.')}</li>
    <li>${t('<em>Ritmis</em>: alternates wide and close every other sentence, like a two-camera shoot.', '<em>Ritmis</em>: bergantian lebar dan dekat tiap kalimat, seperti syuting dua kamera.')}</li>
    <li>${t('Set strength, a maximum zooms per minute, animation (smooth, fast, cut) and areas to ignore such as the taskbar.', 'Atur kekuatan, maksimal zoom per menit, animasi (halus, cepat, lompat), dan area yang diabaikan seperti taskbar.')}</li>
  </ul>`)}

  ${toolSec('resize', 'device-mobile', t('Auto resize and reframe', 'Auto resize dan reframe') + (lang === 'en' ? ' <span class="muted" style="font-weight:500">(Auto Resize)</span>' : ''), `
  <p>${t('Makes a 9:16, 1:1, 4:5 or 16:9 version of the sequence. Every result is a new sequence.', 'Membuat versi 9:16, 1:1, 4:5, atau 16:9 dari sequence. Setiap hasil adalah sequence baru.')}</p>
  <ul>
    <li>${t('<em>Pintar</em> (smart, default): framing follows the cursor, clicks, typing and page changes, or faces and the active speaker. Editable Position keyframes per clip.', '<em>Pintar</em> (bawaan): framing mengikuti kursor, klik, ketikan, dan pergantian halaman, atau wajah dan pembicara aktif. Keyframe Position per clip bisa diedit.')}</li>
    <li>${t('<em>Fokus + blur</em>: for screen content, a sharp window that follows the action over a blurred background, so forms and text are not cropped.', '<em>Fokus + blur</em>: untuk konten layar, jendela tajam yang mengikuti aksi di atas latar blur, supaya formulir dan teks tidak terpotong.')}</li>
    <li>${t('<em>Premiere Auto Reframe</em>: Premiere\'s own reframe, started from the panel.', '<em>Premiere Auto Reframe</em>: fitur reframe bawaan Premiere, dijalankan dari panel.')}</li>
  </ul>`)}

  ${toolSec('angles', 'focus-centered', t('Virtual camera angles', 'Angle kamera virtual') + (lang === 'en' ? ' <span class="muted" style="font-weight:500">(Angle Otomatis)</span>' : ''), `
  <p>${t('A single-camera "multicam": each clip gets a static wide, medium or close crop (100 / 118 / 140 % by default). The angle changes on every cut, never repeats twice in a row, and is anchored on the action or a face.', '"Multicam" dari satu kamera: tiap clip dapat crop statis lebar, sedang, atau dekat (bawaan 100 / 118 / 140 %). Angle berganti di tiap potongan, tidak pernah sama dua kali berturut-turut, dan berpusat di aksi atau wajah.')}</p>
  <ul>
    <li>${t('Presets <em>Halus</em>, <em>Normal</em>, <em>Sering</em>. Choose when to switch: on every cut, or only after a minimum shot length.', 'Preset <em>Halus</em>, <em>Normal</em>, <em>Sering</em>. Pilih kapan ganti: di setiap potongan, atau setelah durasi shot minimal.')}</li>
    <li>${t('Only one long clip? <em>Tambah potongan di awal kalimat</em> splits at sentence starts to create more angle changes.', 'Cuma satu clip panjang? <em>Tambah potongan di awal kalimat</em> memotong di awal kalimat supaya angle lebih sering berganti.')}</li>
  </ul>`)}

  ${toolSec('podcast', 'microphone-2', 'Podcast multicam', `
  ${shot('panel-podcast', t('Podcast multicam speaker mapping', 'Pemetaan pembicara podcast multicam'))}
  <p>${t('The camera follows whoever is talking. Put one mic track and one camera track per speaker (stacked and in sync), plus an optional wide camera.', 'Kamera mengikuti siapa yang bicara. Siapkan satu track mic dan satu track kamera per pembicara (bertumpuk dan sudah sinkron), plus kamera wide opsional.')}</p>
  <ul>
    <li>${t('Tracks are mapped automatically. Naming them "Mic Andi" and "Cam Andi" makes it exact. Up to 6 speakers.', 'Track dipetakan otomatis. Memberi nama "Mic Andi" dan "Cam Andi" membuatnya tepat. Maksimal 6 pembicara.')}</li>
    <li>${t('Review each shot on a speaker ribbon, pick another camera by hand, then apply. Inactive camera clips are disabled on a clone; audio is never cut or muted.', 'Tinjau tiap shot di pita pembicara, pilih kamera lain secara manual, lalu terapkan. Clip kamera yang tidak aktif dinonaktifkan di salinan; audio tidak pernah dipotong atau di-mute.')}</li>
  </ul>`)}

  ${toolSec('broll', 'photo-video', 'B-roll', `
  <p>${t('Finds moments in your speech that benefit from supporting footage and places clips on a new top video track of a clone.', 'Mencari momen dalam ucapan yang cocok diberi footage pendukung, lalu menaruh clip di track video teratas yang baru di salinan sequence.')}</p>
  <ul>
    <li>${t('Sources: your own folder, Pexels stock video (add a free Pexels API key in Settings) or an AI still with a slow Ken Burns move. Credits are written to a text file.', 'Sumber: folder kamu sendiri, video stok Pexels (isi kunci API Pexels gratis di Pengaturan), atau gambar AI dengan gerakan Ken Burns pelan. Kredit ditulis ke file teks.')}</li>
    <li>${t('Placement is activity aware: a quiet screen gets full-screen b-roll, a screen with action gets a picture-in-picture card in the quietest corner, a busy screen is skipped.', 'Penempatan memperhatikan aktivitas: layar tenang dapat b-roll penuh, layar dengan aksi dapat kartu PiP di pojok paling sepi, layar sibuk dilewati.')}</li>
  </ul>`)}

  ${toolSec('voice', 'ear', t('Clear Voice', 'Suara Jernih') + (lang === 'en' ? ' <span class="muted" style="font-weight:500">(Suara Jernih)</span>' : ''), `
  ${shot('panel-voice', t('Clear Voice settings', 'Pengaturan Suara Jernih'))}
  <p>${t('Makes your voice pleasant to listen to, non-destructively. The processed voice is rendered to a new audio track on a clone, and the original voice clips are disabled there.', 'Membuat suaramu enak didengar tanpa merusak aslinya. Suara hasil olahan dirender ke track audio baru di salinan, dan clip suara asli dinonaktifkan di sana.')}</p>
  <ul>
    <li>${t('<strong>Denoise</strong> with DeepFilterNet3 (runs on the CPU), with adjustable strength.', '<strong>Hilangkan noise</strong> dengan DeepFilterNet3 (jalan di CPU), kekuatannya bisa diatur.')}</li>
    <li>${t('<strong>Clicks and breaths</strong>: mouse and keyboard clicks between words are lowered, breaths are softened. Never inside a word. Each one is listed for review.', '<strong>Klik dan napas</strong>: bunyi klik mouse dan keyboard di sela kata dikecilkan, napas dihaluskan. Tidak pernah di dalam kata. Semuanya muncul di daftar tinjau.')}</li>
    <li>${t('<strong>Loudness per platform</strong>: YouTube -14, podcast -16, TikTok/Reels -14 LUFS or your own target, true peak -1 dBTP.', '<strong>Volume per platform</strong>: YouTube -14, podcast -16, TikTok/Reels -14 LUFS atau target sendiri, true peak -1 dBTP.')}</li>
    <li>${t('<strong>Music ducking</strong>: music tracks drop by 12 dB (adjustable) while you speak.', '<strong>Kecilkan musik saat bicara</strong>: track musik turun 12 dB (bisa diatur) saat kamu bicara.')}</li>
    <li>${t('Compare before and after in the result card. <em>Buka yang asli</em> goes back to the original.', 'Bandingkan sebelum dan sesudah di kartu hasil. <em>Buka yang asli</em> kembali ke sequence asli.')}</li>
  </ul>`)}
</section>

<section id="ai">
  <h2>${t('AI providers', 'Penyedia AI')}</h2>
  <p>${t('AI is optional. It improves chapter titles, viral clip picks, b-roll plans, retake opinions, ambiguous profanity, caption text cleanup and reading a caption style from an image. Without it, every tool falls back to its rules.', 'AI itu opsional. AI memperbaiki judul bab, pilihan klip viral, rencana b-roll, pendapat soal pengulangan, kata kasar yang ambigu, rapikan teks caption, dan membaca gaya caption dari gambar. Tanpa AI, semua alat memakai aturan bawaannya.')}</p>
  <h3>${t('Add a provider', 'Menambah penyedia')}</h3>
  <ol>
    <li>${t('Open <strong>Pengaturan</strong> &gt; <strong>AI</strong> &gt; <strong>Penyedia AI</strong> &gt; <strong>Tambah penyedia</strong> and pick a provider. The URL fills in.', 'Buka <strong>Pengaturan</strong> &gt; <strong>AI</strong> &gt; <strong>Penyedia AI</strong> &gt; <strong>Tambah penyedia</strong> lalu pilih penyedia. Alamat URL terisi sendiri.')}</li>
    <li>${t('Paste your API key and click <strong>Simpan kunci</strong>. The field empties right away; the key is encrypted with Windows DPAPI and never shown again.', 'Tempel kunci API lalu klik <strong>Simpan kunci</strong>. Kolom langsung dikosongkan; kunci dienkripsi dengan Windows DPAPI dan tidak pernah ditampilkan lagi.')}</li>
    <li>${t('Click <strong>Ambil daftar model</strong> and pick the <em>Cepat</em> (fast), <em>Pintar</em> (smart) and optional <em>Vision</em> models.', 'Klik <strong>Ambil daftar model</strong> lalu pilih model <em>Cepat</em>, <em>Pintar</em>, dan <em>Vision</em> (opsional).')}</li>
    <li>${t('<strong>Tes koneksi</strong>, then <strong>Simpan</strong>. Reorder with the arrows: the top one is primary, the rest are backups.', '<strong>Tes koneksi</strong>, lalu <strong>Simpan</strong>. Atur urutan dengan panah: paling atas jadi utama, sisanya cadangan.')}</li>
  </ol>
  <div class="table-wrap"><table>
    <thead><tr><th>${t('Provider', 'Penyedia')}</th><th>${t('Where to get a key', 'Tempat ambil kunci')}</th><th>${t('Notes', 'Catatan')}</th></tr></thead>
    <tbody>
      <tr><td>${t('Local proxy', 'Proxy lokal')}</td><td>${t('your own', 'milik sendiri')}</td><td>${t('Any OpenAI-compatible server on 127.0.0.1', 'Server apa saja yang kompatibel OpenAI di 127.0.0.1')}</td></tr>
      <tr><td>OpenRouter</td><td><code>openrouter.ai/keys</code></td><td>${t('One key, many models, some free', 'Satu kunci untuk banyak model, ada yang gratis')}</td></tr>
      <tr><td>Gemini</td><td><code>aistudio.google.com/apikey</code></td><td>${t('Daily free quota', 'Ada kuota gratis harian')}</td></tr>
      <tr><td>OpenAI</td><td><code>platform.openai.com/api-keys</code></td><td>${t('Paid', 'Berbayar')}</td></tr>
      <tr><td>xAI</td><td><code>console.x.ai</code></td><td>${t('Paid', 'Berbayar')}</td></tr>
      <tr><td>Anthropic</td><td><code>console.anthropic.com/settings/keys</code></td><td>${t('Paid, native Messages API', 'Berbayar, lewat Messages API asli')}</td></tr>
      <tr><td>DeepSeek</td><td><code>platform.deepseek.com/api_keys</code></td><td>${t('Low cost, no image input', 'Murah, tidak bisa baca gambar')}</td></tr>
      <tr><td>Groq</td><td><code>console.groq.com/keys</code></td><td>${t('Very fast, free quota', 'Sangat cepat, ada kuota gratis')}</td></tr>
      <tr><td>Ollama</td><td>${t('no key', 'tanpa kunci')}</td><td>${t('Runs on your PC, nothing leaves it', 'Jalan di PC kamu, tidak ada yang keluar')}</td></tr>
      <tr><td>Custom</td><td>${t('optional', 'opsional')}</td><td>${t('Any endpoint with <code>/chat/completions</code>', 'Endpoint apa saja dengan <code>/chat/completions</code>')}</td></tr>
    </tbody>
  </table></div>
  ${call('info', t('<strong>Privacy and cost.</strong> With a cloud provider, the transcript text (and the screenshot for "copy style from an image") is sent to that provider under its own policy. Paid providers charge per request. Results are cached, and AI only runs when you click its button.', '<strong>Privasi dan biaya.</strong> Dengan penyedia cloud, teks transkrip (dan screenshot untuk "tiru gaya dari gambar") dikirim ke penyedia itu sesuai kebijakannya. Penyedia berbayar memotong saldo per permintaan. Hasil disimpan di cache, dan AI hanya jalan saat kamu klik tombolnya.'))}
  ${call('warn', t('A local proxy that wraps a consumer web account (instead of an official API) can get that account rate-limited or blocked. Keep a second provider as a backup.', 'Proxy lokal yang memakai akun web biasa (bukan API resmi) bisa membuat akun itu kena batas atau diblokir. Siapkan satu penyedia lain sebagai cadangan.'))}
</section>

<section id="troubleshooting">
  <h2>${t('Troubleshooting', 'Pemecahan masalah')}</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>${t('Symptom', 'Gejala')}</th><th>${t('Fix', 'Solusi')}</th></tr></thead>
    <tbody>
      <tr><td>${t('Klipora is not in Window &gt; Extensions', 'Klipora tidak ada di Window &gt; Extensions')}</td><td>${t('Check the <code>CSXS.12</code> PlayerDebugMode value is the string <code>1</code>, the junction points at the <code>panel</code> folder (it must contain <code>CSXS\\manifest.xml</code>), then fully restart Premiere.', 'Pastikan nilai PlayerDebugMode di <code>CSXS.12</code> berupa teks <code>1</code>, junction mengarah ke folder <code>panel</code> (harus berisi <code>CSXS\\manifest.xml</code>), lalu tutup Premiere sepenuhnya dan buka lagi.')}</td></tr>
      <tr><td>${t('Panel opens blank', 'Panel terbuka kosong')}</td><td>${t('Close the panel and open it again from the Window menu. If it stays blank, check that the folder was not copied without its subfolders.', 'Tutup panel lalu buka lagi dari menu Window. Kalau tetap kosong, cek apakah folder tersalin lengkap dengan subfoldernya.')}</td></tr>
      <tr><td>${t('Python shows "Belum dicek" or red', 'Python "Belum dicek" atau merah')}</td><td>${t('Set the full path to <code>python.exe</code> in Settings (run <code>py -3.14 -c "import sys; print(sys.executable)"</code> to find it), then click Tes.', 'Isi path lengkap <code>python.exe</code> di Pengaturan (jalankan <code>py -3.14 -c "import sys; print(sys.executable)"</code> untuk mencarinya), lalu klik Tes.')}</td></tr>
      <tr><td>${t('FFmpeg red', 'FFmpeg merah')}</td><td>${t('Open a new terminal and run <code>ffmpeg -version</code>. If it is not found, reinstall with winget and restart Premiere so it sees the new PATH.', 'Buka terminal baru dan jalankan <code>ffmpeg -version</code>. Kalau tidak ditemukan, pasang ulang dengan winget lalu buka ulang Premiere supaya PATH baru terbaca.')}</td></tr>
      <tr><td>${t('GPU red or "GPU busy"', 'GPU merah atau "GPU sibuk"')}</td><td>${t('Update the NVIDIA driver. Only one transcription runs at a time, so wait for the other job or cancel it. Otherwise turn the GPU off in Settings and use the CPU.', 'Update driver NVIDIA. Hanya satu transkripsi yang jalan bersamaan, jadi tunggu proses lain atau batalkan. Kalau tetap gagal, matikan GPU di Pengaturan dan pakai CPU.')}</td></tr>
      <tr><td>${t('First transcription is slow', 'Transkripsi pertama lambat')}</td><td>${t('The Whisper model is downloaded on first use. After that, transcripts are cached and reused by every tool.', 'Model Whisper diunduh saat pertama dipakai. Setelah itu transkrip disimpan di cache dan dipakai ulang semua alat.')}</td></tr>
      <tr><td>${t('"Not enough disk space"', '"Ruang disk tidak cukup"')}</td><td>${t('Klipora refuses to render when the drive is nearly full. Move the work folder to another drive in Settings.', 'Klipora menolak render kalau drive hampir penuh. Pindahkan folder kerja ke drive lain di Pengaturan.')}</td></tr>
      <tr><td>${t('AI: key rejected (401/403)', 'AI: kunci ditolak (401/403)')}</td><td>${t('The key is wrong or revoked. <em>Ubah</em> &gt; paste a new key &gt; <em>Simpan kunci</em>.', 'Kunci salah atau dicabut. <em>Ubah</em> &gt; tempel kunci baru &gt; <em>Simpan kunci</em>.')}</td></tr>
      <tr><td>${t('AI: quota or rate limit (402/429)', 'AI: kuota habis atau dibatasi (402/429)')}</td><td>${t('Wait, top up, or move another provider to the top. Klipora already skips to the next one.', 'Tunggu, isi saldo, atau pindahkan penyedia lain ke atas. Klipora sudah otomatis lompat ke penyedia berikutnya.')}</td></tr>
      <tr><td>${t('AI: not responding', 'AI: tidak merespons')}</td><td>${t('Check the internet connection, or that Ollama / your proxy is running, then <em>Cek status</em>.', 'Cek koneksi internet, atau pastikan Ollama / proxy kamu jalan, lalu <em>Cek status</em>.')}</td></tr>
      <tr><td>${t('Cut sequence looks wrong', 'Sequence hasil potong terlihat salah')}</td><td>${t('Your original is untouched. Click <em>Buka yang asli</em>, adjust the settings or uncheck rows, and run again. Re-runs keep your manual choices.', 'Sequence aslimu tidak berubah. Klik <em>Buka yang asli</em>, ubah pengaturan atau hapus centang, lalu jalankan lagi. Pilihan manual tetap diingat.')}</td></tr>
    </tbody>
  </table></div>
  <p>${t(`Still stuck? <a href="${GH}/issues">Open an issue</a> with your Premiere version, the step you were on and the error text. Never paste API keys or <code>.env</code> files.`, `Masih bermasalah? <a href="${GH}/issues">Buka issue</a> dengan versi Premiere, langkah yang sedang dijalankan, dan teks error. Jangan pernah menempel kunci API atau file <code>.env</code>.`)}</p>
</section>

<section id="update">
  <h2>${t('Update and uninstall', 'Update dan hapus')}</h2>
  <h3>${t('Update', 'Update')}</h3>
  ${code(['cd C:\\klipora', 'git pull', 'py -3.14 -m pip install --no-cache-dir -r requirements.txt'], t)}
  <p>${t('Then close and reopen the panel in Premiere.', 'Lalu tutup dan buka lagi panel di Premiere.')}</p>
  <h3>${t('Uninstall', 'Hapus')}</h3>
  <p>${t('Remove the junction (this does not delete the repo), and optionally turn debug mode off again.', 'Hapus junction (ini tidak menghapus repo), dan kalau mau, matikan lagi mode debug.')}</p>
  ${code(['cmd /c rmdir "%APPDATA%\\Adobe\\CEP\\extensions\\com.klipora.panel"', 'reg add "HKCU\\Software\\Adobe\\CSXS.12" /v PlayerDebugMode /t REG_SZ /d 0 /f'], t)}
</section>

  </article>
</div>`;
  return body;
}
