# Tahap 5: verifikasi penghapusan satu konten

Baseline awal: 2457bde17181785b88819d931fdd561e7652c0d4. Baseline perbaikan kelayakan Page Post: 27c6cdca879961c952e802f3947257eae8630007.
Tanggal pemeriksaan: 13 Oktober 2026.

Status terbaru: **jalur Hapus 1 Post biasa diimplementasikan dengan konfirmasi manual terikat satu target**. Baseline kode tidak diganti; perubahan dokumentasi lokal dari pemeriksaan sebelumnya dibaca dan diperbarui. Bukti pengguna di bawah mengatasi hambatan endpoint/kontrak sukses Page Post sebelumnya. Tidak ada perubahan eligible global atau aktivasi bulk/Reel.

## Bukti resmi yang diberikan pengguna

Sumber: [Pages API Posts](https://developers.facebook.com/documentation/pages-api/posts), dibuka langsung oleh pengguna di browser. Isi yang diberikan pengguna:

- Bagian Delete a Post: DELETE /{page_post_id}, contoh v26.0, page_post_id merupakan ID Post yang akan dihapus, respons sukses JSON {"success": true}.
- Bagian Before you start: Page access token, daftar permissions termasuk pages_manage_posts dan pages_read_engagement, serta tugas Page pengguna terkait. Daftar berlaku untuk keseluruhan panduan; **bukan bukti semuanya izin minimum DELETE**. Detail nama tugas tidak diberikan, sehingga tidak diinventarisasi/dianggap terbukti lewat /me/accounts.

Fetch ulang URL ini dari alat tetap gagal. Implementasi memakai bukti resmi yang isinya disampaikan pengguna, bukan mengklaim agen berhasil membaca bagian tersebut sendiri. SDK di bawah menguatkan node ID asli, tetapi keberadaan method tidak menggantikan kontrak dokumentasi.

Jalur memakai DELETE https://graph.facebook.com/v26.0/{ID_POST_ASLI} dan Page token pada Authorization backend. Sukses harus HTTP 2xx + JSON object + success identik boolean true + tidak ada field error. HTTP 200 saja, false, string, angka, body kosong/rusak tidak diterima. Semua timeout/network/hasil tanpa konfirmasi sukses menjadi uncertain tanpa retry otomatis. Error token/izin/rate limit dipisahkan. Tidak ada GET rutin sebelum/sesudah, maupun batch delete.

## Story: keputusan yang secara eksplisit diizinkan pengguna

Belum ada bukti bahwa published_posts/video_reels sepenuhnya mengecualikan Facebook Story. Scan satu halaman bukan bukti kelengkapan. ID yang teridentifikasi tetap dikeluarkan/ditolak; kind Story, provenance Reel/video/Story, dan identitas ambigu tidak mendapat jalur delete.

Untuk tepat satu **Page Post dengan satu ID asli dari provenance published_posts**, pengguna membuka permalink dan mengonfirmasi bahwa target adalah Page Post yang memang ingin dihapus, bukan Story. Subtype/label/status_type tidak membatasi jenis isi Post: teks, foto, link, atau video dapat dicoba melalui ID Post. Kandidat gabungan Reel hanya diterima bila tiap post_id Reel tepat sama dengan satu ID asli published_posts yang tersedia; Reel tanpa provenance Post tetap ditolak. Tidak ada ID media pengganti. Operasi menargetkan Page Post, **bukan jaminan menghapus objek foto/video/Reel secara terpisah**. Dialog memuat nama Page, jenis, teks, tanggal, link, dan ID Page Post asli. Pengakuan manual dikirim sebagai manual_target_confirmed=true, **bukan story_automatically_verified** (tetap false). safe_for_delete audit/bulk tetap false; tidak ada klaim pengecualian Story otomatis atau alasan membuka bulk.

Confirmation ID acak 256 bit, TTL maksimal 60 detik/sisa sesi, single-use, snapshot disimpan backend tanpa token. Endpoint execute tidak menerima ID/key/Page pengganti. Target dipastikan lagi dari audit pemilik yang masih valid setelah menunggu governor; request ganda/konflik canonical lock ditolak. Tidak ada perubahan .venv, database, queue infrastructure, commit/push, atau delete Facebook nyata dari terminal/test.

## Sumber SDK yang benar-benar dibaca pada pemeriksaan awal

| Sumber resmi | Temuan yang terbukti | Batas bukti |
| --- | --- | --- |
| [PagePost SDK Meta](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/pagepost.py#L104) | api_delete membangun DELETE pada node self['id'], endpoint '/', target AbstractCrudObject. Kandidat ID berasal dari node asli; tidak ada aturan membangun/mengubah Page_Post. | Tidak menjelaskan Page token, scopes/tugas Page, pengecualian Story, atau kontrak respons khusus Post. |
| [AdVideo SDK Meta](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/advideo.py#L390) | api_delete membangun DELETE node self['id']. | AdVideo mempunyai operasi ad-account; method yang sama tidak membuktikan aturan penghapusan semua Reel publik Page, token/izin, relasi Post/video, atau efek menghapus video versus postingannya. |
| [Page SDK Meta](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/page.py#L3952) | published_posts mengembalikan PagePost; video_reels mengembalikan AdVideo; stories mempunyai edge sendiri. Ada parameter show_expired pada published_posts. | Tidak ada deskripsi yang membuktikan published_posts/video_reels mengecualikan Facebook Story. show_expired tidak boleh ditafsirkan sebagai filter Story tanpa bukti semantik. |
| [Stories SDK Meta](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/stories.py) | Field id, post_id, media_id, media_type dan status tersedia. | Tidak membuktikan kelengkapan histori/retensi atau bahwa tidak ditemukannya suatu ID pada satu halaman berarti bukan Story. |
| [FacebookResponse SDK Meta](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/api.py#L76) | Bila respons mapping mempunyai error, is_success false; bila success tersedia, nilainya digunakan. SDK juga mempunyai fallback respons non-error/HTTP 200. | Bukti pengenalan field success secara generik, bukan bahwa DELETE PagePost/Reel versi aplikasi dijamin mengembalikan bentuk tertentu. Fallback HTTP 200 tidak memenuhi persyaratan pengguna. |
| [ObjectParser SDK Meta](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/objectparser.py#L67) | Parser objek menghapus field success dari data sebelum mengisi objek. | Objek SDK yang berhasil diparse bukan bukti sukses penghapusan. Pemeriksaan FacebookResponse terjadi sebelumnya; jangan menyimpulkan SDK sama sekali tidak memeriksa success. |

SDK tidak dipasang/dijalankan dan tidak melakukan operasi API. Pemeriksaan di atas hanya membaca source resmi.

## Riwayat dokumen primer yang tidak dapat dibaca oleh alat

- [Pages API Posts / Delete a Post](https://developers.facebook.com/docs/pages-api/posts/#delete-a-post): HTTP 429 / gagal fetch; variasi locale=en_US juga gagal.
- [Page Post reference](https://developers.facebook.com/docs/graph-api/reference/page-post/): HTTP 429.
- [Post reference](https://developers.facebook.com/docs/graph-api/reference/post/): HTTP 429.
- [Video reference](https://developers.facebook.com/docs/graph-api/reference/video/): gagal fetch.
- [Reels publishing guide](https://developers.facebook.com/docs/video-api/guides/reels-publishing/): HTTP 429.
- [published_posts reference](https://developers.facebook.com/docs/graph-api/reference/page/published_posts/): HTTP 429.
- [Page Stories API](https://developers.facebook.com/docs/page-stories-api/) dan [stories reference](https://developers.facebook.com/docs/graph-api/reference/page/stories/): gagal fetch.
- [pages_manage_posts](https://developers.facebook.com/docs/permissions/reference/pages_manage_posts/) dan [pages_read_engagement](https://developers.facebook.com/docs/permissions/reference/pages_read_engagement/): gagal fetch.
- [Koleksi Facebook workspace Meta di Postman](https://www.postman.com/meta/facebook/overview): gagal fetch; pencarian tidak menghasilkan request Delete Post resmi yang dapat diverifikasi.

Hasil pencarian pihak ketiga menyebut pages_manage_posts, pages_read_engagement, CREATE_CONTENT dan respons success=true. Hasil tersebut tidak digunakan sebagai bukti resmi atau kontrak implementasi. Repository pihak ketiga yang memuat nama Facebook/Meta, koleksi Postman api-evangelist, dan komentar pengguna pada issue SDK bukan dokumentasi endpoint resmi Meta.

## Hambatan pemeriksaan awal dan status terbarunya

1. **Post:** hambatan endpoint/ID/Page token dan kontrak sukses diatasi oleh isi dokumentasi resmi yang diberikan pengguna. Daftar izin panduan tidak dinyatakan minimum khusus DELETE; API tetap dapat menolak token/tugas/izin pengguna. Isi foto/video/link didukung melalui ID Page Post, bukan operasi objek media terpisah.
2. **Reel/video:** method AdVideo DELETE tersedia, tetapi cakupan Reel Page, ID tindakan yang tepat dan efek/izin operasi belum terbukti. Tidak boleh mengubah post_id agar terlihat cocok.
3. **Story:** sumber audit tetap matched_ids_only atau unverified. Tidak ada bukti sumber bebas Story atau filter yang memastikan setiap kandidat bukan Story. Pengguna secara eksplisit mengizinkan pemeriksaan/konfirmasi manual **satu Post**, tanpa membuka bulk atau mengubah status verifikasi otomatis.
4. **Sukses:** existence method/parser dan HTTP 200 tetap tidak cukup; bentuk success boolean true Page Post kini berasal dari isi dokumentasi pengguna. Mock menguji kontrak tersebut, bukan bukti delete Facebook nyata. Kontrak Reel/bulk belum dibuka.

## Hasil tahap ini

Fitur koneksi, Load Posts bertahap, search, dry run dan antrean dipertahankan. Hapus 1 Post tersedia untuk jenis Postingan berprovenance published_posts tunggal, ID asli, Page/token/audit pemilik valid, link aman, serta konfirmasi manual pengguna. Tombstone sukses menginvalidasi semua cache Page terkait dan menyaring snapshot job lama; gagal/uncertain tetap dipertahankan. Tidak ada infrastruktur baru.

Cara mencoba dan batas memori/pacing tercantum di README. Penghapusan objek Reel/video/foto terpisah, verifikasi otomatis Story, dan kelayakan bulk tetap membutuhkan bukti tambahan; konfirmasi manual tidak menggantikannya. Runtime antrean tetap DryRunExecutor, tidak memiliki transport delete.

Regresi perbaikan kelayakan: **136 tes backend lulus**, **38 tes DOM/fetch frontend lulus**. Test sebelumnya yang mengharuskan allowlist subtype diganti oleh bukti bahwa jenis isi tidak menentukan ID target. Perlindungan sesi/konfirmasi/DELETE tetap diuji. Riwayat tahap 5 awal: server .venv pada port uji 18765 pernah menghasilkan HTML 200 dan health ok, lalu dihentikan; perbaikan ini menggunakan ASGI/mock dan bukan pengujian penghapusan Facebook nyata.

Regresi menggunakan mock/fake dan token dummy; **tidak ada penghapusan Facebook nyata**. Pembatas mobile_status_update pada implementasi awal telah dicabut sesuai instruksi terbaru: backend memakai identitas/provenance, frontend hanya menentukan tepat satu pilihan dan menampilkan keputusan prepare. Load Posts/search/thumbnail tidak diubah. Perbaikan deadline monotonic GET Retry-After tahap sebelumnya tetap dipertahankan; tidak ada retry DELETE.
