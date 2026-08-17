# Memasang Ground Server pada Laptop Windows

Panduan ini saya gunakan ketika laptop Windows berperan sebagai ground server pengganti Raspberry Pi Lab. Laptop menjalankan broker Mosquitto, backend, database SQLite, dan dashboard.

## Sebelum mulai

Siapkan Python 3.11 atau versi lebih baru, Eclipse Mosquitto untuk Windows, dan Tailscale. Laptop dan Raspberry Pi onboard harus terhubung ke tailnet yang sama.

Letakkan proyek pada path yang sederhana, misalnya:

```text
C:\USV\usv_server
```

## Menjalankan setup

Buka PowerShell sebagai Administrator, kemudian masuk ke folder `ground-server`:

```powershell
cd C:\USV\usv_server
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\setup_windows_server.ps1
```

Masukkan username dan password MQTT yang sama dengan konfigurasi onboard. Hak Administrator diperlukan untuk pengaturan firewall dan service Mosquitto.

Agar laptop tidak masuk ke mode sleep saat pengujian:

```powershell
.\scripts\windows\disable_sleep_on_ac.ps1
```

## Menjalankan server

```powershell
.\scripts\windows\run_all.ps1
```

Script membuka terminal untuk broker Mosquitto serta terminal untuk backend dan dashboard. Dashboard dapat dibuka di:

```text
http://127.0.0.1:5000
```

## Mengarahkan onboard ke laptop

Cari IP Tailscale laptop:

```powershell
tailscale ip -4
```

Pada Raspberry Pi onboard, buat salinan konfigurasi lalu buka file `.env`:

```bash
cd ~/usv_supervisory_final
cp config/.env config/.env.before_laptop_server
nano config/.env
```

Ubah hanya alamat broker:

```ini
MQTT_HOST=<IP_TAILSCALE_LAPTOP>
```

Biarkan username, password, topic, QoS, dan vehicle ID sesuai konfigurasi deployment.

Uji koneksi dari onboard:

```bash
ping -c 3 <IP_TAILSCALE_LAPTOP>

timeout 3 bash -c '</dev/tcp/<IP_TAILSCALE_LAPTOP>/1883' \
  && echo 'MQTT PORT OPEN' \
  || echo 'MQTT PORT FAILED'
```

## Pemeriksaan setelah server berjalan

Pada laptop:

```powershell
.\scripts\windows\check_server.ps1
```

Saat sistem normal, dashboard seharusnya menunjukkan koneksi Pixhawk, internet, dan MQTT aktif. Buffer akan kembali ke nol setelah seluruh record memperoleh ACK.

Urutan menyalakan sistem yang saya gunakan:

```text
Mosquitto di laptop
→ backend ground server
→ MAVProxy di Raspberry Pi USV
→ program onboard
→ periksa dashboard
```

Sebelum mematikan sistem, tunggu sampai buffer nol. Setelah itu hentikan program onboard, MAVProxy, backend, lalu Mosquitto. Jangan menjalankan ground server laptop dan Raspberry Pi Lab secara bersamaan untuk deployment yang sama.
