# Mosquitto Windows

`setup_windows_server.ps1` menghasilkan:

- `mosquitto-windows.conf`;
- `passwd`;
- `acl`;
- folder persistence dan log.

Broker dijalankan manual menggunakan:

```powershell
.\scripts\windows\run_mosquitto.ps1
```

Service Mosquitto bawaan dihentikan dan dibuat `Manual` oleh setup apabila PowerShell dijalankan sebagai Administrator. Hal ini mencegah broker bawaan memakai konfigurasi yang berbeda pada port 1883.
