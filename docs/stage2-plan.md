# Rencana tahap 2

Permintaan tahap 2 pengguna adalah spesifikasi: koneksi user access token dan daftar Fanpage saja; tanpa database, posts, DELETE, perubahan .venv, commit, atau push. Implementasi langsung di proyek terbuka, satu proses localhost.

1. Tulis pengujian unittest untuk kontrak HTTP terlebih dahulu; gunakan httpx.MockTransport sebagai batas jaringan eksternal. Jalankan dan pastikan route koneksi yang belum ada gagal.
2. Buat `config.py`, `errors.py`, `sessions.py`, dan `graph_api.py`. Konfigurasi tervalidasi; sesi dan identitas browser acak dengan cookie terpisah; limiter bounded; cooldown token memakai HMAC per proses; transaksi penggantian sesi setelah pagination lengkap. AsyncClient tunggal, GET endpoint tetap, header Bearer, tanpa redirect/proxy lingkungan.
3. Integrasikan lifespan dan route `/api/session`, `/api/connect`, `/api/disconnect` dalam `app.py`. Body dibatasi sebelum parsing, origin eksplisit, cookie HttpOnly/Strict, no-store, error tetap tanpa input/response Meta.
4. Tambahkan JavaScript: bootstrap status lokal sebelum Hubungkan aktif, hanya JSON body, textContent untuk data, loading dan pencegahan klik ganda, pemulihan sesi dan putus koneksi. Operasi konten tetap nonaktif.
5. Jalankan suite mock untuk pagination, error/retry/cooldown, isolasi/kedaluwarsa/kapasitas/concurrency, CSRF, host/redirect dan kebocoran token. Tambahkan pengujian untuk celah yang ditemukan sebelum memperbaiki.
6. Perbarui README dengan nilai batas lokal, sumber resmi dan ketidakpastian, lalu verifikasi sintaks, suite, dan server Uvicorn nyata di 127.0.0.1. Tidak melakukan verifikasi Facebook dengan token asli.

Review: missing cookie tidak boleh membuat pekerjaan Meta; kapasitas dicadangkan sebelum await; putus saat pekerjaan berlangsung tidak boleh membangkitkan sesi kembali; after tanpa next berhenti; next tidak pernah dipakai langsung sebagai URL; token dalam pesan/name/error upstream tidak boleh terefleksi; timeout meliputi retry/pagination; rate-limit tidak memicu retry langsung; status refresh tidak menghubungi Meta.
