import { href, GH } from './lib.mjs';

export function changelog(lang) {
  const t = (en, id) => (lang === 'en' ? en : id);
  const li = (items) => `<ul>${items.map((x) => `<li>${x}</li>`).join('')}</ul>`;
  return `
<div class="doc-hero">
  <div class="wrap">
    <span class="eyebrow">${t('Changelog', 'Catatan rilis')}</span>
    <h1>${t('What changed', 'Yang berubah')}</h1>
    <p class="lede">${t(`Notable changes per release. Full history lives in the <a href="${GH}/commits">commit log</a>.`, `Perubahan penting per rilis. Riwayat lengkap ada di <a href="${GH}/commits">log commit</a>.`)}</p>
  </div>
</div>
<div class="wrap" style="padding-bottom:96px">

<article class="release" id="v3">
  <div class="meta"><span class="ver">3.0</span><time datetime="2026-10-05">${t('5 October 2026', '5 Oktober 2026')}</time></div>
  <div>
    <h3>${t('New', 'Baru')}</h3>
    ${li([
      t('<strong>AI providers and model picker.</strong> OpenRouter, Gemini, OpenAI, xAI, Anthropic, DeepSeek, Groq, Ollama, a local proxy or any OpenAI-compatible endpoint. Primary and backup order, Fast / Smart / Vision models per provider, keys encrypted with Windows DPAPI.', '<strong>Penyedia AI dan pilih model.</strong> OpenRouter, Gemini, OpenAI, xAI, Anthropic, DeepSeek, Groq, Ollama, proxy lokal, atau endpoint apa saja yang kompatibel OpenAI. Urutan utama dan cadangan, model Cepat / Pintar / Vision per penyedia, kunci dienkripsi dengan Windows DPAPI.'),
      t('<strong>Pro style tab.</strong> Real font previews, colors, outlines, shadows and 3D, glow, 10 background shapes, active word, position and tilt. Quick effects and reset per group.', '<strong>Gaya Pro.</strong> Contoh font asli, warna, garis tepi, bayangan dan 3D, cahaya, 10 bentuk latar, kata aktif, posisi dan kemiringan. Efek cepat dan reset per grup.'),
      t('<strong>Saved styles and last-used style.</strong> Save, rename, duplicate, favorite, export and import styles. New videos start from the last style used, remembered separately for vertical and horizontal.', '<strong>Gaya Saya dan gaya terakhir.</strong> Simpan, ganti nama, duplikat, favorit, ekspor, dan impor gaya. Video baru mulai dari gaya terakhir dipakai, diingat terpisah untuk vertikal dan horizontal.'),
      t('<strong>51 templates with a moving gallery.</strong> Grouped templates that animate on hover, plus random and mix styles.', '<strong>51 template dengan galeri bergerak.</strong> Template dalam grup yang beranimasi saat diarahkan mouse, plus Acak gaya dan Gabung gaya.'),
      t('<strong>Copy style from an image.</strong> Paste a caption screenshot and get matching colors, a similar font, case, position, highlight and animation.', '<strong>Tiru gaya dari gambar.</strong> Tempel screenshot caption, dapatkan warna, font mirip, kapital, posisi, sorotan, dan animasi yang sesuai.'),
      t('<strong>Brand kit</strong> with brand colors, fonts, brand words, emoji and tone, plus AI suggestions for words to highlight.', '<strong>Brand Kit</strong> dengan warna, font, kata brand, emoji, dan gaya bahasa, plus saran AI untuk kata yang disorot.'),
      t('<strong>Animation studio.</strong> 40 ready animations (in, active word, out, loop) with moving previews and a keyframe editor.', '<strong>Studio animasi.</strong> 40 animasi siap pakai (masuk, kata aktif, keluar, berulang) dengan pratinjau bergerak dan editor keyframe.'),
      t('<strong>Sound effects per word.</strong> Automatic sounds on key words, numbers, emoji or every page. 12 built-in sounds or your own, on one SFX track.', '<strong>Efek suara per kata.</strong> Bunyi otomatis di kata penting, angka, emoji, atau tiap halaman. 12 suara bawaan atau suara sendiri, di satu track SFX.'),
    ])}
    <h3>${t('Fixed', 'Perbaikan')}</h3>
    ${li([
      t('Saved styles and the last-used style can no longer be lost when two processes write at the same time.', 'Gaya Saya dan gaya terakhir tidak bisa hilang lagi saat dua proses menulis bersamaan.'),
      t('The saved-style card menu stays open while the list refreshes in the background.', 'Menu kartu Gaya Saya tetap terbuka saat daftar diperbarui di belakang.'),
      t('An empty saved-style list no longer hangs on "loading".', 'Daftar Gaya Saya yang kosong tidak lagi tertahan di "memuat".'),
    ])}
    <h3>${t('Known limits', 'Batasan yang diketahui')}</h3>
    ${li([
      t('Heavy templates (3D, long shadows) on very long videos need extra preparation time.', 'Template berat (3D, bayangan panjang) di video sangat panjang butuh waktu persiapan lebih lama.'),
      t('Templates designed for 16:9 look small on 9:16. Use the "fits this video" filter.', 'Template yang dirancang untuk 16:9 tampil kecil di 9:16. Pakai filter "Cocok".'),
      t('Copy style picks a similar font from the bundled collection, not the exact font.', 'Tiru gaya memilih font mirip dari koleksi bawaan, bukan font yang persis sama.'),
    ])}
  </div>
</article>

<article class="release" id="v2">
  <div class="meta"><span class="ver">2.0</span><time datetime="2026-10">${t('October 2026', 'Oktober 2026')}</time></div>
  <div>
    <h3>${t('New panel and engine', 'Panel dan engine baru')}</h3>
    ${li([
      t('Rebuilt panel with a home screen grouped by task, sibling tabs, a command palette, history, and a shared review list for every tool.', 'Panel dibangun ulang dengan layar utama per kelompok, tab antar alat, palet perintah, riwayat, dan daftar tinjau yang sama untuk semua alat.'),
      t('Local Python engine with JSON progress, cancel, a persistent worker that keeps Whisper warm, a GPU lock and a disk-space guard.', 'Engine Python lokal dengan progres JSON, tombol batal, worker yang menjaga Whisper tetap siap, kunci GPU, dan pengaman ruang disk.'),
      t('Everything works on a clone. The original sequence and media are never changed.', 'Semua bekerja di salinan. Sequence dan media asli tidak pernah diubah.'),
    ])}
    <h3>${t('Tools', 'Alat')}</h3>
    ${li([
      t('Silence cutting with an automatic threshold, word guard and four presets.', 'Potong silence dengan ambang otomatis, pelindung kata, dan empat preset.'),
      t('Filler removal with a second listening pass and an acoustic detector.', 'Hapus filler dengan dengar ulang dan detektor akustik.'),
      t('Repeated-take removal that keeps your last take, with a listen-before-apply preview.', 'Potong pengulangan yang menyimpan take terakhir, dengan pratinjau dengar sebelum diterapkan.'),
      t('Profanity censor with beep, mute or duck.', 'Sensor kata kasar dengan bip, senyap, atau kecilkan.'),
      t('Animated word-by-word captions rendered as a transparent overlay.', 'Caption animasi per kata yang dirender sebagai overlay transparan.'),
      t('Auto chapters, viral clip finder, auto zoom, auto resize, virtual angles, podcast multicam and b-roll.', 'Bab otomatis, klip viral, auto zoom, auto resize, angle otomatis, podcast multicam, dan b-roll.'),
      t('Clear Voice: DeepFilterNet3 denoise, click and breath removal, loudness per platform and music ducking.', 'Suara Jernih: denoise DeepFilterNet3, hapus klik dan napas, volume per platform, dan musik mengecil saat bicara.'),
    ])}
  </div>
</article>

<p class="muted">${t(`Looking for setup help? See the <a href="${href(lang, 'docs')}">docs</a>.`, `Butuh bantuan pemasangan? Lihat <a href="${href(lang, 'docs')}">dokumentasi</a>.`)}</p>
</div>`;
}
