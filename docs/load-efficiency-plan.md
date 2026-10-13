# Perbaikan Load Posts dari baseline a0cd9c8

Otorisasi pengguna: implementasi langsung, tanpa commit/push, database, perubahan thumbnail atau penghapusan. Maksimal1..5000, client bersama, limiter/cooldown/owner/origin/host/TTL tetap.

## Verifikasi sebelum implementasi

Dokumentasi utama Meta published_posts/video_reels/stories tetap429/tidak dapat diakses. SDK resmi Page mengonfirmasi GET published_posts -> PagePost, GET video_reels -> AdVideo dan GET stories -> Stories. SDK PagePost tidak mendeklarasikan object_id/type, sedangkan AdVideo mendeklarasikan post_id. Gunakan relasi persis Reel.post_id == Post.id yang memang diminta; hapus pembacaan object_id/type yang tidak diminta. Tidak menambah field/endpoint spekulatif.

SDK belum membuktikan pengecualian Facebook Story pada listing, cakupan seluruh Reels dari published_posts, tanggal publikasi video.created_time, atau jaminan urutan. Jangan memakai endpoint tunggal, menghapus perlindungan Story, mengisi tanggal video dari waktu upload, atau mengklaim N terbaru global tanpa bukti. Tanggal Post dari created_time tetap acuan API dengan keterbatasan semantik primer; Reel bertanggal hanya lewat relasi Post yang dimuat. Thumbnail tidak diubah.

## Rencana implementasi dan tes

1. Tes dahulu untuk bounded Story probe, keberhasilan retrieval terpisah dari cross-reference uncertainty, dedup sebelum stop, request counter termasuk retry, stop reasons per source, search lokal/active selection/refresh, race/cooldown/expiry/cache. Jalankan red lalu implementasi dan seluruh regresi.
2. Ambil satu batch dari dua listing yang cakupannya belum terbukti saling menggantikan. Sesudah ada kandidat, satu request Story dibatasi min(limit, page_size, story_max_items), tanpa menelusuri seluruh histori. Jika tidak ada kandidat, tidak meminta Story. Cocokkan semua ID yang tersedia; next/missing reference berarti exclusion uncertain, bukan pagination failure. Known Story tetap dibuang; checkbox tidak boleh aktif jika probe belum memadai.
3. Gabung/dedup ulang secara pure setelah tiap batch; lanjut round-robin hanya saat gabungan eligible unique belum N. Tiap request menyesuaikan sisa; no per-item request. Sort kandidat diketahui terbaru lalu unknown, tie identitas stabil. Tetap latest_guaranteed=false.
4. Source diagnostics: requests aktual termasuk retry, pages, received, skipped, eligible, status/stop_reason. Counter pada transport tepat sebelum request; jangan increment pada cooldown admission. Bedakan unique_limit/source_exhausted/page_limit/pagination_repeated/invalid_pagination/invalid_response/timeout/cooldown/permission/token/cancel. Errors per source dengan pesan tetap, tanpa cursor/URL/raw Meta.
5. Search input di atas dropdown: metadata sesi saja, case-insensitive nama/ID, hasil count/notfound, pin pilihan aktif bila tidak cocok. Hanya change nyata membersihkan audit. Status refresh mempertahankan Page aktif yang masih ada.
6. Update README: policy aktual, fakta/ketidakpastian, limit maksimum bukan target wajib; penyebab historis1743 belum diketahui. Ukur mock20 dan multi-page, syntax/server, review, tanpa Facebook asli.
