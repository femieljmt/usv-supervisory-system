# Ground Server USV

Ground server menerima data dari Raspberry Pi onboard melalui MQTT. Setelah payload lolos validasi, server menyimpannya ke SQLite dan baru kemudian mengirim ACK. Dashboard membaca database yang sama untuk menampilkan kondisi operasi USV.

Saya menyiapkan bagian ini agar dapat dijalankan pada Raspberry Pi/Linux maupun laptop Windows tanpa mengubah alur data utama.

## Data yang ditangani

Satu record dikenali dari gabungan `vehicle_id`, `session_id`, dan `seq_id`. Jika record yang sama dikirim kembali karena ACK sebelumnya tidak diterima onboard, server mengenalinya sebagai duplikat dan tidak membuat baris telemetry baru.

Selain telemetry, server juga menyimpan informasi misi dan waypoint aktif. Dashboard bersifat read-only: dashboard tidak menentukan state supervisory dan tidak mengubah isi buffer onboard.

## Menjalankan pada Raspberry Pi atau Linux

Jalankan dari folder `ground-server`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/.env.example config/.env
python -m server.app
```

Panduan pemasangan broker, service, dan pemeriksaan sistem tersedia pada [INSTALL_RASPBERRY_PI_LINUX.md](docs/INSTALL_RASPBERRY_PI_LINUX.md).

## Menjalankan pada Windows

Buka PowerShell dari folder `ground-server`:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\setup_windows_server.ps1
.\scripts\windows\run_all.ps1
```

Panduan lengkapnya tersedia pada [INSTALL_LAPTOP_WINDOWS.md](docs/INSTALL_LAPTOP_WINDOWS.md).

Setelah backend berjalan, dashboard lokal dapat dibuka melalui:

```text
http://127.0.0.1:5000
```

## Konfigurasi

Salin `config/.env.example` menjadi `config/.env`, kemudian isi alamat broker dan kredensial yang sama dengan konfigurasi onboard. Untuk Windows juga tersedia `config/.env.windows.example`.

Jangan commit file `.env`. Repository hanya menyediakan contoh struktur konfigurasinya.

## Database dan pengujian

Database runtime disimpan pada:

```text
data/database/usv_server.sqlite3
```

Database operasi dan file backup tidak dimasukkan ke repository.

Untuk menjalankan test:

```bash
python -m pytest -q tests
```

Dalam satu deployment, gunakan satu backend aktif agar urutan penyimpanan dan ACK mudah diperiksa.
