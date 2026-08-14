# Windows Release Notes

Basis kode: `usv_server_final(4).zip` dari Raspberry Pi Lab.

Perubahan platform:

- source, dashboard, skema SQLite, ACK, mission, dan API dipertahankan;
- `.venv` Linux/ARM dihapus karena tidak dapat digunakan pada Windows;
- `.git`, cache, log, dan kredensial aktual tidak disertakan;
- penanganan terminal UTF-8 Windows ditambahkan;
- `SIGBREAK` Windows didukung;
- dashboard HTTP menggunakan reusable address agar restart lebih cepat;
- installer, broker launcher, firewall, backup, migrasi, dan health-check PowerShell ditambahkan;
- konfigurasi Mosquitto Windows dibuat dengan anonymous access dinonaktifkan;
- recent track default tetap 500 record;
- 52 unit test asli dipertahankan dan lulus pada source hasil adaptasi.
