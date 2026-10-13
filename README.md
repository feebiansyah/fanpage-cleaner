# Fanpage Cleaner

Fondasi aplikasi web satu halaman berbahasa Indonesia menggunakan FastAPI dan Jinja2. Tahap ini tidak memakai database, tidak menghubungkan Facebook Graph API, dan tidak menyediakan penghapusan atau data postingan contoh.

Semua tombol operasi, pilihan Fanpage, dan checkbox Pilih Semua nonaktif. Halaman menampilkan pesan: "Koneksi Facebook belum tersedia pada tahap ini." Input token menggunakan tipe password; nilainya tidak dikirim atau disimpan. JavaScript belum diperlukan.

## Menjalankan di Windows

Gunakan PowerShell dan Python 3.10.6 dari virtual environment yang sudah tersedia:

```powershell
Set-Location 'D:\FACEBOOK META ADS\fanpage-cleaner'
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Buka http://127.0.0.1:8000 di browser. Tekan `Ctrl+C` di terminal untuk menghentikan server. Untuk pengembangan, tambahkan `--reload` pada perintah di atas.

Paket utama yang sudah terpasang dicatat dengan versi tepat di `requirements.txt`. Jika diperlukan untuk menyiapkan lingkungan lain dengan paket yang sama:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Pemeriksaan

Periksa sintaks Python:

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py
```

Saat server berjalan, periksa endpoint di terminal PowerShell lain:

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/).StatusCode
Invoke-RestMethod http://127.0.0.1:8000/health
```

`GET /` menghasilkan HTML dengan status 200. `GET /health` menghasilkan JSON `{"status":"ok"}`. CSS tersedia di `/static/style.css`.

## Struktur

- `app.py`: aplikasi, route halaman utama dan health, serta penyajian file statis.
- `templates/index.html`: halaman koneksi, daftar konten kosong, dan progres.
- `static/style.css`: tampilan responsif.
- `requirements.txt`: versi paket utama dari `.venv`.
- `.gitignore`: pengecualian virtual environment, cache, konfigurasi rahasia, token, dan file runtime.
