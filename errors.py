"""Error tetap: tidak pernah menyertakan input, exception, atau pesan Meta."""

MESSAGES = {
    'post_scope_unsupported':'Hanya Page Post teks, status, tautan, atau foto yang didukung. Reel/video/Story tidak dapat dihapus; tidak ada DELETE dikirim.',
    'reel_delete_unsupported':'Penghapusan Reel/video tidak didukung. Tidak ada DELETE dikirim.',
    'audit_scope_incompatible':'Snapshot lama atau klasifikasi target tidak didukung. Muat ulang postingan dan siapkan konfirmasi baru; tidak ada DELETE dikirim.',
    'post_media_unsupported':'Video, Reel, Story, dan jenis tidak dikenal tidak didukung. Muat ulang postingan; tidak ada DELETE dikirim.',

    'diagnosis_budget':'Budget diagnosis lima request aktual sudah habis; bagian berikutnya tidak dipanggil.',
    'diagnosis_busy':'Diagnosis masih berjalan atau kapasitas diagnosis lokal penuh.',
    'diagnosis_not_failed_post':'Diagnosis ini hanya untuk satu hasil Page Post gagal milik sesi; target sukses/Reel tidak diproses kembali.',
    'diagnosis_unavailable':'Hasil gagal tidak tersedia pada sesi/proses ini. Setelah restart/reconnect, koneksi baru tidak membuktikan keadaan token lama. Jangan ulang penghapusan untuk diagnosis.',
    'reel_source_missing': 'Pilihan tidak memiliki ID asli dari listing video_reels. Percobaan Reel tidak memakai ID Post atau ID bebas.',
    'reel_identity_ambiguous': 'Identitas Reel/video tidak tunggal atau format ID asli tidak valid. Tidak ada ID alternatif yang dicoba.',
    'permission_circuit_capacity': 'Dispatch dihentikan sementara karena kapasitas catatan penolakan lokal penuh; bukan penolakan izin seluruh Page. Target belum dikirim perlu konfirmasi pekerjaan baru setelah jeda.',
    'permission_circuit_open': 'Pengiriman pada Page/token ini dijeda setelah penolakan berulang. Ini bukan bukti seluruh Page kehilangan izin. Target belum dikirim perlu konfirmasi pekerjaan baru setelah jeda.',
    "timeout_before_send": "Batas waktu habis sebelum pengiriman DELETE. Tidak ada request penghapusan dikirim.",
    "network_before_send": "Transport gagal sebelum pengiriman DELETE. Tidak ada request penghapusan dikirim.",
    "bulk_limit": "Batas lokal bulk maksimal 5000 pilihan per konfirmasi (atau batas konfigurasi yang lebih kecil). Ini bukan batas Meta.",
    "bulk_confirmation_not_found": "Konfirmasi bulk tidak tersedia atau bukan milik sesi browser ini.",
    "bulk_no_supported_targets": "Tidak ada Page Post yang didukung pada snapshot ini. Tidak ada penghapusan dikirim.",
    "bulk_job_not_found": "Pekerjaan penghapusan tidak tersedia pada sesi browser ini.",
    "bulk_dispatch_stopped": "Dispatch baru dihentikan. Target belum dikirim tidak dianggap terhapus.",
    "bulk_page_blocked": "Dispatch Page ini diblokir karena penolakan izin pada token terkait.",
    "bulk_token_blocked": "Dispatch scope token ini diblokir karena token invalid.",
    "bulk_target_deleted": "Target atau alias terkait telah diproses oleh operasi sukses lain; tidak dikirim ulang. Status ini bukan respons penghapusan baru.",
    "invalid_single_delete": "Pilih tepat satu key Post audit; ID atau token bebas tidak diterima.",
    "post_delete_unsupported": "Snapshot target berubah setelah preview. Periksa ulang target sebelum menyiapkan konfirmasi baru.",
    "page_post_missing": "Konten tidak memiliki ID Page Post asli dari published_posts. Penghapusan objek Reel/video melalui ID media aslinya belum terverifikasi; ID tersebut tidak dicoba sebagai pengganti ID Post.",
    "post_identity_ambiguous": "Identitas Page Post atau relasi Post/Reel ambigu. Tidak ada target yang ditebak atau ID yang diubah.",
    "identified_story": "Story teridentifikasi. Target ini tidak boleh dihapus.",
    "post_permalink_missing": "Permalink Facebook valid tidak tersedia. Pemeriksaan manual target tidak dapat dilakukan.",
    "confirmation_not_found": "Konfirmasi tidak tersedia, sudah kedaluwarsa, atau bukan milik sesi browser ini.",
    "confirmation_expired": "Konfirmasi kedaluwarsa. Periksa kembali target sebelum menyiapkan konfirmasi baru.",
    "confirmation_used": "Konfirmasi sudah digunakan. DELETE tidak dikirim ulang.",
    "target_busy": "Target yang sama sedang diproses oleh operasi lain. Tidak ada DELETE tambahan.",
    "delete_capacity": "Kapasitas konfirmasi atau catatan penghapusan lokal penuh. Tunggu hingga kedaluwarsa.",
    "invalid_selection": "Pilihan harus berisi Page ID dan key dari hasil audit milik sesi saja.",
    "audit_expired": "Hasil audit tidak tersedia atau kedaluwarsa. Muat ulang konten sebelum pemeriksaan atau penghapusan.",
    "dry_job_not_found": "Pekerjaan dry run tidak tersedia pada sesi browser ini.",
    "dry_capacity": "Kapasitas antrean/checkpoint penuh. Tunggu atau kurangi pilihan.",
    "checkpoint_failed": "Checkpoint tidak dapat disimpan. Dispatch dihentikan; tidak ada penghapusan.",
    "job_not_found": "Pekerjaan tidak tersedia pada sesi ini atau sudah kedaluwarsa.",
    "invalid_revision": "Nomor langkah pekerjaan tidak valid. Baca status lokal pekerjaan.",
    "invalid_limit": "Maksimal postingan harus bilangan bulat antara 1 dan 5000.",
    "invalid_page": "Page ID harus berupa teks ID Fanpage yang valid.",
    "invalid_load_body": "Body Load Posts harus JSON dengan page_id dan limit saja.",
    "page_not_owned": "Fanpage tidak tersedia pada sesi browser ini.",
    "page_token_missing": "Fanpage ini tidak memiliki page access token. Hubungkan kembali dengan izin yang sesuai.",
    "session_expired": "Sesi tidak tersedia atau sudah kedaluwarsa. Hubungkan kembali; hasil tidak disimpan.",
    "load_busy": "Satu pekerjaan Load Posts masih berjalan pada browser ini.",
    "load_cancelled": "Load Posts dibatalkan karena koneksi diputus. Hasil tidak disimpan.",
    "audit_capacity": "Kapasitas hasil audit dalam memori penuh. Kurangi maksimal postingan atau tunggu hasil kedaluwarsa.",
    "token_empty": "Token Facebook kosong. Masukkan user access token.",
    "token_invalid": "Token Facebook tidak valid atau sudah kedaluwarsa.",
    "permission_denied": "Facebook menolak akses atau operasi pada target ini. Lihat detail pesan; satu penolakan bukan bukti seluruh Fanpage kehilangan izin.",
    "network_error": "Gangguan jaringan saat menghubungi Facebook. Coba lagi nanti.",
    "timeout": "Waktu koneksi habis. Daftar Fanpage belum berhasil dimuat.",
    "upstream_error": "Facebook mengembalikan respons yang tidak dapat digunakan. Coba lagi nanti.",
    "rate_limited": "Batas penggunaan Facebook tercapai. Tunggu hingga cooldown selesai.",
    "cooldown": "Koneksi sedang dalam cooldown. Tunggu sebelum mencoba lagi.",
    "pagination_incomplete": "Pagination tidak selesai. Daftar parsial tidak disimpan; sesi lama dipertahankan jika masih valid.",
    "session_capacity": "Kapasitas sesi penuh. Putuskan sesi yang tidak digunakan atau tunggu sesi kedaluwarsa.",
    "job_capacity": "Pekerjaan koneksi penuh. Coba lagi setelah pekerjaan lain selesai.",
    "connection_busy": "Koneksi browser ini sedang berjalan. Tunggu hingga selesai.",
    "local_rate_limit": "Batas percobaan koneksi lokal tercapai. Tunggu sebelum mencoba lagi.",
    "limiter_capacity": "Kapasitas pembatas lokal penuh. Tunggu entri kedaluwarsa.",
    "browser_required": "Identitas browser belum tersedia atau kedaluwarsa. Muat ulang halaman.",
    "origin_denied": "Request lintas origin atau asal request yang tidak dipercaya ditolak.",
    "bad_request": "Body harus berupa JSON dengan satu field token berbentuk teks.",
    "body_too_large": "Ukuran input melampaui batas aplikasi.",
    "internal_error": "Koneksi tidak dapat diselesaikan. Sesi lama tetap dipertahankan jika masih valid.",
}


class AppError(Exception):
    def __init__(self, code: str, status: int = 400, retry_after: int | None = None):
        self.code = code
        self.status = status
        self.retry_after = retry_after
        super().__init__(MESSAGES[code])

    def payload(self):
        result = {"code": self.code, "message": MESSAGES[self.code]}
        if self.retry_after is not None:
            result["retry_after"] = self.retry_after
        return {"error": result}
