# Tahap 3: Load Posts dan audit

Permintaan pengguna menjadi spesifikasi dan otorisasi implementasi langsung. Tanpa database, DELETE, commit/push, dependensi baru, atau perubahan .venv. Pertahankan pengujian kontrak tahap 2.

## Bukti dan keputusan sumber sebelum implementasi

SDK resmi Meta `facebook/facebook-python-business-sdk` yang dibaca 13 Oktober 2026:
- `adobjects/page.py`: GET published_posts (PagePost), GET video_reels (AdVideo), GET stories (Stories); bukan inferensi dari POST video_reels. GET videos juga tersedia, tetapi tidak diperlukan sebagai sumber tambahan karena published_posts mewakili postingan Page yang terbit, termasuk foto/video. Tidak ada feed pengunjung atau fallback.
- `pagepost.py`, `post.py`: created_time, is_published, from, permalink_url, full_picture, message, status_type, scheduled_publish_time; Post juga mendeklarasikan type/object_id untuk identifikasi jika dikembalikan. Jangan mengasumsikan seluruh versi selalu mengembalikan field opsional tersebut.
- `advideo.py`: published, from, post_id, description, permalink_url, picture. created_time video adalah waktu pembuatan dan tidak dijadikan publikasi. Tanggal Reel hanya dari relasi post_id yang persis dengan Post terbit yang dimuat.
- `stories.py`: id, post_id, media_id dan status PUBLISHED/ARCHIVED. Cocokkan identitas, bukan teks `story` pada Post.
- `api.py`: pagination memerlukan next, bukan after saja.

Dokumentasi utama Meta tidak dapat dibaca. SDK membuktikan metode dan nama field, tetapi bukan jaminan kronologi, histori Story lengkap, izin per akun, atau semua Reels tercakup. created_time Post dipakai sebagai acuan waktu Post terbit (tidak updated_time); semantik lengkap dokumentasi primer belum dapat diperiksa. Hasil harus menyatakan ini, tidak menjamin N terbaru seluruh Fanpage, dan tidak memiliki delete target.

## Langkah implementasi

1. Tes HTTP mock dahulu: batas 1/100, invalid input, sesi/owner/Page token, 30-item pages, dedup/Story, sumber Reels dan global sort kandidat, dates/ties/unknown, parsial/error/cooldown/expiry/memory/races/host/token.
2. Refactor transport GET tetap agar akun dan konten berbagi AsyncClient, retry, cooldown dan batas respons. Pertahankan kontrak accounts.
3. Modul `content.py`: normalisasi dan kandidat bounded. Scan Story PUBLISHED+ARCHIVED dengan batas halaman/identitas; fetch posts/Reels bergantian sampai masing-masing punya limit kandidat eligible atau habis. Tidak menganggap sumber terurut; hasil adalah limit terbaru di antara kandidat yang dimuat. Simpan provenance ID asli, relasi yang terbukti, date basis. Unknown dates terpisah; tie stabil. Known Story dikeluarkan. Kegagalan Story mematikan pilihan.
4. Modul `content_cache.py`: satu hasil terakhir per sesi, TTL ≤ TTL sesi, kapasitas row/byte/entry dengan reservasi sebelum request Meta. Gabungkan admission konten dengan limiter/global jobs lama tanpa mengubah perilaku connect. Disconnect membatalkan load, tidak resurrect hasil.
5. Route POST `/api/posts/load` menerima page_id dan limit; GET `/api/posts` membaca memori pemilik saja. Origin sama, body bounded, Page ownership, deadline sesi tidak diperpanjang, errors aman.
6. Frontend limit default100/range1..5000, Load active dengan sesi+Page; generation guard saat Page berubah/disconnect; tampil50 per halaman lokal, set pilihan seluruh hasil, status parsial/cakupan/order dan tanggalWIB, URL HTTPS tervalidasi. Hapus/Berhenti tetap disabled.
7. Jalankan seluruh tes tahap2+3, syntax, server localhost tanpa token asli, review kode, dokumentasi sumber/ketidakpastian/cara limit20. Tidak mengklaim verifikasi Facebook langsung.
