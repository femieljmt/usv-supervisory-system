# Instalasi USV Server pada Laptop Windows

## 1. Persiapan

Pasang:

- Python 3.11 atau lebih baru;
- Eclipse Mosquitto Windows;
- Tailscale Windows.

Pastikan laptop dan Raspberry Pi onboard masuk ke tailnet yang sama.

## 2. Ekstrak paket

Gunakan lokasi tanpa karakter aneh, misalnya:

```text
C:\USV\usv_server_final
```

## 3. Setup otomatis

Buka PowerShell sebagai Administrator:

```powershell
cd C:\USV\usv_server_final
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\setup_windows_server.ps1
```

Username dan password MQTT yang dimasukkan harus sama dengan konfigurasi onboard.

## 4. Nonaktifkan sleep saat charger terhubung

```powershell
.\scripts\windows\disable_sleep_on_ac.ps1
```

## 5. Jalankan

```powershell
.\scripts\windows\run_all.ps1
```

Dua terminal akan digunakan:

- terminal broker Mosquitto;
- terminal backend dan dashboard.

Dashboard:

```text
http://127.0.0.1:5000
```

## 6. Arahkan onboard ke laptop

Pada laptop:

```powershell
tailscale ip -4
```

Di Raspberry Pi onboard:

```bash
cd ~/usv_supervisory_final
nano config/.env
```

Ubah:

```ini
MQTT_HOST=<IP_TAILSCALE_LAPTOP>
```

Username, password, topic, QoS, dan vehicle ID tidak diubah.

## 7. Uji port dari onboard

```bash
ping -c 3 <IP_TAILSCALE_LAPTOP>

timeout 3 bash -c '</dev/tcp/<IP_TAILSCALE_LAPTOP>/1883' \
  && echo 'MQTT PORT OPEN' \
  || echo 'MQTT PORT FAILED'
```

## 8. Validasi

Pada laptop:

```powershell
.\scripts\windows\check_server.ps1
```

Pada dashboard:

```text
LIVE
Pixhawk = OK
Internet = UP
MQTT = UP
Buffer = 0
Sync = IDLE
```

## 9. Urutan operasi

```text
Laptop: broker Mosquitto
→ Laptop: server.app
→ Pi USV: MAVProxy
→ Pi USV: onboard.app
→ dashboard LIVE
```

## 10. Urutan berhenti

```text
Tunggu buffer 0
→ hentikan onboard.app
→ hentikan MAVProxy
→ hentikan server.app
→ hentikan Mosquitto
```

Jangan menjalankan server Raspberry Pi Lab bersamaan dengan laptop server.
