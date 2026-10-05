# AutoCut BOT v3: yang baru (Auto Caption + AI)

Tanggal: 5 Oktober 2026 (WIB). Panel Premiere Pro 26.2.2 + engine Python.
Status: semua dites offline (engine) dan di Chrome headless (panel, dengan engine asli). **Belum ada yang dites di
Premiere live.** Itu bagian kamu, lihat **Checklist tes live** di bawah.

Catatan: dokumen ini memakai nama lama "AutoCut" (sekarang Klipora). Screenshot tes headless ditulis ke folder
`--shots` (bawaan `docs/shots`, tidak ikut di repo publik).

---

## 1. Yang baru

| Fitur | Singkatnya | Di mana |
|---|---|---|
| **Penyedia AI + pilih model** | AI tidak lagi hanya Proxy lokal (kompatibel OpenAI). Bisa OpenRouter, Gemini, OpenAI, xAI, Anthropic, DeepSeek, Groq, Ollama, atau endpoint custom. Ada urutan Utama dan Cadangan, plus model Cepat / Pintar / Vision per penyedia | Pengaturan > AI > Penyedia AI |
| **Gaya Pro** | Tab Gaya berisi semua pengaturan tampilan: huruf (pilih font dengan contoh asli), warna, garis tepi, bayangan dan 3D, cahaya, latar (10 bentuk), kata aktif, posisi dan kemiringan. Ada Efek cepat dan Reset per grup | Auto Caption > Gaya |
| **Gaya Saya + gaya terakhir** | Simpan gaya sendiri, lalu pakai di video lain. Bisa favorit, ganti nama, duplikat, ekspor, impor. Video baru otomatis mulai dari **gaya terakhir dipakai** (vertikal dan horizontal diingat terpisah) | Auto Caption > Template, tombol Simpan gaya |
| **51 template + galeri bergerak** | 51 template bawaan dalam grup (Shorts Viral, Tutorial, Podcast, Jualan/Promo, Sinematik, Edukasi, Minimal, Fun/Komik, Gaming, Berita, Aesthetic). Arahkan mouse ke kartu, animasinya diputar. Ada juga Acak gaya dan Gabung gaya | Auto Caption > Template |
| **Tiru gaya dari gambar** | Tempel screenshot caption orang lain, AutoCut meniru warna, font mirip, kapital, posisi, sorotan, animasi | Auto Caption > Template, baris paling atas |
| **Brand Kit** | Warna, font, kata brand, emoji, gaya bahasa per toko/channel. Bisa diterapkan ke template mana pun. Ada juga **Sorot kata penting (AI)** | Auto Caption > Gaya (bar atas) atau Pengaturan > Caption |
| **Studio animasi** | 40 animasi siap pakai (Masuk, Kata aktif, Keluar, Berulang) dengan pratinjau bergerak, plus editor keyframe sendiri | Auto Caption > Animasi > Pustaka / Studio |
| **Efek suara (SFX)** | Suara otomatis di kata penting, angka, emoji, atau tiap awal caption (Setiap halaman). 12 suara bawaan + suara sendiri, ditaruh di track audio "AutoCut SFX" | Auto Caption > Animasi > Efek suara |

---

## 2. Cara pakai

### 2.1 Penyedia AI dan model
1. Pengaturan (ikon slider) > **AI** > **Penyedia AI** > **Tambah penyedia**.
2. Pilih penyedia, tempel kunci API, klik **Simpan kunci**. Kunci disimpan terenkripsi dan tidak pernah ditampilkan lagi.
3. **Ambil daftar model**, cari, klik model untuk mengisi Cepat, lalu Pintar, lalu Vision (Vision dipakai Tiru gaya).
4. **Tes koneksi**, lalu **Simpan**. Atur urutan dengan panah. Yang paling atas = Utama.
5. Kalau semua penyedia gagal, alat tetap jalan pakai aturan bawaan (tanpa AI).

Panduan lengkap: `docs/AI_PROVIDERS.md` bagian 1.

### 2.2 Gaya Pro
1. Buat caption, buka tab **Gaya**.
2. Coba **Efek cepat** dulu (3D tebal, Bayangan panjang, Garis ganda, dan lainnya).
3. Rapikan per grup. Grup yang kamu ubah ditandai "Diubah". **Reset <grup>** di bawah tiap grup.
4. Font: klik tombol font, cari, filter Bawaan / Windows atau kategori. Ukuran visual dijaga saat ganti font.
5. Efek 3D dan Bayangan panjang punya slider **Salinan 3D** / **Salinan bayangan** (0 = otomatis).
6. Semua perubahan bisa Ctrl Z.

### 2.3 Gaya Saya dan gaya terakhir
- **Simpan**: tombol **Simpan gaya** (bintang) di bawah, isi nama, Enter. Kartu langsung muncul di **Gaya Saya**.
- Menu titik tiga di kartu: Ganti nama, Duplikat, Jadikan favorit, Ekspor (.json), Hapus (klik dua kali).
- **Impor gaya**: pilih file .json hasil ekspor. Nama yang sama otomatis dibedakan.
- **Gaya terakhir**: video baru langsung memakai gaya terakhir untuk bentuk video yang sama (9:16 dan 16:9 terpisah).
  Mau balik ke template asli? Klik **Pakai template asli**.

### 2.4 Template dan galeri bergerak
- Tab **Template**: cari, filter grup, **Cocok** = cocok dengan ukuran video ini.
- Arahkan mouse atau Tab ke kartu, animasinya diputar. Klik = pakai (ada Batalkan di notifikasi).
- **Acak gaya** = kombinasi acak yang tetap terbaca. **Gabung gaya** = ambil huruf dari A, warna dari B, dan seterusnya.
- **Pakai frame video ini** = gambar kecil template dibuat ulang di atas frame video kamu.

### 2.5 Tiru gaya dari gambar
1. Screenshot caption yang kamu suka (Win+Shift+S).
2. Tab Template > baris **Tiru gaya dari gambar** > **Tempel** (atau klik barisnya lalu Ctrl+V).
3. Lihat perbandingan "Gambar kamu" vs "Hasil AutoCut", lalu **Pakai gaya ini** atau **Simpan ke Gaya Saya**.
4. Saklar **Pakai AI pembaca gambar**: lebih akurat tapi pakai 1 permintaan AI (butuh model Vision). Mati = gratis, offline.

### 2.6 Brand Kit
1. Tab Gaya > bar Brand Kit > **Buat** (atau **Atur**).
2. Isi nama, 4 warna, font judul, kata brand (pisahkan koma), emoji, gaya bahasa.
3. **Simpan dan terapkan**. Template diwarnai ulang, kata brand selalu dieja benar dan diberi warna utama.
4. **Sorot kata penting (AI)**: AutoCut menyarankan kata yang diwarnai (brand, angka, kata kunci). Centang, lalu Terima.

Panduan lengkap: `docs/tools/captions_brand.md`.

### 2.7 Studio animasi
- **Pustaka**: pilih animasi Masuk, Kata aktif, Keluar, Berulang. Atur durasi dan jeda antar kata.
- **Studio**: pilih preset sebagai awal, ubah keyframe (skala, transparansi, geser, putar, blur, jarak huruf, warna) di
  timeline mini, atur easing, lalu **Pakai** atau **Simpan sebagai animasi saya**.
- Hasil asli: tombol **Putar 3 dtk** di bar pratinjau.

### 2.8 Efek suara
1. Tab Animasi > **Efek suara**.
2. Nyalakan aturan: **Angka** (Kaching), **Kata penting** (Pop), **Emoji**, **Setiap halaman**. Pilih suara dan keras suaranya.
3. Atur volume, maks. per menit, jeda minimal. Tambah suara sendiri kalau mau.
4. **Cek rencana bunyi**: lihat daftar titik suara. Lalu **Terapkan ke timeline**: satu file WAV di track audio "AutoCut SFX".
5. Terapkan lagi = mengganti versi lama. Track lain tidak disentuh.

---

## 3. Checklist tes live di Premiere

Siapkan: sequence uji `Tutorial - Episode 12` dari klip tes 49 dtk (`%KLIPORA_TEST_MEDIA%\talk_49s.mp4`). Untuk 9:16, buat sequence
1080x1920 dan taruh video yang sama. Jangan pakai project penting, dan jangan save kalau hanya tes.

### A. Panel dan AI
- [ ] Reload panel. Tidak ada pesan error, menu Auto Caption terbuka.
- [ ] Pengaturan > AI > Penyedia AI: ada **Proxy lokal (kompatibel OpenAI)** sebagai Utama, teks "Urutan: Proxy lokal, lalu aturan bawaan".
- [ ] Tambah penyedia (misalnya Gemini atau OpenRouter), tempel kunci, **Simpan kunci**. Kolom langsung kosong, status "Tersimpan".
- [ ] **Ambil daftar model**: daftar muncul, bisa dicari, klik mengisi Cepat lalu Pintar.
- [ ] **Tes koneksi**: hijau. **Simpan**: penyedia masuk ke urutan sebagai Cadangan.
- [ ] Klik **Cek status**: titik hijau di penyedia yang benar.

### B. Buat caption + template
- [ ] Auto Caption > **Buat caption** di sequence 49 dtk. Muncul sekitar 18 halaman, pratinjau di atas frame asli.
- [ ] Tab Template: 51 template, grup bisa difilter. Arahkan mouse ke kartu: animasinya bergerak.
- [ ] Klik **Hormozi Kuning**, pratinjau berubah. Ctrl Z: kembali.
- [ ] **Acak gaya** 2x, lalu **Gabung gaya** (A = Kuning 3D, B = Neon): hasilnya terbaca.
- [ ] Baris **Tiru gaya dari gambar** terlipat. Galeri template terlihat tanpa scroll jauh.

### C. Gaya Pro
- [ ] Tab Gaya: klik Efek cepat **3D tebal**. Pratinjau menampilkan teks 3D.
- [ ] Grup Bayangan dan 3D: slider **Salinan 3D** muncul (0 sampai 32). Isi 8, pratinjau tetap 3D.
- [ ] Ganti font lewat tombol font: contoh di daftar memakai font aslinya, ukuran teks di pratinjau kurang lebih sama.
- [ ] Latar: pilih bentuk **Pita** lalu **Stiker**. Tampilan berubah.
- [ ] **Reset** grup Huruf: hanya font kembali, warna tetap.

### D. Gaya Saya + gaya terakhir
- [ ] **Simpan gaya** > nama "Tes Toko" > Enter. Kartu muncul di Gaya Saya, notifikasi punya tombol **Lihat**.
- [ ] Menu kartu > **Ekspor**: dialog simpan Windows muncul, file .json tersimpan.
- [ ] Menu kartu > **Hapus**: tulisan berubah jadi "Yakin? Klik lagi", klik lagi, kartu hilang.
- [ ] **Impor gaya** dengan file tadi: kartu kembali.
- [ ] Ekspor template **Mr Beast**, impor lagi: kata aktif TIDAK jadi kuning (tetap tanpa sorot).
- [ ] Ekspor **Hook Tengah**, impor lagi: ukuran teks sama dengan aslinya.
- [ ] Buka sequence 16:9 lain > Auto Caption: kartu **Gaya terakhir dipakai** muncul dengan gaya tadi.
- [ ] Buka sequence 9:16: TIDAK memakai gaya 16:9 tadi (mulai dari Hormozi Kuning, atau gaya vertikal terakhir).

### E. Tiru gaya + Brand Kit
- [ ] Screenshot caption dari video TikTok/YouTube (Win+Shift+S), Template > Tiru gaya > **Tempel**.
  Kartu terbuka, muncul "Gambar kamu" vs "Hasil AutoCut" dan kotak atribut dengan persen.
- [ ] **Pakai gaya ini**: caption berubah. Ctrl Z: kembali.
- [ ] **Simpan ke Gaya Saya**: kartu muncul di Gaya Saya.
- [ ] Ctrl+V langsung (tanpa klik tombol). Kalau tidak bereaksi, catat (Premiere bisa menahan tombol).
- [ ] Brand Kit > Buat: isi warna dan kata brand "TokoKita", **Simpan dan terapkan**. Kata TokoKita berwarna utama.
- [ ] Di lembar Brand Kit: Tab berputar di dalam lembar, Esc menutup dan fokus kembali ke tombol Atur.
- [ ] **Sorot kata penting (AI)**: daftar saran muncul, **Terima semua**, kata berwarna di pratinjau.

### F. Animasi + SFX
- [ ] Tab Animasi > Pustaka: pilih **Pop per kata** (Masuk) dan **Pop** (Kata aktif). **Putar 3 dtk**: animasi terlihat.
- [ ] Studio: ubah satu keyframe, **Pakai**, putar lagi. **Simpan sebagai animasi saya**: muncul di Pustaka.
- [ ] Efek suara: nyalakan Angka dan Kata penting, klik play di suara (bunyi keluar), **Cek rencana bunyi**: ada daftar titik.
- [ ] **Terapkan ke timeline**: track audio baru bernama **"AutoCut SFX"** dengan 1 klip WAV. Putar di Premiere:
  bunyi pas di kata. Track lain tidak berubah.
- [ ] Terapkan lagi: klip lama diganti (bukan ditumpuk), tetap satu track "AutoCut SFX".
- [ ] Taruh klip lain sendiri di track "AutoCut SFX", lalu Terapkan: harus DITOLAK dan SFX lama tetap ada.
- [ ] Hapus satu suara sendiri di pustaka, lalu Terapkan dengan rencana lama: muncul "Suara efek yang dipilih sudah
  tidak ada." atau ringkasan "... dilewati karena suaranya sudah dihapus", bukan error Python.

### G. Terapkan caption ke timeline
- [ ] **Terapkan 18 caption**: track video "AutoCut Captions" paling atas berisi 1 klip .mov. Warna sama dengan pratinjau.
- [ ] Ubah satu kata, **Perbarui di timeline**: versi naik (v2), klip diganti di tempat yang sama.
- [ ] Sequence 9:16 + **Bold Pop**, isi satu caption dengan kata sangat panjang (MEMPERTANGGUNGJAWABKANNYA):
  pil ungu di tengah, tidak menyentuh atau terpotong di tepi kiri.
- [ ] Sequence panjang (34 menit) + **Flash Sale** > Terapkan: langkah "Siapkan caption" menunjukkan progres jalan
  (tidak macet di 5 %), **Batal** berfungsi.

### H. Bersih-bersih setelah tes
- [ ] Hapus track "AutoCut Captions" dan "AutoCut SFX" di sequence uji (atau tombol Hapus dari timeline di tab Ekspor).
- [ ] Hapus sequence dan bin uji. Jangan save project kalau tidak perlu.

---

## 4. Batasan yang diketahui

- **Belum dites di Premiere live**: penamaan track "AutoCut SFX", `overwriteClip` WAV mono di track stereo, dialog
  Ekspor/Impor gaya CEP, font contoh dari file lokal, Ctrl+V gambar di panel, animasi galeri di CEP.
- **Template berat di video panjang**: Flash Sale, 3D, bayangan panjang di video 34 menit masih butuh sekitar 30 dtk
  CPU di langkah persiapan, juga setelah edit satu kata.
- **Animasi masuk pop/zoom** dengan kata sangat panjang bisa menyentuh tepi frame sekitar 0,2 dtk pertama.
- Di zona aman TikTok, caption yang memenuhi lebar sedikit bergeser ke kiri (sengaja, supaya tidak tertutup tombol kanan).
- Template yang dirancang 16:9 (Subtitle Klasik, Tutorial Bersih, dan sejenisnya) tampil kecil di video 9:16. Pakai filter **Cocok**.
- Font Tiru gaya = font **mirip** dari koleksi AutoCut, bukan font yang persis sama.
- Ctrl Z membatalkan gaya, tapi tidak membatalkan Ganti nama / Hapus / Impor di Gaya Saya.
- Track caption Premiere (SRT) lama tidak bisa dihapus lewat script. Terapkan SRT lagi = menambah track baru.
- **Proxy lokal**: kemampuan dan batasnya tergantung proxy yang kamu jalankan. Siapkan satu penyedia cadangan
  (Gemini / OpenRouter). Penyedia berbayar memotong saldo per permintaan (hasil di-cache).
- Privasi: transkrip dan gambar Tiru gaya (kalau AI nyala) dikirim ke penyedia AI yang aktif. Ollama tetap di PC ini.

## 5. Perbaikan di gerbang akhir (5 Okt 2026)

- File **Gaya Saya** (`caption_library.json`) dan **gaya terakhir** (`caption_last.json`) sekarang aman dari bentrok baca/tulis
  di Windows. Sebelumnya, kalau dua proses menyentuh file bersamaan, penyimpanan bisa gagal dan (jarang) isi lama bisa
  tertimpa kosong.
- Menu kartu Gaya Saya tidak hilang lagi waktu daftar diperbarui di belakang (misalnya tepat setelah Ekspor).
- Gaya Saya kosong tidak lagi tertahan di tulisan "Memuat Gaya Saya...".
