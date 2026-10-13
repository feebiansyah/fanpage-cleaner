# Fanpage Cleaner

Aplikasi FastAPI + Jinja2 satu halaman berbahasa Indonesia. Koneksi menerima **Facebook user access token** dan daftar Fanpage lewat `GET /me/accounts`. Load Posts mengambil kandidat untuk audit. Tahap 5 menambahkan **Hapus 1 Post biasa** melalui konfirmasi manual di web lokal. Bulk delete, delete Reel/video, OAuth, database, dan perpanjangan token tidak tersedia.

## Status tahap 5

**Hapus 1 Post** mendukung tepat satu **Post teks/status** berjenis **Postingan**, dengan metadata `status_type=mobile_status_update` yang memang diminta oleh loader, satu identitas asli dari provenance `published_posts`, milik sesi/Page pengguna, audit belum kedaluwarsa, dan link Facebook valid. Allowlist jenis ini kebijakan scope lokal, bukan daftar semua jenis Post yang Meta dapat hapus. Jenis hilang/tidak diketahui, share/link dengan status lain, Foto, Video, Reel (termasuk Reel yang digabung dengan Post), Story teridentifikasi, dan identitas ambigu belum didukung. Audit yang dimuat sebelum perubahan metadata ini perlu dimuat ulang. Tidak ada ID sintetis, batch delete, atau penghapusan lewat antrean.

Dasar resmi: [Pages API Posts — Delete a Post](https://developers.facebook.com/documentation/pages-api/posts), **isi bagian yang diberikan pengguna setelah membuka halaman di browser**: `DELETE /v26.0/{page_post_id}` memakai ID Post asli; sukses `{"success":true}`. Bagian Before you start menetapkan Page access token, daftar permissions termasuk `pages_manage_posts` dan `pages_read_engagement`, serta tugas pengguna pada Page. **Daftar panduan bukan klaim izin minimum khusus DELETE**, dan Page muncul pada `/me/accounts` bukan jaminan izin menghapus. Fetch dokumentasi dari alat ini masih gagal; asal bukti serta batasnya dicatat di [verifikasi tahap 5](docs/stage5-api-verification.md).

Pemeriksaan Story tetap mengecualikan ID teridentifikasi, tetapi satu halaman **tidak membuktikan seluruh kandidat bebas Story**. Untuk percobaan satu Post, pengguna harus membuka link, memeriksa target, dan mengonfirmasi bahwa itu Post biasa yang memang ingin dihapus, bukan Story. Ini **konfirmasi manual target**, bukan verifikasi otomatis cakupan Story. `safe_for_delete` hasil load dan kelayakan bulk tetap false; konfirmasi manual tidak membuka bulk.

### Cara mencoba satu Post melalui web

1. Jalankan satu proses server sesuai perintah Windows di bawah. Hubungkan token, pilih Fanpage, dan coba Load Posts limit **20** atau **100**. Tunggu selesai atau hentikan pemuatan agar audit tersimpan.
2. Pilih **tepat satu** Post teks berjenis **Postingan** dengan jenis yang dikenali, lalu klik **Hapus 1 Post**. Tombol nonaktif untuk pilihan kosong/ganda, metadata jenis tidak diketahui, atau jenis yang belum didukung.
3. Dialog menampilkan nama Fanpage, jenis, tanggal, teks singkat, link, dan **ID Post asli**. Buka link dan pastikan target yang tepat adalah Post biasa, bukan Story. Jika ragu, klik **Batal**.
4. Centang pernyataan pemeriksaan manual, lalu tekan **Konfirmasi Hapus** hanya jika memang ingin menghapus. **Post ini akan dihapus dari Fanpage. Penghapusan tidak dapat dibatalkan.** Menyiapkan dialog atau mencentang saja belum mengirim DELETE.
5. Sukses hanya jika HTTP 2xx, JSON object memiliki `success` boolean true, dan tidak memiliki field `error`. Post dikeluarkan dari daftar/pilihan lokal dan cache terkait. HTTP 200 saja, false, string `"true"`, angka 1, kosong, rusak, atau bentuk lain bukan sukses.
6. Gagal atau **uncertain** mempertahankan Post. Timeout/network setelah request mungkin terkirim tidak diulang otomatis. Bila respons lokal hilang, periksa Facebook manual sebelum mempertimbangkan percobaan baru. Tidak ada GET sebelum/sesudah penghapusan rutin.

Backend menerima `{page_id, keys:[key]}` untuk menyiapkan konfirmasi; token/ID target bebas ditolak. Eksekusi hanya menerima `{confirmation_id, manual_target_confirmed:true}`; tidak menerima target pengganti. ID konfirmasi acak 256 bit, terikat browser/sesi dan snapshot identitas/metadata, **TTL maksimal 60 detik**, single-use, tidak memperpanjang sesi 20 menit. Audit/Page/token diperiksa ulang sebelum dispatch setelah penantian governor. Disconnect/sesi habis sebelum dispatch mencegah DELETE. Request yang mungkin sudah terkirim tidak dapat ditarik kembali; respons lama tidak ditampilkan pada Fanpage/sesi baru.

DELETE memakai Page token backend pada header Authorization, AsyncClient bersama, concurrency global **2**, pacing awal **1 detik**, usage/cooldown/Retry-After bersama. **Satu kali pengiriman tanpa retry otomatis**, termasuk bila respons rusak/transient. Lock `post:ID_ASLI` sama dengan dry run, sehingga target sama lintas browser/pekerjaan tidak bersamaan. Tidak ada transport delete untuk antrean. Versi jalur single delete ditetapkan **v26.0** sesuai bukti, terpisah dari konfigurasi versi GET.

Konfirmasi dibatasi **200 global / 10 per sesi**, dibersihkan saat TTL/sesi habis; metadata tanpa salinan token. Diagnostik aman disimpan **di memori saja, 200 catatan terakhir**, berisi operasi, ID target, HTTP status, kode/subkode numerik terpilih, durasi, dan outcome; tidak ada token/header/exception/response mentah. Tidak ada endpoint untuk melihat diagnostik browser lain. Tombstone sukses dibatasi `MAX_AUDIT_ITEMS` (default 20000), kapasitas diperiksa sebelum dispatch termasuk operasi aktif; TTL `SESSION_TTL + AUDIT_TTL` (default 1500 detik), mencegah snapshot cache/job lama memunculkan target kembali. Semua state ini hilang saat restart; tidak ada pemulihan/retry otomatis single delete.

Pengujian tahap 5 memakai **mock HTTP/token dummy dan DOM/fetch simulasi**. Penghapusan Facebook nyata belum dicoba oleh agen; pengguna yang menekan konfirmasi setelah review.

## Tahap 4: antrean dan Dry Run saja

**Antrean tetap dry run saja**, tidak mengirim DELETE/batch mutation dan tidak mempunyai konfigurasi untuk mengaktifkan bulk. Executor antrean hanya memeriksa identitas/metadata lokal; `dry_run_checked` bukan berhasil dihapus. Jalur satu Post tahap 5 terpisah dari executor antrean.

Cara mencoba: hubungkan sesi, Load Posts limit 100, pilih **5 checkbox**, lalu klik **Periksa Penghapusan (Dry Run)**. Ringkasan menampilkan nama Page, jumlah dipilih, unik, layak, belum layak/ambigu, duplikat, progres dan target konkret. Hapus Terpilih tetap nonaktif. Berhenti memuat dan Berhenti dry run menggunakan tombol yang sama dengan label berbeda. Memilih Page lain membatalkan tampilan/pekerjaan dry run lama. Status dan halaman hasil dry run dibaca dari memori backend, tanpa Meta; pagination daftar Load Posts tetap array lokal 50 item. Tidak ada estimasi waktu penghapusan.

### Identitas dan hambatan validasi

Frontend mengirim `{"selections":[{"page_id":"11","keys":["published_posts:11_1"]}]}`. Key harus berasal dari audit backend milik sesi yang masih berlaku; token, URL, ID delete bebas, field real_delete, Page asing, dan key yang tidak dimuat ditolak. API mendukung hingga 200 kelompok Page per pekerjaan, 5000 key per Page, dan total kapasitas 50000 target. UI sederhana mengirim Page yang sedang dipilih. Audit per Page dipertahankan terbatas agar API dapat memakai beberapa Page dalam satu pekerjaan; default 200 hasil Page, total 20000 item/64 MiB, TTL 300 detik dan selalu tunduk pada sesi. Refresh Page mengganti kapasitas Page tersebut, bukan menduplikasi audit lama.

Key tampilan, object_id sumber, canonical identity, dan delete_target terpisah. [SDK resmi PagePost](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/pagepost.py) memiliki api_delete pada node ID asli. Itu hanya dasar **usulan** target Post, bukan bukti izin/Story/keberhasilan. ID tidak diubah atau digabung menjadi Page_Post. Relasi post_id disimpan persis dari field Reels yang memang diminta, tanpa mengubah format; pencocokan berbeda format tetap tidak diasumsikan sama. Reel tanpa dasar Post memiliki delete_target=null dan alasan reel_delete_semantics_unverified. Tidak ada request validasi per item.

SDK tidak membuktikan published_posts/video_reels bebas Story, dan pemeriksaan Story satu halaman tidak membuktikan seluruh kandidat bebas Story. **Kriteria dry run/bulk tahap 4 tetap konservatif**: alasan per target tetap mencakup story_exclusion_unverified, delete_permissions_unverified, success_semantics_unverified, dan bila relevan relasi Reel ambigu. Label lama ini belum diperbarui menjadi kelayakan bulk oleh bukti single Post; jalur tahap 5 memakai kriteria percobaan dan konfirmasi manual terpisah. Story teridentifikasi ditolak. Ketidakpastian satu target tidak menghentikan pemeriksaan lainnya; safe_for_delete hasil audit selalu false. Kemunculan Page pada /me/accounts bukan bukti izin menghapus.

### Antrean, worker dan pacing

Antrean terpusat satu proses memakai round robin antar Page, dengan kunci **canonical target**, bukan kunci seluruh Page. Target sama lintas pekerjaan/browser tidak aktif bersamaan. Target berbeda di Page sama boleh paralel. Default **dua worker GLOBAL**, maksimum konfigurasi lokal **8**; ini kebijakan awal, bukan kuota Meta. Batas antrean: 100 pekerjaan, 50000 target, budget konservatif 128 MiB (2048 byte/target), 100 event per pekerjaan, durasi/memori pekerjaan 1200 detik. Kapasitas job/target mencakup pekerjaan selesai yang belum TTL; checkpoint tetap dipertahankan lebih lama.

RequestGovernor dibagi oleh Graph GET koneksi/load dan dispatch pemeriksaan dry run. Default jarak awal **1 detik** antar percobaan, tanpa burst, concurrency global **2**, maksimum lokal 8 serta tidak melebihi pool HTTP. Pemeriksaan dry run menggunakan permit pacing lokal tetapi **tidak mengirim request Meta**. Enam request per langkah Load Posts adalah budget langkah, bukan rate limiter. Test fixtures memakai interval kecil agar suite cepat; tes rate menggunakan interval terukur tersendiri.

Header penggunaan valid di atas 50% menambah interval menjadi setidaknya dua kali interval awal, di atas 80% empat kali (dibatasi GRAPH_MAX_INTERVAL=10 detik). Ambang tahap 2 90% tetap memasang cooldown konservatif. Tidak ada percepatan otomatis setelah request sukses. Header tidak tersedia berarti penggunaan/kuota tidak diketahui, bukan nol. Retry-After dan cooldown store bersama tetap berlaku; rate limit scope tidak diketahui memakai global konservatif. Dry run/fake executor rate limit memasang cooldown sebelum melepas permit agar worker lain menghormatinya. Tidak ada rotasi token/IP/perangkat atau klaim bebas limit.

Konfigurasi: DRY_WORKERS=2, GRAPH_REQUEST_CONCURRENCY=2, GRAPH_REQUEST_INTERVAL=1, GRAPH_MAX_INTERVAL=10, DRY_MAX_JOBS=100, DRY_MAX_TARGETS=50000, DRY_MAX_BYTES=134217728, DRY_JOB_TTL=1200, DRY_MAX_EVENTS=100, DRY_BODY_BYTES=8388608. Ukuran body besar hanya pada endpoint pilihan dry run, bukan input token.

### Kontrak hasil dan checkpoint

State antrean: queued, processing, dry_run_checked, failed, uncertain, cancelled; succeeded disediakan pada schema untuk rancangan berikutnya, **tidak dihasilkan executor dry run**. Temporary failure pemeriksaan sebelum pengiriman dapat dicoba maksimal tiga percobaan, dengan hitungan tersimpan. Token invalid/izin tidak diulang. Kontrak bulk/classifier tahap 4 tetap konservatif, tidak diaktifkan oleh tahap 5. Single Post mempunyai classifier ketat terpisah dan tidak memakai retry antrean/GET. Tidak ada asumsi semua DELETE idempotent.

Checkpoint pada folder tetap `runtime/dry-run/` yang sudah diabaikan Git. Format versi 1, nama UUID backend, schema allowlist; tidak menyimpan token user/Page, cookie, session ID, Authorization, cursor, nama/teks/permalink konten, respons Meta mentah. Owner UUID internal nonrahasia terpisah dari credential sesi. Manifest ditulis atomik dengan fsync + replace; progres ditulis sebagai shard maksimal **256 target** dengan generation manifest, sehingga tidak menulis ulang 50000 target pada tiap transisi. Processing dicatat sebelum executor dan hasil setelahnya. Generation lama diabaikan setelah manifest diperbarui.

Batas checkpoint: **100 manifest**, maksimal 196 shard per job pada kapasitas 50000 target, **64 MiB per file**, **256 MiB total termasuk staging/tmp**, retensi **7 hari**. Replace atomik memerlukan ruang salinan sementara; kapasitas penuh ditolak sebelum penulisan. Path bebas dari frontend tidak diterima. Restore membatasi jumlah target dan total byte manifest+shard sebelum membaca; format rusak/tidak kompatibel tetap tidak dapat dieksekusi. File tmp lama dibersihkan menurut retensi. Checkpoint gagal menghentikan dispatch, bukan melanjutkan tanpa pencatatan.

Saat disconnect/sesi habis/shutdown, dispatch baru berhenti; processing menjadi uncertain dan target belum diproses tetap tercatat tanpa token. Sesi tetap **20 menit**, tidak diperpanjang dry run/status. Setelah restart semua record yang dibaca menjadi **paused_needs_reconnect**, processing crash menjadi uncertain. Tidak ada eksekusi otomatis atau pemulihan owner dari Page ID yang sama. **Mekanisme pemulihan ownership eksplisit belum tersedia; checkpoint belum dapat dilanjutkan atau diakses lewat UI/browser baru.** Record disimpan untuk audit nonrahasia, bukan bearer credential.

Endpoint dry run: POST /api/dry-run/jobs (keys saja), GET /api/dry-run/jobs/{id}?offset=0 (50 hasil dari memori), POST /api/dry-run/jobs/{id}/stop. Semuanya memerlukan cookie pemilik serta perlindungan origin/client/no-store yang sama. Job ID saja tidak memberi akses. Tidak ada endpoint bulk delete.

Gunakan **satu proses Uvicorn --workers 1** sesuai perintah di bawah. Jangan menambah worker Uvicorn untuk mempercepat antrean; beberapa proses tidak berbagi locks/limiter/sesi. Aplikasi tetap localhost, bukan akses banyak perangkat.

Pengujian tahap ini hanya mock/fake transport dan target dummy. Tes memasukkan **50000 target dummy dari 200 Page** tanpa dispatch, serta mengukur concurrency, pacing, fairness, canonical locks, cooldown, retry, cancel/expiry, secret-free checkpoint dan crash/restart. Ini bukan bukti Facebook nyata atau kelayakan penghapusan.

## Load Posts bertahap: coba 100, lalu 3000

1. Jalankan satu proses server memakai interpreter `.venv` sesuai perintah Windows di bawah, hubungkan token, lalu pilih Fanpage. **Cari Fanpage** memfilter nama/Page ID lokal tanpa request Meta; pilihan aktif tetap dipertahankan dan ditandai bila di luar pencarian.
2. Isi **Maksimal postingan = 100**, klik satu tombol **Load Posts**. Minimum 1, default 100, maksimum 5000; angka ini maksimum unik ditampilkan, bukan janji jumlah atau N terbaru seluruh Page.
3. Browser memulai satu pekerjaan lokal, kemudian melanjutkan langkah pekerjaan yang sama otomatis. Cursor dan kandidat disimpan backend; halaman berhasil diterima tidak diminta dari awal lagi. Awal pengambilan membagi anggaran kandidat antara dua sumber yang belum terbukti saling mencakup: limit 100 meminta sekitar 50 dari posts dan 50 dari Reels, kemudian mengisi kekurangan setelah deduplikasi/pengecualian. API boleh mengembalikan lebih sedikit/lebih banyak dari request; hasil tampilan tetap dibatasi.
4. Perhatikan progres nyata: item mentah diterima, kandidat unik, jumlah ditampilkan, request termasuk retry, jumlah bertanggal/tanpa tanggal, dan **Ringkasan sumber API**. Saat cooldown, timer browser menunggu lokal; tidak ada polling Meta untuk mengecek cooldown. Waktu tunggu tetap mengurangi batas total pekerjaan dan sisa sesi.
5. Cocokkan kandidat dengan tab **Postingan** Fanpage Facebook. Tanggal acuan Post dari API ditampilkan WIB; Reel tanpa relasi Post tetap **Tanggal tidak tersedia** dan berada di akhir. Susunan aplikasi/pin dapat berbeda. Tidak ada jaminan seluruh konten tersedia lewat API.
6. Daftar memakai pagination lokal **50 konten**; mengganti halaman atau mencari Fanpage tidak meminta Meta. Checkbox dan **Pilih Semua** hanya pilihan audit, boleh aktif pada hasil berjalan/parsial/cakupan Story belum pasti. Pilih Semua mencakup seluruh hasil dimuat, dan pilihan yang masih ada dipertahankan saat snapshot bertambah. Ini tidak menyatakan konten aman dihapus.
7. **Berhenti** menghentikan pemuatan, mempertahankan halaman diterima untuk audit, dan menghentikan langkah lanjutan. Memilih Fanpage lain membatalkan pekerjaan lama dan membersihkan audit. Disconnect/sesi habis menghapus pekerjaan dan token sesi; respons lama tidak ditampilkan.
8. Setelah hasil 100 diperiksa, coba **3000**. Baca alasan berhenti per sumber; hasil boleh kurang dari maksimum akibat sumber habis, izin, batas lokal, atau error. Jangan menyimpulkan penyebab hasil nyata 1600 posts + 543 Reels selain timeout yang memang dilaporkan pengguna; diagnosis baru membantu pemeriksaan berikutnya.

**Hapus Terpilih tetap nonaktif.** Hapus 1 Post hanya tersedia melalui dialog konfirmasi manual tahap 5, bukan sebagai kelayakan otomatis dari checkbox audit. Thumbnail hitam/perilaku gambar tidak diubah.

## Sumber API tahap 3 dan bukti

Verifikasi sebelum implementasi memakai source **SDK resmi Meta**, dibaca 13 Oktober 2026. Halaman dokumentasi utama masih gagal diakses (429 / gagal fetch). Keberadaan POST publikasi tidak dipakai sebagai bukti listing.

| Sumber | Bukti resmi / penggunaan |
| --- | --- |
| `GET /{page-id}/published_posts` | [Page SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/page.py) memiliki `get_published_posts`, menghasilkan PagePost. Dipilih sebagai sumber postingan Page terbit, termasuk foto/video, bukan feed pengunjung. [PagePost SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/pagepost.py) mendeklarasikan field yang diminta. |
| `GET /{page-id}/video_reels` | SDK Page memiliki **get_video_reels**, method GET, menghasilkan AdVideo. [AdVideo SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/advideo.py) memuat `published`, `from`, `post_id`, `scheduled_publish_time`, dan metadata visual. Ini bukti listing terpisah dari POST upload/publish. |
| `GET /{page-id}/stories` | SDK Page memiliki `get_stories`; [Stories SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/stories.py) memuat `id`, `post_id`, `media_id`, status PUBLISHED/ARCHIVED. Dipakai untuk mengecualikan ID, bukan menjadi konten audit. |

Tidak menambahkan `/videos` sebagai listing terpisah yang menduplikasi postingan video. Tidak ada feed pengunjung, draft/scheduled/ads listing, atau fallback endpoint. Dua listing konten tetap diperlukan karena SDK tidak membuktikan published_posts mencakup semua Reels. SDK juga tidak membuktikan kedua listing mengecualikan Facebook Story; perlindungan Story tidak dihapus. Pemeriksaan Story terbatas dilakukan setelah kandidat ditemukan, bukan pemindaian histori wajib sebelum listing. Bila sumber tidak dapat dibaca, hasil ditandai parsial; aplikasi tidak mengarang fallback. Setiap sumber memakai field dalam satu request per halaman, tanpa request metadata per item.

Field posts: `id,from,is_published,created_time,scheduled_publish_time,message,full_picture,permalink_url,status_type`. Field Reels: `id,from,published,post_id,scheduled_publish_time,description,picture,permalink_url`. Field Story: `id,post_id,media_id`; filter status PUBLISHED dan ARCHIVED. Hanya kandidat dengan owner `from.id` sesuai Page dan flag published eksplisit true yang dimuat. Jadwal masa depan ditolak. Kandidat tanpa bukti owner/published dilewati dan dicatat sebagai keterbatasan.

Identitas asli dan provenance sumber disimpan. Post dan Reel digabung jika `Reel.post_id` persis sama dengan ID Post yang dimuat; Reel lain dengan post_id yang sama juga digabung. Pencocokan object_id dihapus: field itu tidak diminta dan tidak dideklarasikan model PagePost endpoint ini. Keberadaannya pada model Post generik bukan bukti dukungan published_posts. Tidak ada deduplikasi berdasarkan teks/tanggal, atau pembuatan PageID_PostID sintetis untuk penghapusan. Tanpa relasi, kedua kandidat dipertahankan dan ketidakpastian identitas silang sumber dilaporkan. `key` hanya kunci pilihan UI sementara, **bukan ID untuk DELETE**.

Story dikeluarkan melalui seluruh identitas yang tersedia (`id`, `post_id`, `media_id`). Kata/field `story` pada Post tidak dijadikan klasifikasi Facebook Story. Pemeriksaan mengambil **maksimal satu halaman logis**, dengan limit minimum dari batas pengguna, PAGE_SIZE, dan STORY_MAX_ITEMS; retry tetap maksimal dua. Kandidat kosong pada kedua sumber tidak memerlukan request Story. ID Story yang ditemukan juga diterapkan pada halaman konten selanjutnya.

Jika halaman Story masih memiliki next, atau referensi Post/media tidak lengkap, pengambilan konten dapat selesai tetapi pengecualian Story **belum terverifikasi**. Checkbox audit boleh aktif; ini tidak mengubah cakupan Story atau safe_for_delete. Ketidakpastian ini bukan kegagalan pagination. Kegagalan request Story yang nyata menghasilkan hasil parsial. Bahkan bila pemeriksaan berakhir, **Pengecualian Story seluruh kandidat belum selesai dibuktikan**: cakupan published_posts/video_reels dan histori/retensi Story Meta belum terverifikasi; `story_exclusion=matched_ids_only` hanya menyatakan kecocokan ID dalam pemeriksaan tersebut. Tidak ada klaim aman dihapus.

## Urutan dan makna limit

SDK membuktikan GET dan field, **tidak menjamin urutan kronologis pagination**. Karena itu aplikasi tidak mengklaim N terbaru seluruh Fanpage. Published posts dan Reels dikunjungi masing-masing pada awal, lalu diambil bertahap/bergantian sampai **jumlah unik gabungan setelah deduplikasi dan pengecualian** mencapai batas pengguna, sumber habis, atau batas/kegagalan menghentikan pekerjaan. Tidak ada kuota N terpisah yang menghentikan sumber sebelum deduplikasi lintas sumber. Request berikutnya menyesuaikan sisa kuota; satu respons halaman dapat memberikan sedikit kandidat ekstra. Aplikasi tidak memuat seluruh Fanpage dahulu lalu memotongnya, dan tidak berhenti setelah N dari sumber pertama tanpa mengunjungi Reels.

Kandidat yang berhasil diperoleh digabung/dideduplikasi, diurutkan secara global di antara kandidat tersebut, kemudian dibatasi maksimal N. Acuan tanggal Post adalah `created_time` pada Post terbit; [SDK Post](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/post.py) mengonfirmasi field, tetapi deskripsi semantik publikasi lengkap pada dokumentasi primer belum dapat dibaca. Pengguna perlu membandingkan tanggal ini dengan tab Postingan Facebook; keberadaan field tidak membuktikan semantik publikasi, termasuk konten backdated. Parser Python 3.10 menerima offset Graph +0000 dengan menormalkan format offset ke +00:00, tanpa mengubah waktu absolut. Jangan menafsirkan sort kandidat sebagai bukti kronologi seluruh sumber.

**created_time video/Reel tidak dipakai sebagai publikasi** karena waktu upload/pembuatan dapat berbeda. Reel hanya mendapat tanggal dari relasi Post terbit yang berhasil dimuat. Tidak ada pengganti dari updated_time, waktu server sekarang, atau jadwal publikasi. Tanggal hilang/invalid/naive ditampilkan sebagai **Tanggal tidak tersedia**, dikelompokkan setelah kandidat bertanggal. Tie-breaker stabil berbasis sumber+ID asli. Waktu absolut UTC dipakai untuk sort lalu dikonversi ke WIB (+07:00, Asia/Jakarta). Jenis Foto/Video memakai status API yang dikenal; jenis yang tidak terbukti lebih spesifik tetap Postingan.

Jika N kandidat bertanggal sudah mengisi batas, kandidat tanpa tanggal mungkin tidak ditampilkan; ini bukan bukti Reel tersebut lebih lama. Naikkan limit atau audit sumber yang belum tercakup secara terpisah bila diperlukan. Keterbatasan identitas/tanggal/cakupan ditampilkan, bukan disembunyikan sebagai keberhasilan lengkap.

| Status | Arti |
| --- | --- |
| `limit_reached` | Batas tampilan tercapai pada kandidat yang dimuat; bukan jaminan N terbaru global |
| `source_exhausted` | Listing konten yang dipakai sudah tidak punya next sebelum batas tercapai; bukan seluruh postingan Facebook pasti tersedia |
| `empty` | Tidak ada kandidat eligible; dibedakan dari error token/izin |
| `running` / `cooldown` | Pekerjaan belum selesai / menunggu timer lokal; kandidat diterima dapat diaudit |
| `cancelled` | Pengguna menghentikan pemuatan; hasil diterima tetap hanya untuk audit |
| `partial` | Request sumber, pagination, deadline, atau batas lokal benar-benar menghentikan pengambilan; jumlah dan error aman ditampilkan, pilihan hanya untuk audit |

`retrieval_complete` hanya berarti pengambilan **berdasarkan policy bounded lokal** selesai, bukan cakupan Facebook lengkap. Semua hasil tetap memiliki `coverage_complete=false`, `latest_guaranteed=false`, `safe_for_delete=false`. Tidak ada konten yang dinyatakan aman dihapus.

## Ringkasan sumber dan diagnosis

Buka **Ringkasan sumber API** untuk published_posts, video_reels, dan stories. Setiap sumber melaporkan jumlah request aktual **termasuk retry**, halaman valid, item mentah diterima, status dan alasan berhenti. Status membedakan batas tercapai, sumber habis, gagal, dibatalkan, dan pemeriksaan tidak diperlukan. Alasan spesifik mencakup timeout, cooldown/rate limit, izin, token invalid, batas halaman, pagination berulang/invalid, respons invalid, kegagalan upstream (termasuk retry layanan yang habis), dan pembatalan. Respons yang melampaui batas ukuran ditolak oleh transport dengan kode upstream_error; kode ini tidak mengklaim bentuk respons pasti invalid. Respons API menyimpan ringkasan pada `sources`, serta error per sumber pada `errors`; UI tidak menampilkan cursor, token, URL pagination, respons atau pesan Meta mentah.

Keberhasilan akuisisi, ketidakpastian cakupan/urutan/identitas, dan kegagalan nyata dilaporkan terpisah. Maksimal 3000 tidak mewajibkan 3000 hasil. Bila sumber habis pada 1743, status menyebut sumber habis; jika error/batas lokal, alasan sumber tersebut ditampilkan. **Penyebab hasil 1743 pada pengujian Facebook sebelumnya belum diketahui**; tes mock 1743 hanya membuktikan perilaku ketika API benar-benar habis, bukan menjelaskan kejadian itu.

Pengukuran mock pekerjaan bertahap (bukan Facebook nyata): limit 3000, posts 30 halaman masing-masing 100, Reels kosong dan Story kosong menghasilkan **3000 kandidat lewat beberapa langkah dan 32 request Meta**. Sumber Reels habis tidak dipanggil lagi, cursor posts berlanjut 100, 200, ... 2900. Maksimal enam percobaan request per langkah. Tes juga membuktikan timeout langkah menyimpan halaman sebelumnya dan cursor valid, cooldown tidak mengirim request, serta replay revisi tidak mengambil halaman ulang.

Tidak ada bukti resmi yang berhasil diakses untuk menyatakan published_posts/video_reels pasti bebas Facebook Story. SDK hanya membuktikan method/field tersedia. Karena itu request Story tidak dihapus, dan pemeriksaan satu halaman **tidak dijadikan bukti seluruh kandidat bebas Story**. Story yang identitasnya ditemukan dikeluarkan; bagian yang belum pasti ialah kandidat lain yang tidak dapat dibandingkan dengan histori Story lengkap. Tidak ada jalan pintas story_complete=True atau klaim persoalan cakupan selesai.

## Memori, keamanan, dan batas tahap 3

POST `/api/posts/jobs` menerima hanya `{"page_id":"11","limit":100}`. Page harus milik sesi dan memiliki page token; frontend tidak mengirim token. Start tidak meminta Meta, mengembalikan job_id opaque dan revisi. State memori menyimpan pemilik, Page ID, limit, kandidat, cursor/identitas dedup, statistik dan status; **tidak menyalin token ke pekerjaan**. Token diambil sementara dari sesi pada langkah yang aktif.

POST `/api/posts/jobs/{job_id}/step` menerima `{"revision":0}`. Hanya satu task langkah aktif: request bersamaan berbagi task, revisi lama mengembalikan snapshot saat ini tanpa akuisisi baru. Respons langkah yang hilang boleh diulang frontend maksimal dua kali dengan revisi sama. Start ulang dengan Page/limit sama saat aktif mengembalikan job yang sama, tanpa admission/request baru. GET `/api/posts/jobs/{job_id}` membaca progres memori tanpa Meta. POST `/api/posts/jobs/{job_id}/stop` idempotent menghentikan task/lanjutan, menyimpan audit yang diterima. Job/cursor/token tidak disimpan di browser storage; job_id bukan otorisasi tanpa cookie sesi pemilik.

Endpoint lama `/api/posts/load` dipertahankan sebagai jalur kompatibilitas kontrak tahap sebelumnya (satu operasi dengan POSTS_TIMEOUT, policy checkbox konservatif lama); UI tidak menggunakannya. GET `/api/posts?page_id=11` membaca cache terakhir pemilik tanpa Meta. Pagination UI hanya array lokal.

Satu job aktif per browser dan satu langkah aktif per job. Connect/load berbagi limiter admission browser/global dan dua slot pekerjaan global. Slot job dipertahankan selama job aktif, termasuk cooldown, lalu dilepas saat selesai/stop/expiry. Maksimal dua request Graph bersamaan melalui job/connect; AsyncClient dan pool tahap 2 tetap digunakan. Kelanjutan revisi tidak dihitung sebagai job baru. Reconnect saat job aktif ditolak.

| Batas pekerjaan baru | Default / perilaku |
| --- | --- |
| POSTS_STEP_TIMEOUT | 12 detik per langkah; halaman yang telah dikomit tetap tersedia |
| POSTS_STEP_REQUESTS | 6 percobaan request aktual termasuk retry per langkah |
| POSTS_JOB_TIMEOUT | 600 detik total, termasuk cooldown; selalu dibatasi sisa sesi |
| POSTS_JOB_REQUESTS / POSTS_JOB_STEPS | 300 percobaan request / 200 langkah total |
| POSTS_MAX_PAGES | 50 halaman valid per sumber; next sah wajib, cursor/halaman berulang ditolak |
| REQUEST_TIMEOUT / CONNECT_TIMEOUT | 10 / 5 detik transport; timeout request Meta berbeda dari batas langkah |
| Retry halaman | Maksimal tiga percobaan (awal + dua retry), dihitung lintas langkah pada cursor yang sama; tidak direset oleh request frontend |
| Timeout langkah berulang | Maksimal tiga interupsi halaman yang belum selesai; setelah itu sumber gagal, tidak retry tanpa akhir |
| CONTENT_JOB_IDLE_TTL | 120 detik tanpa kelanjutan; cooldown yang sah memberi waktu hingga resume + TTL, tetap tunduk pada sesi/600 detik total |
| MAX_CONTENT_JOBS / MAX_CONTENT_JOB_ITEMS | 20 entri job (aktif + selesai), 20000 slot kandidat konservatif total; aktif tetap maksimal MAX_JOBS=2 |
| MAX_CONTENT_JOB_BYTES | 128 MiB budget reservasi kandidat; sebelum start dicadangkan (2*limit+200)*8192 + 65536 byte/job |
| STORY_MAX_ITEMS | 5000 batas atas konfigurasi lama; aktual satu halaman, min(limit pengguna, PAGE_SIZE=100, nilai ini) |
| AUDIT_TTL / MAX_AUDIT_ENTRIES | 300 detik / 20 hasil cache; cache terpisah dari kandidat kerja |
| MAX_AUDIT_ITEMS / MAX_AUDIT_BYTES | 20000 hasil / 64 MiB budget serialisasi; reservasi sebelum request |

Deadline langkah hanya mengulang halaman yang belum diterima, dengan cursor terakhir valid; halaman yang sudah berhasil dikomit tidak diulang. Timeout request Meta setelah retry terbatas membuat sumber gagal/hasil parsial, sementara halaman sebelumnya tetap ada. Error permanen sumber tidak dicoba ulang; sumber lain yang masih dapat dibaca dapat menyelesaikan akuisisi terbatas. Batas total menghasilkan partial dengan alasan job_timeout/job_request_limit/job_step_limit. Kegagalan Story tidak mengubah audit menjadi aman dihapus. Hasil kosong, sumber habis, batas unik, partial, cooldown, dan pembatalan dibedakan.

Sesi tetap **20 menit dari koneksi**, tidak diperpanjang load/status/revisi. Cleanup periodik dan validasi tiap endpoint menghapus/cancel job saat sesi hilang/expired; tidak ada pekerjaan yang menyimpan token setelah sesi pemiliknya berakhir. Disconnect menghapus job/cache/sesi; pindah Fanpage frontend mengirim stop dan menolak respons generasi lama. Job idle dibuang, bukan dilanjutkan tanpa akhir. Cache audit akhir juga habis saat restart/disconnect/expiry. Angka byte adalah budget serialisasi/reservasi konservatif, bukan ukuran heap Python pasti; kandidat diterima dibatasi sebelum insertion.

Thumbnail hanya HTTPS pada fbcdn.net/fbsbx.com dan subdomainnya. Permalink hanya HTTPS Facebook/fb.watch; URL berkredensial, skema lain, query token, dan token yang terefleksi ditolak. CSP hanya memperluas **img-src** ke CDN tersebut; script-src/connect-src tetap origin sendiri. Gambar lazy-loaded dan memakai no-referrer. Teks API menggunakan textContent. Tidak menjanjikan bebas rate limit.

Pengujian tahap 3 memakai HTTP mock dan token dummy, ditambah DOM/fetch simulasi frontend. **Belum ada verifikasi Load Posts terhadap Facebook nyata atau pengujian visual browser nyata oleh agen.** Keberhasilan koneksi token asli tahap 2 dilaporkan pengguna, bukan bukti cakupan konten tahap 3.

## Menjalankan di Windows

Gunakan PowerShell dan Python 3.10.6 dari virtual environment yang sudah tersedia:

```powershell
Set-Location 'D:\FACEBOOK META ADS\fanpage-cleaner'
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --no-access-log
```

Buka **http://127.0.0.1:8000**, bukan nama host lain. Gunakan satu proses Uvicorn: sesi, cooldown, limiter, dan reservasi pekerjaan berada dalam memori proses tersebut. `--no-proxy-headers` mencegah kepercayaan pada forwarded headers; `--no-access-log` menghindari pencatatan URL request yang mungkin salah diisi pengguna. Jangan mengaktifkan logging body/header atau debug transport yang mencatat rahasia.

Tekan `Ctrl+C` untuk berhenti. Sesi hilang saat server restart. Menambahkan `--reload` untuk pengembangan dapat menghapus sesi saat kode berubah. Jangan menjalankan beberapa worker atau instance sekaligus.

`requirements.txt` tetap menggunakan versi paket utama yang terpasang. Tahap ini tidak memasang paket atau mengubah `.venv`. Node tidak diperlukan untuk menjalankan aplikasi.

## Mencoba Hubungkan

1. Buka halaman dan tunggu pemeriksaan sesi lokal selesai.
2. Masukkan **user access token Facebook** pada input password, lalu klik **Hubungkan**. Jangan bagikan token lewat chat, URL, atau kode.
3. Jika seluruh pagination selesai dan hasil tidak kosong, dropdown menampilkan nama Fanpage + Page ID dan jumlah Fanpage, dengan pesan untuk memilih Fanpage dan menggunakan Load Posts untuk audit.
4. Hasil kosong menampilkan pesan tersendiri, tanpa keberhasilan menyesatkan. Hasil kosong tidak otomatis berarti token invalid.
5. Refresh membaca daftar dari memori backend, tanpa request tambahan ke Meta. **Putuskan Koneksi** menghapus sesi/token backend dan cookie sesi. Cookie identitas browser tanpa token tetap ada untuk pembatas lokal, lalu kedaluwarsa sendiri.

Load Posts aktif setelah sesi dan Fanpage tersedia. Hapus Terpilih tetap nonaktif; Berhenti membedakan pemuatan/dry run. Tidak ada tombol pembatalan request DELETE yang mungkin sudah terkirim. Kemunculan Fanpage tidak membuktikan izin menghapus. Page tanpa page token tetap muncul pada dropdown tetapi load/delete ditolak dengan pesan yang jelas.

## Endpoint dan keamanan

| Endpoint | Perilaku |
| --- | --- |
| `GET /` | HTML tanpa token atau sesi tertanam |
| `GET /health` | `{"status":"ok"}` |
| `GET /api/session` | Status dari memori; bootstrap identitas browser bila diperlukan |
| `POST /api/connect` | Body JSON `{"token":"…"}`; mengganti sesi setelah daftar lengkap |
| `POST /api/disconnect` | Menghapus sesi dan cookie sesi, tanpa request Meta |
| `POST /api/posts/jobs` | Memulai pekerjaan lokal dengan Page ID/limit |
| `POST /api/posts/jobs/{id}/step` | Melanjutkan revisi pekerjaan pemilik dengan batas langkah |
| `GET /api/posts/jobs/{id}` | Progres dari memori, tanpa Meta |
| `POST /api/posts/jobs/{id}/stop` | Menghentikan pemuatan, tanpa DELETE |
| `POST /api/posts/load` | Jalur kompatibilitas satu operasi; tidak digunakan UI |
| `GET /api/posts?page_id=…` | Hasil audit terakhir dari memori pemilik saja |
| `POST /api/posts/delete/prepare` | Snapshot satu Post dari key audit, konfirmasi sekali pakai; tanpa Meta |
| `POST /api/posts/delete/execute` | Satu DELETE v26.0 setelah konfirmasi manual terikat target/sesi |

API membutuhkan `X-Fanpage-Client: 1`. POST juga membutuhkan `Origin` persis sama dengan `APP_ORIGIN`. `Sec-Fetch-Site` lintas origin ditolak; host hanya `127.0.0.1`, klien hanya loopback, tanpa CORS wildcard. ID sesi hanya dibaca dari cookie, bukan query/body. Cookie sesi dan browser berisi ID acak opaque 256 bit, `HttpOnly`, `SameSite=Strict`, `Path=/`, tanpa `Domain`. Cookie sesi harus cocok dengan identitas browser pemiliknya.

Token user dan Page hanya disimpan dalam memori backend. Cookie tidak berisi token. Frontend tidak memakai localStorage/sessionStorage dan membersihkan input saat request dikirim maupun selesai. Respons koneksi/status Fanpage hanya berisi nama dan ID Page; respons audit berisi metadata konten yang dinormalisasi. Semua teks dirender dengan `textContent` dan rahasia yang terefleksi disamarkan. Respons terkait sesi memakai `Cache-Control: no-store`; CSP membatasi script/koneksi frontend ke origin sendiri.

Tidak ada respons Meta, pesan Meta, Authorization, body token, URL pagination, atau exception mentah yang dikirim ke frontend/dicatat aplikasi. Diagnostik memakai kode lokal, pesan tetap Indonesia, bila relevan retry_after/cause_code, serta untuk single delete HTTP status/kode-subkode numerik/ID target/durasi/outcome yang dipilih secara aman.

Koneksi baru baru mengganti sesi lama setelah pagination lengkap. Kegagalan parsial tidak disimpan dan sesi lama yang masih berlaku dipertahankan. Status lokal tidak membuktikan token masih valid di Meta karena tidak ada polling/pengecekan ulang. Backend menolak Hubungkan bersamaan dari identitas browser yang sama, termasuk sebelum ada sesi Facebook; frontend wajib bootstrap status dahulu. Dua tab browser yang sama berbagi cookie/sesi. Putus saat koneksi berjalan ditolak agar pekerjaan lama tidak membangkitkan sesi kembali.

## Konfigurasi dan batas lokal

Gunakan environment variable PowerShell. Aplikasi tidak membaca `.env` dan tidak membutuhkan token di environment. Semua field `Settings` pada `config.py` dapat dikonfigurasi dengan nama huruf kapital.

```powershell
$env:GRAPH_VERSION = 'v26.0'
$env:APP_ORIGIN = 'http://127.0.0.1:8000'
```

Jika port server berubah, port `APP_ORIGIN` juga harus berubah. Untuk HTTPS loopback yang disiapkan sendiri, gunakan origin `https://127.0.0.1:PORT` dan `$env:COOKIE_SECURE = 'true'`. Default `false` memungkinkan HTTP localhost. TLS tidak disiapkan otomatis.

| Variable | Default | Batas lokal / alasan |
| --- | --- | --- |
| `GRAPH_VERSION` | `v26.0` | Pin berdasarkan SDK resmi yang dapat diverifikasi, bukan klaim versi terbaru |
| `APP_ORIGIN` / `COOKIE_SECURE` | `http://127.0.0.1:8000` / `false` | Satu origin dipercaya; Secure untuk HTTPS |
| `SESSION_TTL` / `BROWSER_TTL` | `1200` / `1800` detik | Sesi maksimal 20 menit per koneksi sukses; browser idle 30 menit, harus ≥ TTL sesi |
| `MAX_SESSIONS` | `100` | Tolak saat penuh, tanpa menyingkirkan sesi aktif lain |
| `MAX_BROWSER_ENTRIES` / `MAX_TOKEN_ENTRIES` | `512` / `512` | Batas memori browser/limiter/cooldown |
| `CLEANUP_INTERVAL` | `30` detik | Pembersihan periodik, juga sebelum operasi state |
| `LOCAL_WINDOW` / `LOCAL_ATTEMPTS` | `60` detik / `3` | Maksimal tiga pekerjaan Hubungkan per browser per menit |
| `GLOBAL_ATTEMPTS` | `12` | Batas pekerjaan seluruh proses per window, termasuk identitas baru |
| `MAX_JOBS` | `2` | Maksimal dua koneksi berjalan; slot sesi dicadangkan sebelum request Meta |
| `OPERATION_TIMEOUT` | `30` detik | Seluruh pagination dan retry koneksi |
| `REQUEST_TIMEOUT` / `CONNECT_TIMEOUT` | `10` / `5` detik | Timeout baca/tulis dan connect/pool; body lokal dibaca maksimal 5 detik |
| `MAX_CONNECTIONS` / `MAX_KEEPALIVE_CONNECTIONS` | `4` / `2` | Pool AsyncClient eksplisit dan kecil |
| `MAX_PAGES` / `PAGE_SIZE` | `20` / `100` | Pengambilan terbatas, metadata maksimal 2.000 Page |
| `MAX_RESPONSE_BYTES` | `1048576` | Maksimal 1 MiB per respons Meta dengan streaming |
| `MAX_BODY_BYTES` / `MAX_TOKEN_LENGTH` | `16384` / `8192` | Maksimal 16 KiB JSON / 8.192 karakter token, termasuk chunked body |
| `RETRY_BASE` / `RETRY_JITTER` | `0.5` / `0.25` detik | Exponential backoff + jitter; maksimal dua retry per request |
| `MAX_RETRY_WAIT` | `5` detik | Instruksi tunggu lebih lama menjadi cooldown, tanpa retry dini |
| `COOLDOWN_SECONDS` / `USAGE_THRESHOLD` | `60` detik / `90` | Cooldown lokal minimal untuk rate limit/usage tinggi |

Nilai ini adalah **batas konservatif aplikasi, bukan kuota resmi Meta** dan tidak menjamin bebas rate limit Facebook. Refresh tidak memperpanjang TTL sesi. Kapasitas limiter penuh ditolak. Key cooldown token memakai HMAC dengan salt acak per proses, bukan raw token. Bila tabel cooldown penuh, gunakan cooldown global konservatif tanpa menghapus cooldown aktif.

Satu `httpx.AsyncClient` dibuat saat startup dan ditutup saat shutdown. Token dikirim melalui header Bearer per request, tanpa redirect/proxy environment. Pagination berurutan berlanjut hanya jika `paging.next` ada, bukan sekadar `after`. URL berikutnya harus HTTPS, host `graph.facebook.com`, versi/path accounts yang diizinkan. Request berikut dibangun dari endpoint tetap `/me/accounts` dan cursor tervalidasi; URL `next`/parameter token tidak diikuti. Cursor yang mengandung token diketahui ditolak. Page ID dideduplikasi; cursor/halaman berulang, batas halaman, atau pengambilan terputus menghasilkan error, bukan daftar lengkap.

Retry hanya untuk gangguan transport sementara, HTTP 500/502/503/504, atau error bertanda `is_transient=true`. Token invalid, izin ditolak, redirect, payload rusak, dan rate limit tidak di-retry langsung. Pemetaan lokal ada pada `graph_api.py`; pemetaan resmi lengkap belum dapat diverifikasi (lihat di bawah).

`Retry-After` valid berupa detik nonnegatif atau HTTP-date dihormati. Jika lebih lama dari 5 detik atau sisa deadline, operasi berhenti dan cooldown mempertahankan durasi instruksi tersebut; tidak dipotong agar retry lebih awal. Instruksi pendek ditunggu sebelum retry. Header rusak diabaikan dan backoff lokal tetap berlaku. Frontend tidak otomatis retry.

Rate limit yang diklasifikasikan sebagai user (kode 17) memakai lingkup HMAC token; app/Page/lingkup tidak diketahui memakai cooldown seluruh proses. Header `X-App-Usage` dan `X-Business-Use-Case-Usage`, bila tersedia, diperiksa pada field numerik `call_count`, `total_time`, `total_cputime`. Nilai ≥ ambang lokal memicu cooldown global konservatif. Header tidak ada/rusak berarti **penggunaan tidak diketahui**, bukan nol. `estimated_time_to_regain_access` tidak diinterpretasikan karena satuan/aturan endpoint ini belum terverifikasi; aplikasi tidak mengarang waktu reset resmi.

## Sumber Meta dan keterbatasan verifikasi

Diperiksa **13 Oktober 2026**. Dokumentasi utama Meta tidak dapat dibaca dari lingkungan ini (HTTP 429 / gagal fetch): [changelog](https://developers.facebook.com/docs/graph-api/changelog/), [User Accounts](https://developers.facebook.com/docs/graph-api/reference/user/accounts/), [pagination](https://developers.facebook.com/docs/graph-api/results/), [access tokens](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/), [error handling](https://developers.facebook.com/docs/graph-api/guides/error-handling/), dan [rate limiting](https://developers.facebook.com/docs/graph-api/overview/rate-limiting/).

Sumber resmi alternatif yang dapat dibaca:

- [Konfigurasi SDK Meta](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/apiconfig.py): versi API `v26.0`, dasar default eksplisit.
- [User SDK Meta](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/user.py): GET `/accounts` menghasilkan Page; [field Page](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/page.py) memuat `id`, `name`, `access_token`.
- [Cursor SDK Meta](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/api.py): kelanjutan memakai `paging.next` dan `after`; `after` saja tidak cukup.
- [Error SDK Meta](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/exceptions.py): bentuk `error`, `code`, `is_transient`.
- [Koleksi Facebook milik Meta di Postman](https://www.postman.com/meta/facebook/request/bqfxwbp/get-access-tokens-of-pages-you-manage): `/me/accounts` memakai user token untuk daftar Page dan page token.
- [Koleksi Graph API milik Meta di Postman](https://www.postman.com/meta/whatsapp-business-platform/documentation/3kru5r6/moved-whatsapp-business-management-api): contoh Bearer dan struktur header usage. Contoh berasal dari produk lain pada Graph, bukan bukti header selalu ada pada `/me/accounts`.

**Belum terverifikasi:** versi terbaru/jadwal dukungan, persyaratan izin/App Review/akses Business yang lengkap, seluruh pemetaan kode/subkode Meta, serta aturan/satuan/reset limit khusus endpoint ini. Default dan policy lokal tidak menjamin token tertentu berhasil. Periksa izin pada dokumentasi/dashboard Meta yang dapat Anda akses; aplikasi tidak menambah izin melalui OAuth.

Pengujian menggunakan token dummy dan mock HTTP, tanpa request Meta sungguhan. Koneksi Facebook langsung perlu dicoba sendiri lewat browser. Pengujian frontend memakai DOM/fetch simulasi, belum pengujian visual browser nyata.

## Pengujian lokal

Tidak perlu pytest atau paket tambahan:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile app.py config.py errors.py sessions.py graph_api.py content.py content_cache.py content_jobs.py bulk_targets.py bulk_queue.py bulk_scheduler.py bulk_executor.py bulk_checkpoint.py request_governor.py single_delete.py
```

Jika Node tersedia, tes frontend tanpa npm install:

```powershell
node --check static/app.js
node --test tests/frontend.test.cjs
```

Suite mencakup pagination/deduplikasi/loop/batas/hasil parsial, validasi token/body, error/retry/cooldown, kapasitas/reservasi/concurrency, isolasi/expiry/cleanup/disconnect, CSRF/host, refresh lokal, kebocoran token, redirect/URL asing, serta interaksi frontend.

Saat server berjalan, pemeriksaan read-only:

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/).StatusCode
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/session -Headers @{'X-Fanpage-Client'='1'}
```

## File

- `app.py`: entry point, lifecycle, route, validasi body/origin/cookie/security headers.
- `config.py`: konfigurasi environment tervalidasi.
- `graph_api.py`: Graph client, pagination, retry, inspeksi usage.
- `sessions.py`: memori sesi/browser, limiter/reservasi/cooldown.
- `errors.py`: pesan Indonesia dan kode error aman.
- `single_delete.py`, `tests/test_single_delete.py`: konfirmasi manual sekali pakai, satu Post, lock bersama, diagnostik aman dan mock regresi.
- `content_jobs.py`: pekerjaan bertahap, cursor backend, revisi idempotent, TTL/capacity/stop.
- `content.py`, `content_cache.py`: listing/normalisasi/dedup/sort kandidat dan cache audit bounded.
- `templates/index.html`, `static/style.css`, `static/app.js`: halaman responsif.
- `tests/test_jobs.py`: regresi pekerjaan bertahap, ribuan kandidat, cursor, timeout/cooldown/stop/expiry/ownership.
- `tests/test_app.py`, `tests/test_content.py`, `tests/frontend.test.cjs`: regresi koneksi, konten HTTP mock, dan interaksi frontend.
- `docs/stage2-plan.md`: rencana implementasi dan review tahap ini.
- `docs/stage3-plan.md`: bukti sumber dan rencana tahap audit.
- `docs/resumable-load-plan.md`: rancangan dan batas pekerjaan bertahap serta hambatan bukti Story.
- `docs/load-efficiency-plan.md`: bukti, policy efisiensi, dan rencana regresi perbaikan Load Posts.
- `requirements.txt`, `.gitignore`: versi paket dan pengecualian rahasia/cache/runtime.

Modul tahap 4: bulk_targets.py (identitas/kelayakan), bulk_queue.py (antrean/fairness/ownership), bulk_scheduler.py (worker), bulk_executor.py (dry run/kontrak), bulk_checkpoint.py (ledger atomik), request_governor.py (pacing/concurrency bersama). Regresi tambahan: tests/test_bulk.py; rancangan: docs/stage4-plan.md.
