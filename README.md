# Fanpage Cleaner

Aplikasi FastAPI + Jinja2 lokal berbahasa Indonesia untuk Page Post biasa: teks/status/link dan foto. **Cakupan operasi hanya Post biasa dan foto; tidak ada jalur penghapusan Reel/video atau target Story yang teridentifikasi.** Satu-satunya sumber konten adalah `published_posts`; metadata jenis, status dan attachment dipakai untuk menyaring cakupan. Konten video/Reel/Story yang teridentifikasi dan metadata yang tidak pasti dilewati. Tidak ada permintaan ke edge `video_reels`, `videos` atau `stories`, pemindaian Story tambahan, ataupun endpoint UI hapus Reel.

Penyaringan metadata bukan jaminan seluruh Story selalu teridentifikasi. **Pemeriksaan manual seluruh link, ID, Page dan daftar konkret tetap wajib sebelum konfirmasi.** Penghapusan hanya memakai ID Page Post asli dari audit backend dengan `operation_scope=page_post`; ID foto tidak menjadi target alternatif dan penghapusan objek foto terpisah tidak dijamin. Audit lama dengan cakupan berbeda ditolak: lakukan **Load Posts kembali** sebelum menyiapkan konfirmasi baru.

## Alur utama dan kapasitas

**Hubungkan lalu cari/pilih Fanpage lalu isi maksimal konten lalu Load Posts lalu pilih lalu Hapus Terpilih lalu periksa snapshot lalu konfirmasi lalu progres otomatis.** Cari Fanpage dan pagination daftar/preview berjalan lokal. Pilih Semua mencakup seluruh hasil load, termasuk halaman yang tidak terlihat. Maksimal **5000 pilihan** per konfirmasi, preview **50 target per halaman**, dengan **2 worker global**, concurrency Graph **2** dan pacing awal **1 dispatch/detik** bersama GET/DELETE.

Limit load menghitung konten yang lolos filter, bukan menjamin jumlah hasil. Konten yang dilewati beserta alasan metadata singkat tercatat pada diagnosis sumber tertutup; jika sumber habis atau batas pekerjaan tercapai, hasil dapat lebih sedikit. Alat tambahan tertutup memuat **Hapus 1 Post**, **Periksa Akses Post**, diagnosis hasil gagal, Dry Run dan pembacaan antrean. Diagnosis opsional; tidak ada GET diagnosis otomatis atau retry DELETE.

Konfirmasi bulk terikat sesi dengan TTL default **600 detik**, single Post **60 detik**, keduanya dibatasi sisa sesi. Sesi tetap **20 menit**. Kapasitas 5000 pilihan bukan janji seluruhnya selesai dalam satu sesi. Stop, disconnect dan expiry menghentikan dispatch baru sambil menunggu request yang sudah terkirim. Setiap DELETE dicoba sekali; sukses memerlukan HTTP 2xx, JSON object, `success` boolean true dan tanpa error. Timeout/network setelah kemungkinan terkirim menjadi uncertain dan tidak diulang otomatis.

Progres lokal mengirim maksimal **50 detail target** dan **100 transisi** per polling, memakai revisi/cursor dan resinkronisasi ringkas jika riwayat tertinggal. Hasil succeeded membersihkan daftar/pilihan; failed/uncertain tetap terlihat. Checkpoint nonrahasia atomik dikarantina setelah restart; reconnect tidak melanjutkan penghapusan lama secara otomatis. Token hanya berada dalam memori.

## Filter pemuatan

Fields `published_posts`: `id,from,is_published,created_time,scheduled_publish_time,message,full_picture,permalink_url,status_type,attachments{type,subattachments{type,subattachments{type}}}`. Attachment diminta dalam request listing yang sama, tanpa GET per item. Classifier menerima metadata lengkap untuk status biasa/link/foto dan attachment `photo`, `album`, `link`, `share`; album harus memiliki subattachment yang seluruhnya didukung. Video/Reel, attachment campuran yang mengandung jenis di luar scope/tidak dikenal, identitas tidak pasti, serta koneksi attachment terpotong dilewati. `shared_story` tidak dianggap Story 24 jam hanya berdasarkan nama.

Bila field attachment tidak dikembalikan, bahkan Post teks dapat dilewati sebagai metadata belum cukup; aplikasi tidak menganggap field hilang sebagai daftar kosong. Sumber/hasil melaporkan jumlah dan alasan yang dilewati serta sumber habis, batas, timeout atau pengambilan parsial. Limit dihitung dari kandidat unik yang lolos filter, urutan tanggal diketahui terbaru ke terlama; urutan N terbaru seluruh Fanpage tidak dijamin.

[SDK resmi Meta Post](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/post.py) menunjukkan `status_type` dan edge attachments. Referensi StoryAttachment terhalang saat verifikasi; dukungan nested fields pada setiap token/versi tetap mengikuti respons Meta, tanpa fallback atau klaim cakupan universal. Allowlist di atas adalah filter konservatif aplikasi, bukan bukti seluruh Facebook Story otomatis dikecualikan. [Pages API Posts](https://developers.facebook.com/documentation/pages-api/posts) mendasari operasi DELETE ID Page Post dan kontrak `success: true`.

Snapshot baru menyimpan versi cakupan dan metadata klasifikasi nonrahasia. Konfirmasi lama yang tidak kompatibel ditolak, dan target antrean diperiksa lagi sebelum dispatch. Tidak ada fallback ke ID foto/media; hasil request yang sudah terkirim tetap dicatat sekali.

## Diagnosis Post

Setelah restart: **Hubungkan lalu pilih Fanpage lalu Load Posts lalu pilih tepat satu Post lalu Periksa Akses Post**. Tidak perlu melakukan DELETE untuk mendapatkan diagnosis. `POST /api/delete-diagnosis` menerima `{page_id,keys:[key]}` dari audit aktif, atau `{job_id,key}` / `{last_result:true}` untuk hasil gagal milik sesi. ID/token bebas dan ID dari URL ditolak.

Pemeriksaan GET saja memakai token sesi saat ini dengan budget maksimal **5 request termasuk retry**, governor/pacing/cooldown bersama: permissions, tasks Page, metadata ID Post, application/object_id dan attachment terbatas. Data hilang/parsial tetap tidak diketahui. Sukses GET tidak membuktikan DELETE diizinkan; gagal GET tidak membuktikan objek sudah terhapus. Diagnosis sesi baru tidak membuktikan keadaan token lama dan tidak memulihkan hasil lama. Tidak ada request Meta nyata dalam pengujian lokal agen.

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
| `GET /api/posts/delete/last` | Hasil terakhir/processing dari memori sesi pemilik; tanpa request Meta |

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
| `COOLDOWN_SECONDS` / `USAGE_THRESHOLD` | `60` detik / `90` | Minimum rate limit dan satu jeda saturasi; 90 adalah ambang pacing |

Nilai ini adalah **batas konservatif aplikasi, bukan kuota resmi Meta** dan tidak menjamin bebas rate limit Facebook. Refresh tidak memperpanjang TTL sesi. Kapasitas limiter penuh ditolak. Key cooldown token memakai HMAC dengan salt acak per proses, bukan raw token. Bila tabel cooldown penuh, gunakan cooldown global konservatif tanpa menghapus cooldown aktif.

Satu `httpx.AsyncClient` dibuat saat startup dan ditutup saat shutdown. Token dikirim melalui header Bearer per request, tanpa redirect/proxy environment. Pagination berurutan berlanjut hanya jika `paging.next` ada, bukan sekadar `after`. URL berikutnya harus HTTPS, host `graph.facebook.com`, versi/path accounts yang diizinkan. Request berikut dibangun dari endpoint tetap `/me/accounts` dan cursor tervalidasi; URL `next`/parameter token tidak diikuti. Cursor yang mengandung token diketahui ditolak. Page ID dideduplikasi; cursor/halaman berulang, batas halaman, atau pengambilan terputus menghasilkan error, bukan daftar lengkap.

Retry hanya untuk gangguan transport sementara, HTTP 500/502/503/504, atau error bertanda `is_transient=true`. Token invalid, izin ditolak, redirect, payload rusak, dan rate limit tidak di-retry langsung. Pemetaan lokal ada pada `graph_api.py`; pemetaan resmi lengkap belum dapat diverifikasi (lihat di bawah).

`Retry-After` valid berupa detik nonnegatif atau HTTP-date dihormati. Jika lebih lama dari 5 detik atau sisa deadline, operasi berhenti dan cooldown mempertahankan durasi instruksi tersebut; tidak dipotong agar retry lebih awal. Instruksi pendek ditunggu sebelum retry. Header rusak diabaikan dan backoff lokal tetap berlaku. Frontend tidak otomatis retry.

Cooldown dari `Retry-After`, HTTP 429 dan kode rate-limit dicatat terpisah dari jeda kebijakan lokal. Instruksi Meta tidak diperpendek oleh observasi usage yang membaik. Scope memakai HMAC token terkait; browser dengan token sama berbagi jeda. Aplikasi belum memiliki pemetaan App ID/business bucket yang tervalidasi, sehingga tidak menganggap header tertentu sebagai perintah menghentikan seluruh token yang tidak terkait. Pacing GET/DELETE tetap global untuk seluruh browser.

Header `x-app-usage` dibaca sebagai object dengan `call_count`, `total_time`, `total_cputime`; `x-business-use-case-usage` sebagai object ID numerik ke array bucket bertipe. Hanya nilai numerik valid disimpan, tanpa header mentah, ID bucket atau token pada diagnosis. Contoh struktur berasal dari [koleksi resmi Meta di Postman](https://www.postman.com/meta/whatsapp-business-platform/documentation/3kru5r6/moved-whatsapp-business-management-api); contoh produk WhatsApp ini tidak membuktikan ketersediaan header atau scope limit setiap endpoint Page. [Referensi rate limiting Meta](https://developers.facebook.com/docs/graph-api/overview/rate-limiting/) terhalang HTTP 429 saat diperiksa. `estimated_time_to_regain_access` tidak diinterpretasikan karena satuan/aturan endpoint ini belum terverifikasi.

Kebijakan lokal: usage >=90% meningkatkan interval minimal 6x pacing awal dan bertahap 25%, maksimal `GRAPH_MAX_INTERVAL` (default 10 detik). Usage 50-79% mempertahankan minimal 2x, 80-89% minimal 4x. Usage >=100% memberi satu jeda lokal `COOLDOWN_SECONDS` (default 60 detik) per episode saturasi, lalu melanjutkan pada pacing maksimum; respons sukses tinggi berikutnya tidak mengulang jeda tetap. Episode diaktifkan kembali setelah observasi bucket turun di bawah ambang 90% atau observasinya tidak lagi segar (5 menit). Rate limit nyata tetap dapat menjeda lagi sesuai instruksi/kebijakan minimum.

Interval turun 20% bertahap setelah tiga respons baru dengan seluruh metrik yang diketahui lengkap dan <50%; tidak turun di bawah pacing awal. Header hilang, rusak, atau metrik parsial bukan usage nol dan tidak memberi suara pemulihan. Bucket lain yang masih tinggi menahan pemulihan. Ini kebijakan aplikasi, bukan kuota resmi Meta; default dua worker tetap berlaku. Riwayat bucket dibatasi MAX_TOKEN_ENTRIES; jika penuh, bucket baru mempertahankan pacing maksimum dan tidak memasang jeda lokal berulang tanpa state episode. Cooldown rate limit nyata tetap berlaku.

Progres menampilkan alasan singkat seperti "Penggunaan API tinggi" atau "Meta meminta menunggu". Detail tertutup mencatat sumber header/kode, scope, akhir jeda UTC, nilai usage, interval, dan jeda terakhir yang tersimpan di memori. Diagnosis tidak menyimpan token/header mentah ke checkpoint. Tidak ada GET khusus untuk memeriksa usage. Penyebab percobaan nyata 40 sukses/60 queued belum dapat dipastikan tanpa metadata runtime; tes mock membuktikan perbaikan kebijakan, bukan keadaan kuota akun tersebut.

## Pengujian lokal

Tidak perlu pytest atau paket tambahan:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile app.py config.py errors.py sessions.py graph_api.py content.py content_cache.py content_jobs.py bulk_targets.py bulk_queue.py bulk_scheduler.py bulk_executor.py bulk_checkpoint.py request_governor.py single_delete.py bulk_delete.py
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


Verifikasi frontend cakupan Post biasa/foto: **73 tes lulus**, termasuk penolakan preview video, tidak adanya tombol/endpoint Reel, audit lama meminta Load Posts baru, serta regresi search/pagination/5000 target/cooldown/diagnosis. Pengujian memakai mock/token dummy; tidak ada request Meta nyata.


Verifikasi perubahan scope: regresi lengkap 217 tes backend lulus (termasuk antrean 5000 target mock). Setelah review filter terakhir, 36 tes konten dan 24 tes pekerjaan bertahap lulus, termasuk metadata rusak, foto dengan link, campuran video, dan snapshot/audit lama. Frontend 73 tes lulus; sintaks Python/JavaScript dan git diff --check lulus. Semua pengujian memakai mock/token dummy; agen tidak menjalankan DELETE Facebook nyata.
