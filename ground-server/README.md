# USV Supervisory Ground Server

Ground server untuk sistem Supervisory USV. Satu codebase digunakan untuk Raspberry Pi/Linux maupun laptop Windows.

Fungsi utama:

- menerima telemetry dan mission melalui MQTT;
- menyimpan record unik ke SQLite berdasarkan `vehicle_id + session_id + seq_id`;
- mengirim application-level ACK setelah transaksi database selesai;
- menyimpan mission plan dan active waypoint;
- menyediakan dashboard monitoring read-only;
- menangani penerimaan berulang tanpa membuat duplikasi record.

## Platform

### Raspberry Pi / Linux

Gunakan `docs/INSTALL_RASPBERRY_PI_LINUX.md`, script pada `scripts/linux/`, dan template service pada `systemd/`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/.env.example config/.env
python -m server.app
```

### Windows

Panduan lengkap tersedia di `docs/INSTALL_LAPTOP_WINDOWS.md`.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\setup_windows_server.ps1
.\scripts\windows\run_all.ps1
```

Dashboard lokal:

```text
http://127.0.0.1:5000
```

## Konfigurasi

Jangan commit `config/.env`. Salin file contoh lalu isi kredensial dan alamat jaringan milik deployment masing-masing.

```bash
cp config/.env.example config/.env
```

Untuk Windows tersedia juga `config/.env.windows.example`.

## Database

Database runtime berada di:

```text
data/database/usv_server.sqlite3
```

Database dan backup runtime sengaja tidak disertakan dalam repository.

## Pengujian

```bash
python -m pytest -q tests
```

## Catatan

Jalankan satu backend server aktif untuk deployment yang sama agar alur ACK dan penyimpanan tetap mudah ditelusuri.
