# Tiru gaya dari gambar + Brand Kit (Auto Caption)

Panduan pemakai. Detail teknis untuk agen/dev: `docs/CAPTIONS_API.md` bagian 12. Kode: `engine/ac/captions/stylist.py`,
`engine/ac/captions/brandkit.py`, panel `panel/js/tools/captions_brand.js`. Screenshot (gitignored `--shots` folder): `v3_brand_*.png`.

## 1. Tiru gaya dari gambar

Lihat caption bagus di video orang lain? Ambil screenshot-nya, Klipora meniru gayanya untuk caption kamu.

Di mana: Auto Caption > tab **Template** > baris **Tiru gaya dari gambar** (paling atas, terlipat jadi satu baris
supaya galeri template tetap terlihat). Klik judulnya untuk membuka kartu; posisi buka/tutup diingat. Tombol **Tempel**
di baris itu dan Ctrl+V (gambar) tetap jalan saat terlipat, kartu terbuka sendiri untuk progres dan hasilnya.

Cara memasukkan gambar (pilih salah satu):
- **Tempel (Ctrl+V)**: ambil screenshot (misalnya Win+Shift+S), klik area kartu, tekan Ctrl+V.
- **Tempel** (tombol): mengambil gambar yang sedang ada di clipboard Windows. Pakai ini kalau Ctrl+V tidak bereaksi.
- **Seret** file PNG/JPG ke kotak putus-putus, atau klik **Pilih gambar**.
- **Pakai frame pratinjau**: memakai frame video di posisi pratinjau. Berguna kalau kamu menaruh video contoh (yang
  sudah ada captionnya) di timeline.

Saklar **Pakai AI pembaca gambar**: nyala = lebih akurat untuk jenis huruf, kapital, dan cara kata aktif disorot (pakai 1
permintaan AI per gambar, hasilnya disimpan, gambar yang sama tidak bayar lagi). Mati = semuanya diukur langsung dari
gambar (gratis, tanpa internet, akurasi lebih rendah, ditandai **Tanpa AI, akurasi lebih rendah**).

Hasilnya:
- **Gambar kamu** dan **Hasil Klipora** berdampingan, diperbesar ke area caption. Klik gambarnya untuk melihat seluruh frame.
- Kotak atribut: Warna teks, Kata aktif, Garis tepi, Latar, Kapital, Huruf, Ukuran, Posisi, Kata per baris, Sorotan,
  Animasi. Angka persen = seberapa yakin Klipora. Hijau = yakin, kuning = cukup, merah = tebakan.
- **Pakai gaya ini**: gaya langsung dipakai di caption. Ctrl Z (atau Batalkan di notifikasi) mengembalikan gaya lama.
- **Simpan ke Gaya Saya**: isi nama, tekan Enter. Gaya masuk ke **Gaya Saya** di tab Template (kartunya langsung
  muncul, notifikasi punya tombol **Lihat**) dan bisa dipakai di video lain. Jalurnya sama dengan **Simpan gaya** di
  bawah (engine `library save` dengan `template`).

Tips supaya hasilnya pas:
- Pakai screenshot yang tajam dan caption-nya besar. Screenshot satu frame penuh = posisi dan ukuran ikut ditiru.
- Kalau videonya ramai tulisan (halaman web, banner, game), **potong gambar di sekitar caption** atau nyalakan AI.
  Tanpa AI, tulisan lain yang lebih besar bisa dikira caption.
- Huruf ditiru dengan font yang **mirip** dari koleksi Klipora (bukan font yang persis sama).
- Setelah dipakai, sisanya bisa dirapikan di tab Gaya (ukuran, warna, posisi).

## 2. Brand Kit

Simpan warna, font, dan nama produk/channel kamu sekali, pakai di semua video. Bisa beberapa kit (misalnya per toko
atau per channel), satu yang aktif.

Di mana: tab **Gaya** (bar Brand Kit di paling atas) > **Atur**, atau **Pengaturan > Caption > Atur Brand Kit**.
Lembarnya modal: Tab / Shift+Tab berputar di dalam lembar, Esc atau X menutup, fokus kembali ke tombol pembukanya.

Isi kit:
- **Nama**: nama toko atau channel.
- **Warna**: Utama (kata brand), Aksen (kata aktif, angka, kata penting), Teks, Latar (warna kotak latar). Klik
  kotak warna, atau ketik kode warna seperti `#00A86B`. Kode yang salah diberi garis merah dan tidak dipakai.
- **Huruf**: Judul (dipakai untuk caption) dan Isi. Font dari koleksi Klipora atau font Windows yang terpasang.
- **Kata brand**: nama produk, toko, channel. Pisahkan dengan koma. Ejaannya selalu dibetulkan (masuk Kamus) dan
  katanya otomatis diberi warna Utama.
- **Emoji**: Tanpa, Sedikit (paling banyak 1 tiap 15 detik), Banyak.
- **Gaya bahasa**: Santai, Profesional, Semangat, Lucu, Edukatif. Mengatur seberapa besar kata yang disorot
  (Profesional tidak membesar, Semangat paling besar).
- **Logo / watermark**: lokasi file, opsional. Hanya disimpan, tidak digambar di caption.
- Contoh tampilan di bawah form berubah langsung saat kamu mengubah warna.

Tombol: **Simpan dan terapkan** (dari tab Gaya), **Simpan** (dari Pengaturan), **Jadikan aktif**, **Hapus** (klik dua kali).
Kit disimpan di `%APPDATA%\Klipora\brandkit.json` (dipindah sekali dari folder lama `%APPDATA%\AutoCutBOT`).

**Terapkan Brand Kit** (tab Gaya > Terapkan): template yang sedang dipakai diwarnai ulang dengan warna kit (teks,
kata aktif, kotak, cahaya, gradasi kalau ada), font judul dipakai, kata brand masuk Kamus dan diberi warna Utama,
emoji diatur sesuai kit. Bentuk template (animasi, posisi, ukuran) tidak berubah. Satu kali Ctrl Z mengembalikan semuanya.

## 3. Sorot kata penting (AI)

Tab **Gaya** > **Sorot kata penting (AI)**. Klipora memilih kata yang pantas diberi warna:
- kata brand dari Brand Kit aktif (selalu, warna Utama),
- angka dan harga,
- kata kunci pilihan AI (plus emoji kalau kit mengizinkan),
- tanpa AI: kata isi terpanjang di tiap caption.

Paling banyak 1 kata per caption (ditambah kata brand). Hasilnya **saran**: centang atau hilangkan centang per kata,
klik waktu (misalnya 0:04,91) untuk melihatnya di pratinjau, lalu **Terima semua** atau **Terima n kata**. **Tolak
semua** = tidak ada yang berubah. Kata yang sudah kamu warnai sendiri tidak diubah. Ctrl Z membatalkan.

## 4. Bahasa tampilan (Indonesia / English)

Semua teks di kartu Tiru gaya, bar Brand Kit, lembar Brand Kit, daftar Sorot kata penting, dan tab Animasi (Pustaka,
Studio, Efek suara) mengikuti **Pengaturan > Bahasa tampilan** (Otomatis ikut Premiere, Bahasa Indonesia, atau
English) dan langsung berganti tanpa membuka ulang panel. Pilihan Gaya bahasa dan Emoji juga diterjemahkan (English:
Casual, Professional, Energetic, Funny, Educational; None, A few, Lots). Nama kit, kata brand, dan nama gaya/animasi
yang kamu ketik tidak diterjemahkan. Template bawaan punya nama dan deskripsi Inggris (misalnya Judul Emas = Gold
Title, Hijau Viral = Viral Green, Berita Terkini = Breaking News), preset animasi juga (Pudar halus = Soft fade).

Nama di proyek Premiere sama di kedua bahasa: track audio efek suara **Klipora SFX** dan bin **Klipora Captions**.
Proyek lama dengan track **AutoCut SFX** tetap dikenali (Terapkan lagi mengganti isinya di track lama itu). File
ekspor Gaya Saya tetap memakai format `autocut.caption_styles` (file lama dan `klipora.caption_styles` bisa diimpor).

## 5. Kalau ada masalah

| Gejala | Artinya | Yang dilakukan |
|---|---|---|
| "Caption tidak ditemukan di gambar" | Tidak ada tulisan yang jelas, AI mati | Potong di sekitar caption, atau nyalakan AI |
| "Clipboard tidak berisi gambar" | Yang disalin bukan gambar | Salin screenshot dulu (Win+Shift+S), lalu klik Tempel |
| Ctrl+V tidak bereaksi | Panel belum fokus, atau Premiere menahan tombolnya | Klik kotak putus-putus dulu, atau pakai tombol Tempel |
| Warna teks salah | Latar video ramai atau ada efek cahaya/gradasi | Potong gambar lebih rapat, nyalakan AI, atau ganti warna di tab Gaya |
| "AI tidak dipakai" | AI mati di Pengaturan, atau penyedia AI tidak punya model Vision | Pengaturan > AI > Penyedia AI, isi kolom Vision |
| Font kit "tidak ada di PC ini" | Font Windows yang dipilih sudah dihapus | Pilih font lain di Brand Kit |

## 6. Biaya, privasi, risiko

- AI pembaca gambar mengirim gambar yang kamu tempel (diperkecil) ke penyedia AI yang aktif. Untuk Proxy lokal (kompatibel OpenAI),
  batasnya tergantung proxy yang kamu jalankan. Hasil disimpan, gambar yang sama tidak dikirim lagi.
- Tanpa AI tidak ada yang dikirim ke internet.
- Gambar yang kamu pakai disimpan sebagai salinan kecil di folder kerja video (`stylist\`, 12 terakhir).
