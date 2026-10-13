"""Error tetap: tidak pernah menyertakan input, exception, atau pesan Meta."""

MESSAGES = {
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
    "permission_denied": "Akses atau izin Facebook ditolak. Periksa izin aplikasi dan akses Fanpage.",
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
