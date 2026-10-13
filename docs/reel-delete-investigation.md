# Penelusuran delete Reel mandiri

Baseline kode: `a59d35ce11c8018740a2080d2c46924a757a9b08`.
Diperiksa 13 Oktober 2026. Tidak ada request penghapusan Facebook nyata.

## Bukti resmi yang dapat dibaca

- [SDK Meta Page](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/page.py), `get_video_reels`: GET edge `/video_reels`, hasil diparse sebagai `AdVideo`.
- [SDK Meta AdVideo](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/advideo.py), `api_delete`: method DELETE pada node `self['id']`, tanpa membentuk ID Post atau menggunakan endpoint publikasi.

Ini membuktikan SDK menyediakan operasi node video dan memakai model tersebut untuk listing Reel. SDK tidak menetapkan kontrak respons sukses dalam method itu, persyaratan Page token/izin, batasan untuk Reel Page, efek pada Post terkait, atau pengecualian Story. Bukti ini belum cukup untuk menyatakan dukungan Reel umum terverifikasi. Jalur percobaan terbatas di bawah dibuka atas instruksi eksplisit pengguna, bukan atas klaim tambahan tentang SDK.

Pengguna kemudian berhasil membuka **Page Videos v26.0** dan memberikan isi bagian Deleting: penghapusan diarahkan ke **Video endpoint** dan tidak dapat dilakukan pada edge Page Videos. Ini bukti resmi untuk membedakan operasi node Video dari `/Page/videos`, tetapi bagian tersebut tidak memberikan contoh request node, izin/token DELETE atau respons sukses. Permissions dan Return Type bagian Creating hanya berlaku untuk publikasi dan tidak digunakan sebagai kontrak DELETE.

## Referensi alternatif dan hasil akses

- [Page Videos](https://developers.facebook.com/docs/graph-api/reference/page/videos/): tidak dapat diambil dari alat penelusuran. Versi URL `/reference/v26.0/page/videos/` juga tidak dapat dibaca.
- [Video API](https://developers.facebook.com/docs/video-api/) dan [Reels Publishing](https://developers.facebook.com/docs/video-api/guides/reels-publishing/): tidak dapat diambil. [Video Publishing guide](https://developers.facebook.com/docs/video-api/guides/publishing/) terhalang HTTP 429. Endpoint publikasi tidak digunakan sebagai operasi penghapusan.
- [Page video_reels](https://developers.facebook.com/docs/graph-api/reference/page/video_reels/): HTTP 429. Belum dapat membuktikan penerapan operasi node DELETE pada Reel dari edge ini.
- Pencarian sumber resmi Facebook/Meta dan koleksi Meta Postman tidak menghasilkan kontrak delete Reel yang dapat dibaca. Hasil pihak ketiga tidak digunakan sebagai dasar implementasi.
- Referensi umum [Using the Graph API](https://developers.facebook.com/docs/graph-api/using-graph-api/) dan [pages_manage_posts](https://developers.facebook.com/docs/permissions/reference/pages_manage_posts/) juga tidak dapat dibaca dari alat ini; kontrak/izin video tidak diambil dari dugaan tentang operasi Post.
- Halaman `/video` lama **tidak diminta ulang**: pengguna sudah memastikan tidak mempunyai bagian Deleting dan mempunyai banner fitur dihapus setelah v3.2. Semantik/izin halaman itu tidak dianggap bukti operasi Reel saat ini.

## Dasar percobaan yang diizinkan pengguna

Pengguna memberikan file lama `app-with-reels.py` dari proyek `fb-page-cleaner-optimized/fb-page-cleaner`, lalu menginstruksikan percobaan **SATU Reel** melalui web lokal. Kode tersebut memuat listing `video_reels`, pemetaan ID Reel ke Page dan DELETE node memakai ID asli. Itu referensi implementasi percobaan, bukan bukti kontrak API atau hasil uji nyata Reel.

Kode lama tidak dieksekusi. Token frontend/URL, pemetaan Page bebas dari frontend, retry DELETE, parser `bool(success)`, fallback updated_time, dan fallback ID/deduplikasi dari potongan URL tidak disalin. Tidak ada permintaan ulang untuk mencari bagian Deleting pada halaman yang sudah diberikan pengguna.

## Perilaku percobaan saat ini

Loader tetap menyimpan ID asli provenance `video_reels` dan `post_id` terpisah; waktu dan pagination tidak diubah. Resolver default single/bulk tetap menargetkan **Page Post**. Endpoint baru `POST /api/reels/delete/prepare` dan `/execute` memakai scope terpisah `reel_video`, hanya tepat satu ID asli dari audit Page/sesi backend. Konfirmasi acak berlaku maksimal 60 detik, single-use, snapshot terikat jenis operasi; konfirmasi Post tidak dapat dipakai sebagai konfirmasi Reel.

Tombol **Uji Hapus 1 Reel** menampilkan ID video asli, Page/nama, teks/thumbnail audit dan link pemeriksaan. Pengguna harus memastikan target benar dan bukan Story serta menyetujui penghapusan nyata yang tidak dapat dibatalkan. Link menggunakan ID asli; link tidak dipakai untuk memilih atau mengganti target DELETE. Story teridentifikasi dan identitas ambigu tetap ditolak. Pengecualian otomatis seluruh Story belum dibuktikan.

`GraphClient.delete_reel_once` memakai transport satu kali yang sudah ada, DELETE `/v26.0/{ID_VIDEO_ASLI}` dengan Page token backend pada Authorization. Tidak ada GET rutin, retry atau ID alternatif. Governor, cooldown, Retry-After, limiter request global dan circuit breaker dipertahankan. Runtime memberi `experimental=true`, `contract_verified=false`: sukses hanya HTTP 2xx + JSON object + success boolean true + tanpa field error. Respons tidak dikenali menjadi uncertain; penolakan Meta yang dikenali menjadi failed. Pesan/diagnosis disanitasi, tanpa respons mentah atau token.

Lock memakai ID video dan relasi Post yang diketahui. Setelah respons sukses, target serta alias provenancenya disupresi di audit/cache agar tidak diproses dua kali; ini tidak mengklaim objek alias juga dihapus oleh Meta. Reservasi memori mempertimbangkan semua tombstone alias. Hasil satu Reel hanya berada di memori sesi dan hilang setelah restart; checkpoint antrean tidak mengaktifkan pemulihan atau eksekusi Reel otomatis.

Bulk Reel tetap tidak dibuka. Batas 20 pilihan bulk, dua worker global dan pacing awal satu dispatch/detik tidak dinaikkan. Tidak ada database, perubahan .venv, commit atau push.

## Pemeriksaan aktual

Regresi terakhir: **189 tes backend dan 59 tes frontend lulus**, termasuk 19 tes Reel baru. Tes memakai token dummy/MockTransport dan DOM/fetch simulasi: ID asli, provenance, Story, pemilik sesi, TTL/single-use, scope Post/Reel, parser ketat, cooldown, alias lock, supresi dua arah, kapasitas dan respons uncertain. Sintaks Python/JavaScript serta `git diff --check` lulus. Tidak ada DELETE Facebook nyata oleh agen atau bukti hasil Reel nyata dari tes mock ini.

Implementasi percobaan siap direview; yang belum dibuktikan tetap kontrak/izin/cakupan API Reel umum dan hasil satu Reel nyata yang akan diuji pengguna melalui UI setelah memeriksa target.
