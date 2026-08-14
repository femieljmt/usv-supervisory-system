# Instalasi Ground Server pada Raspberry Pi / Linux

## 1. Persiapan

Pasang Python 3, Mosquitto, dan Tailscale sesuai sistem operasi Raspberry Pi/Linux yang digunakan.

## 2. Virtual environment

Dari direktori `ground-server`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Konfigurasi aplikasi

```bash
cp config/.env.example config/.env
```

Isi `MQTT_USERNAME`, `MQTT_PASSWORD`, dan parameter lain sesuai deployment. Jangan commit `config/.env`.

## 4. Konfigurasi Mosquitto

Gunakan file berikut sebagai referensi:

- `mosquitto/mosquitto.conf.example`
- `mosquitto/acl.example`

Buat password Mosquitto menggunakan `mosquitto_passwd`, lalu sesuaikan path pada konfigurasi broker.

## 5. Jalankan backend dan dashboard

```bash
bash scripts/linux/run_server.sh
```

Dashboard menggunakan port yang ditentukan pada `config/.env` (default `5000`).

## 6. systemd opsional

Template tersedia pada:

```text
systemd/usv-server.service
```

Template menggunakan direktori `/opt/usv-ground-server` dan user `usv`. Sesuaikan keduanya sebelum mengaktifkan service.

## 7. Pemeriksaan

```bash
bash scripts/linux/check_server.sh
```

## 8. Sinkronisasi waktu

Untuk deployment penelitian, sinkronisasi waktu sistem perlu aktif agar timestamp server dapat dibandingkan dengan timestamp telemetry.

```bash
timedatectl status
sudo timedatectl set-timezone Asia/Jakarta
sudo timedatectl set-ntp true
```
