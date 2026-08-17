# Catatan Adaptasi Ground Server ke Windows

Versi Windows ini saya adaptasi dari source ground server yang sebelumnya dijalankan pada Raspberry Pi Lab. Alur telemetry, skema SQLite, mekanisme ACK, penyimpanan misi, API, dan dashboard tetap dipertahankan.

Penyesuaian yang dilakukan untuk Windows:

- virtual environment Linux/ARM tidak dibawa karena tidak kompatibel;
- penanganan terminal UTF-8 dan sinyal `SIGBREAK` ditambahkan;
- alamat HTTP dashboard dibuat reusable agar proses restart lebih cepat;
- tersedia script PowerShell untuk setup, firewall, menjalankan broker, backup, migrasi, dan pemeriksaan server;
- konfigurasi Mosquitto menonaktifkan anonymous access;
- batas awal recent track tetap 500 record.

File `.git`, cache, log runtime, database pengujian, dan kredensial asli tidak disertakan dalam paket. Pada saat adaptasi dibuat, 52 unit test dari source awal tetap dipertahankan dan dijalankan pada versi hasil penyesuaian.
