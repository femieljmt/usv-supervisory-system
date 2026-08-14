# Cleanup Report

## Hasil konsolidasi

Tiga paket sumber disusun menjadi dua komponen utama:

- `onboard/` untuk Raspberry Pi Onboard pada USV;
- `ground-server/` untuk backend, database, ACK, dan dashboard.

Raspberry Pi Server dan Laptop Server digabung menjadi satu `ground-server` karena basis source-nya hampir sama. Dari 41 file yang terdapat pada kedua paket, 36 file identik dan 5 file berbeda. Perbedaan utama berada pada konfigurasi/entry point cross-platform, dashboard HTTP server, README, dan dukungan Windows.

## Pembersihan untuk GitHub

Yang dikeluarkan atau diperbaiki:

- database historis `usv_server.sqlite3` (~138 MiB);
- backup database historis;
- file `.env` dan credential runtime;
- runtime log dan cache Python;
- alamat Tailscale deployment asli;
- absolute path user dari template service;
- empty deployment stubs pada paket Onboard;
- duplikasi source Raspberry Pi Server dan Laptop Server.

Yang ditambahkan/diperbaiki:

- `.gitignore` pada root dan Onboard;
- struktur runtime placeholder dengan `.gitkeep`;
- satu ground-server cross-platform;
- `scripts/linux/` dan `scripts/windows/`;
- template Mosquitto Linux;
- template systemd generik;
- README utama dan README per komponen;
- panduan instalasi Raspberry Pi/Linux dan Windows;
- helper script yang menentukan project directory relatif terhadap lokasi script.

## Validasi

- seluruh file Python lolos pemeriksaan sintaks dengan `compileall`;
- seluruh shell script lolos `bash -n`;
- tidak ditemukan database runtime, `.env`, `.log`, `.tlog`, atau alamat deployment asli di paket final;
- source asli pada tiga ZIP unggahan tidak diubah.

Full unit test belum dijalankan pada lingkungan validasi karena dependency runtime seperti `paho-mqtt` dan `pymavlink` tidak tersedia di environment ini. Setelah dependency dipasang dari `requirements.txt`, jalankan `pytest -q` pada masing-masing komponen.
