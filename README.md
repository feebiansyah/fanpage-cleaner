# Fanpage Cleaner

Aplikasi FastAPI + Jinja2 satu halaman berbahasa Indonesia. Koneksi tahap 2 menerima **Facebook user access token** dan daftar Fanpage lewat `GET /me/accounts`. Tahap 3 menambahkan **Load Posts untuk audit**. Tidak ada OAuth, pertukaran/perpanjangan token, database, data konten contoh, atau penghapusan. Seluruh request aplikasi ke Meta tetap GET, tanpa DELETE.

## Load Posts: mulai dengan limit 20

1. Jalankan server satu proses sesuai perintah Windows di bawah. Hubungkan token melalui input password, lalu pilih Fanpage.
2. Isi **Maksimal postingan = 20**, kemudian klik **Load Posts**. Default 100, minimum 1, maksimum lokal 5000; tidak ada mode mengambil semua.
3. Lihat jumlah kandidat unik ditemukan, jumlah ditampilkan, status hasil, serta peringatan cakupan. Angka maksimal berlaku pada hasil akhir setelah deduplikasi dan pengecualian Story yang teridentifikasi.
4. Cocokkan daftar dengan tab **Postingan** Fanpage di aplikasi Facebook. Nomor, jenis, teks, thumbnail yang tersedia, link Facebook, dan tanggal acuan API dalam WIB ditampilkan. Postingan disematkan/susunan khusus dapat membuat urutan aplikasi Facebook berbeda.
5. Halaman tampilan berisi maksimal **50 konten**. Sebelumnya/Berikutnya hanya membaca array lokal dan tidak meminta Meta lagi. Pilih Semua memilih semua konten eligible yang dimuat, bukan hanya halaman terlihat; jumlah terpilih ditampilkan. Pilihan hanya untuk audit, tanpa delete target.
6. Ganti Fanpage untuk membersihkan daftar/pilihan lama. Respons load Page lama diabaikan. Putuskan Koneksi saat load membatalkan pekerjaan backend dan mencegah hasil lama muncul kembali.

**Hapus Terpilih dan Berhenti tetap nonaktif.** Tidak ada fungsi penghapusan. Jika pemeriksaan Story gagal atau relasi Story tidak mencukupi, checkbox individual juga nonaktif; pada semua hasil parsial Pilih Semua nonaktif.

## Sumber API tahap 3 dan bukti

Verifikasi sebelum implementasi memakai source **SDK resmi Meta**, dibaca 13 Oktober 2026. Halaman dokumentasi utama masih gagal diakses (429 / gagal fetch). Keberadaan POST publikasi tidak dipakai sebagai bukti listing.

| Sumber | Bukti resmi / penggunaan |
| --- | --- |
| `GET /{page-id}/published_posts` | [Page SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/page.py) memiliki `get_published_posts`, menghasilkan PagePost. Dipilih sebagai sumber postingan Page terbit, termasuk foto/video, bukan feed pengunjung. [PagePost SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/pagepost.py) mendeklarasikan field yang diminta. |
| `GET /{page-id}/video_reels` | SDK Page memiliki **get_video_reels**, method GET, menghasilkan AdVideo. [AdVideo SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/advideo.py) memuat `published`, `from`, `post_id`, `scheduled_publish_time`, dan metadata visual. Ini bukti listing terpisah dari POST upload/publish. |
| `GET /{page-id}/stories` | SDK Page memiliki `get_stories`; [Stories SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/stories.py) memuat `id`, `post_id`, `media_id`, status PUBLISHED/ARCHIVED. Dipakai untuk mengecualikan ID, bukan menjadi konten audit. |

Tidak menambahkan `/videos` sebagai listing terpisah yang menduplikasi postingan video. Tidak ada feed pengunjung, draft/scheduled/ads listing, atau fallback endpoint. Tiga sumber merupakan dua listing konten ditambah indeks Story untuk pengecualian. Bila sumber tidak dapat dibaca, hasil ditandai parsial; aplikasi tidak mengarang fallback. Setiap sumber memakai field dalam satu request per halaman, tanpa request metadata per item.

Field posts: `id,from,is_published,created_time,scheduled_publish_time,message,full_picture,permalink_url,status_type`. Field Reels: `id,from,published,post_id,scheduled_publish_time,description,picture,permalink_url`. Field Story: `id,post_id,media_id,status`; filter status PUBLISHED dan ARCHIVED. Hanya kandidat dengan owner `from.id` sesuai Page dan flag published eksplisit true yang dimuat. Jadwal masa depan ditolak. Kandidat tanpa bukti owner/published dilewati dan dicatat sebagai keterbatasan.

Identitas asli dan provenance sumber disimpan. Post dan Reel digabung jika `Reel.post_id` persis sama dengan ID Post yang dimuat; bila API memberi relasi object video yang terbukti, ID tersebut juga dapat digunakan. Tidak ada deduplikasi berdasarkan teks/tanggal, atau pembuatan PageID_PostID sintetis untuk penghapusan. Tanpa relasi, kedua kandidat dipertahankan dan ketidakpastian identitas silang sumber dilaporkan. `key` hanya kunci pilihan UI sementara, **bukan ID untuk DELETE**.

Story dikeluarkan melalui pencocokan ID Post/media yang dikembalikan sumber Story. Kata/field `story` pada Post tidak dijadikan klasifikasi Facebook Story. Record Story dengan referensi Post/media yang hilang menyebabkan status parsial dan menonaktifkan checkbox. Histori/retensi/kelengkapan Story yang disediakan Meta belum dapat dibuktikan meskipun pagination indeks berakhir; `story_exclusion=matched_ids_only` menyatakan cakupan terbatas ini.

## Urutan dan makna limit

SDK membuktikan GET dan field, **tidak menjamin urutan kronologis pagination**. Karena itu aplikasi tidak mengklaim N terbaru seluruh Fanpage. Published posts dan Reels diambil bergantian, tiap sumber sampai cukup kandidat eligible untuk limit pengguna atau sumber habis. Request berikutnya menyesuaikan sisa kuota; satu respons halaman dapat memberikan sedikit kandidat ekstra. Aplikasi tidak memuat seluruh Fanpage dahulu lalu memotongnya, dan tidak berhenti setelah N dari sumber pertama tanpa mengunjungi Reels.

Kandidat yang berhasil diperoleh digabung/dideduplikasi, diurutkan secara global di antara kandidat tersebut, kemudian dibatasi maksimal N. Acuan tanggal Post adalah `created_time` pada Post terbit; [SDK Post](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/post.py) mengonfirmasi field, tetapi deskripsi semantik publikasi lengkap pada dokumentasi primer belum dapat dibaca. Pengguna perlu membandingkan tanggal ini dengan tab Postingan Facebook. Jangan menafsirkan sort kandidat sebagai bukti kronologi seluruh sumber.

**created_time video/Reel tidak dipakai sebagai publikasi** karena waktu upload/pembuatan dapat berbeda. Reel hanya mendapat tanggal dari relasi Post terbit yang berhasil dimuat. Tidak ada pengganti dari updated_time, waktu server sekarang, atau jadwal publikasi. Tanggal hilang/invalid/naive ditampilkan sebagai **Tanggal tidak tersedia**, dikelompokkan setelah kandidat bertanggal. Tie-breaker stabil berbasis sumber+ID asli. Waktu absolut UTC dipakai untuk sort lalu dikonversi ke WIB (+07:00, Asia/Jakarta). Jenis Foto/Video memakai status API yang dikenal; jenis yang tidak terbukti lebih spesifik tetap Postingan.

Jika N kandidat bertanggal sudah mengisi batas, kandidat tanpa tanggal mungkin tidak ditampilkan; ini bukan bukti Reel tersebut lebih lama. Naikkan limit atau audit sumber yang belum tercakup secara terpisah bila diperlukan. Keterbatasan identitas/tanggal/cakupan ditampilkan, bukan disembunyikan sebagai keberhasilan lengkap.

| Status | Arti |
| --- | --- |
| `limit_reached` | Batas tampilan tercapai pada kandidat yang dimuat; bukan jaminan N terbaru global |
| `source_exhausted` | Listing konten yang dipakai sudah tidak punya next sebelum batas tercapai; bukan seluruh postingan Facebook pasti tersedia |
| `empty` | Tidak ada kandidat eligible; dibedakan dari error token/izin |
| `partial` | Sumber, pagination, deadline, atau pengecualian Story terputus/tidak terverifikasi; jumlah dan error aman ditampilkan, pilihan massal nonaktif |

`retrieval_complete` hanya berarti pengambilan **berdasarkan policy bounded lokal** selesai, bukan cakupan Facebook lengkap. Semua hasil tetap memiliki `coverage_complete=false`, `latest_guaranteed=false`, `safe_for_delete=false`. Tidak ada konten yang dinyatakan aman dihapus.

## Memori, keamanan, dan batas tahap 3

POST `/api/posts/load` menerima hanya `{"page_id":"…","limit":20}`. Page ID harus ada pada sesi pemilik dan page token tersedia; frontend tidak mengirim token. Origin/cookie/no-store/AsyncClient/retry/cooldown/ukuran respons dari tahap 2 tetap digunakan. GET `/api/posts?page_id=…` membaca hasil terakhir milik sesi dari memori, tanpa request Meta; Page yang tidak dimiliki ditolak. UI mengganti halaman secara lokal tanpa memanggil endpoint ini.

Satu load aktif per browser. Pekerjaan connect dan load berbagi maksimal pekerjaan Graph global serta limiter browser/global yang sama. Default tiga pekerjaan total per browser per menit, dua pekerjaan global bersamaan. Reconnect saat load aktif ditolak; disconnect membatalkan load. TTL sesi tidak diperpanjang oleh load. Deadline dibatasi sisa TTL sesi; bila sesi habis, hasil tidak disimpan dan alasan ditampilkan. Pergantian Page menggunakan guard generasi frontend untuk menolak respons lama.

| Konfigurasi tambahan | Default / perilaku |
| --- | --- |
| `POSTS_TIMEOUT` | 60 detik untuk seluruh Story/posts/Reels/retry, juga dibatasi sisa sesi |
| `POSTS_MAX_PAGES` | 50 halaman per sumber, hanya next yang sah melanjutkan; cursor/halaman berulang ditolak |
| `STORY_MAX_ITEMS` | 5000 record Story untuk indeks; berhenti parsial jika batas tercapai sebelum sumber habis |
| `AUDIT_TTL` | 300 detik; hasil juga berakhir saat sesi habis/restart/disconnect |
| `MAX_AUDIT_ENTRIES` | 20 hasil terakhir per sesi; tanpa eviksi diam-diam sesi lain |
| `MAX_AUDIT_ITEMS` | 20000 konten total dalam cache |
| `MAX_AUDIT_BYTES` | 64 MiB total budget serialisasi; reservasi konservatif 8192 byte/item + overhead sebelum request Meta |

Cache satu hasil terakhir per sesi. Load baru menggantikan audit lama browser itu; hasil parsial dapat disimpan dengan label parsial. Kapasitas entry/row/byte dicadangkan sebelum Graph; bila penuh, request ditolak tanpa mengambil data. Angka byte adalah budget serialisasi dan reservasi, bukan ukuran pasti heap Python. Memori sementara kandidat juga dibatasi kuota sumber, halaman, dan ukuran response. Tidak ada database/file hasil. Frontend membersihkan audit ketika TTL hasil habis.

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

Load Posts aktif setelah sesi dan Fanpage tersedia. Hapus Terpilih/Berhenti tetap nonaktif. Kemunculan Fanpage tidak membuktikan izin menghapus. Page tanpa page token tetap muncul pada dropdown tetapi Load Posts ditolak dengan pesan yang jelas.

## Endpoint dan keamanan

| Endpoint | Perilaku |
| --- | --- |
| `GET /` | HTML tanpa token atau sesi tertanam |
| `GET /health` | `{"status":"ok"}` |
| `GET /api/session` | Status dari memori; bootstrap identitas browser bila diperlukan |
| `POST /api/connect` | Body JSON `{"token":"…"}`; mengganti sesi setelah daftar lengkap |
| `POST /api/disconnect` | Menghapus sesi dan cookie sesi, tanpa request Meta |
| `POST /api/posts/load` | Memuat kandidat audit dengan page token pemilik dari backend |
| `GET /api/posts?page_id=…` | Hasil audit terakhir dari memori pemilik saja |

API membutuhkan `X-Fanpage-Client: 1`. POST juga membutuhkan `Origin` persis sama dengan `APP_ORIGIN`. `Sec-Fetch-Site` lintas origin ditolak; host hanya `127.0.0.1`, klien hanya loopback, tanpa CORS wildcard. ID sesi hanya dibaca dari cookie, bukan query/body. Cookie sesi dan browser berisi ID acak opaque 256 bit, `HttpOnly`, `SameSite=Strict`, `Path=/`, tanpa `Domain`. Cookie sesi harus cocok dengan identitas browser pemiliknya.

Token user dan Page hanya disimpan dalam memori backend. Cookie tidak berisi token. Frontend tidak memakai localStorage/sessionStorage dan membersihkan input saat request dikirim maupun selesai. Respons koneksi/status Fanpage hanya berisi nama dan ID Page; respons audit berisi metadata konten yang dinormalisasi. Semua teks dirender dengan `textContent` dan rahasia yang terefleksi disamarkan. Respons terkait sesi memakai `Cache-Control: no-store`; CSP membatasi script/koneksi frontend ke origin sendiri.

Tidak ada respons Meta, pesan Meta, Authorization, body token, URL pagination, atau exception mentah yang dikirim ke frontend/dicatat aplikasi. Diagnostik hanya berupa kode lokal, pesan tetap bahasa Indonesia, dan bila relevan `retry_after`/`cause_code` yang dipilih aplikasi.

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
.\.venv\Scripts\python.exe -m py_compile app.py config.py errors.py sessions.py graph_api.py content.py content_cache.py
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
- `content.py`, `content_cache.py`: listing/normalisasi/dedup/sort kandidat dan cache audit bounded.
- `templates/index.html`, `static/style.css`, `static/app.js`: halaman responsif.
- `tests/test_app.py`, `tests/test_content.py`, `tests/frontend.test.cjs`: regresi koneksi, konten HTTP mock, dan interaksi frontend.
- `docs/stage2-plan.md`: rencana implementasi dan review tahap ini.
- `docs/stage3-plan.md`: bukti sumber dan rencana tahap audit.
- `requirements.txt`, `.gitignore`: versi paket dan pengecualian rahasia/cache/runtime.
