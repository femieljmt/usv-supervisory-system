# Mosquitto pada Windows

Script `setup_windows_server.ps1` menyiapkan `mosquitto-windows.conf`, file password, ACL, serta folder persistence dan log.

Broker dijalankan dengan:

```powershell
.\scripts\windows\run_mosquitto.ps1
```

Jika setup dijalankan sebagai Administrator, service Mosquitto bawaan dihentikan dan startup type-nya diubah menjadi `Manual`. Tujuannya agar tidak ada broker kedua yang memakai konfigurasi berbeda pada port 1883.

Kredensial yang dibuat saat setup harus sama dengan nilai MQTT pada `config/.env` ground server dan konfigurasi onboard.
