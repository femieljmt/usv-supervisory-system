# Memasang Ground Server pada Raspberry Pi atau Linux

Panduan ini digunakan jika broker MQTT, backend, database, dan dashboard dijalankan pada Raspberry Pi Lab atau komputer Linux.

## Persiapan aplikasi

Pasang Python 3, Mosquitto, dan Tailscale sesuai distribusi Linux yang digunakan. Dari folder `ground-server`, buat environment Python:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Buat konfigurasi lokal dari file contoh:

```bash
cp config/.env.example config/.env
```

Isi `MQTT_USERNAME`, `MQTT_PASSWORD`, alamat broker, dan parameter lain sesuai perangkat. File `config/.env` tidak boleh di-commit.

## Menyiapkan Mosquitto

Gunakan `mosquitto/mosquitto.conf.example` dan `mosquitto/acl.example` sebagai dasar konfigurasi. Buat password dengan `mosquitto_passwd`, lalu sesuaikan lokasi file password, ACL, log, dan persistence pada konfigurasi broker.

## Menjalankan backend

```bash
bash scripts/linux/run_server.sh
```

Dashboard menggunakan port dari `config/.env`; nilai bawaannya adalah `5000`.

Untuk memeriksa proses server dan database:

```bash
bash scripts/linux/check_server.sh
```

## Menjalankan sebagai service

Template systemd tersedia di `systemd/usv-server.service`. Template tersebut memakai direktori `/opt/usv-ground-server` dan user `usv`. Ubah keduanya agar sesuai dengan lokasi proyek dan user pada perangkat sebelum service diaktifkan.

## Waktu sistem

Timestamp onboard dan server dibandingkan selama pengujian, sehingga sinkronisasi waktu perlu aktif:

```bash
timedatectl status
sudo timedatectl set-timezone Asia/Jakarta
sudo timedatectl set-ntp true
```

Setelah instalasi selesai, pastikan broker menerima koneksi dari onboard, backend dapat menulis ke SQLite, dan dashboard dapat membaca data yang baru disimpan.
