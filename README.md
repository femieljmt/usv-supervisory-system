# USV Supervisory System

Repository ini berisi program yang saya kembangkan untuk Tugas Akhir:

**Perancangan Mekanisme Supervisory Operasi Sistem USV untuk Mendukung Keberlanjutan Pelaksanaan Misi dan Menjaga Kontinuitas Informasi pada Kondisi Kehilangan Komunikasi Telemetri.**

Masalah yang saya tangani adalah terputusnya komunikasi telemetri antara USV dan ground station. Saat koneksi hilang, Pixhawk harus tetap menjalankan misi, sementara data operasi yang belum terkirim tidak boleh ikut hilang. Karena itu, saya memisahkan fungsi navigasi dari fungsi pencatatan dan pengiriman data.

Pixhawk 6C tetap menangani navigasi berbasis waypoint. Raspberry Pi onboard membaca MAVLink, mencatat data secara lokal, dan menyimpan data yang belum dikonfirmasi server ke SQLite. Setelah koneksi kembali tersedia, data tersebut dikirim ulang melalui MQTT. Ground server menyimpan data dan mengirim ACK setelah transaksi database berhasil.

## Cara kerja singkat

```text
Pixhawk 6C
    │ MAVLink
    ▼
Raspberry Pi onboard
    │ pencatatan lokal dan persistent outbox
    │ MQTT melalui jaringan
    ▼
Ground server
    │ SQLite dan application-level ACK
    ▼
Dashboard pemantauan
```

Jalur Mission Planner tetap terpisah. Mekanisme supervisory hanya menjaga kontinuitas informasi operasi dan tidak mengambil alih kendali gerak, perencanaan lintasan, atau navigasi Pixhawk.

Empat kondisi yang dipakai pada program onboard adalah:

- `NORMAL`: MAVLink dan internet tersedia, serta tidak ada proses sinkronisasi;
- `GCS_LOST`: MAVLink masih tersedia, tetapi koneksi internet onboard terputus;
- `RECOVERY`: koneksi sudah pulih dan data tertunda sedang dikirim;
- `PIXHAWK_LOST`: heartbeat atau data MAVLink tidak diterima dalam batas waktu.

Rincian aturan transisi dapat dilihat pada [state transition](onboard/docs/state_transition.md).

## Isi repository

```text
.
├── onboard/        # program pada Raspberry Pi di USV
└── ground-server/  # broker, backend, database, dan dashboard
```

Folder [`onboard/`](onboard/) berisi pembacaan MAVLink, pencatatan sesi, persistent outbox, pengiriman MQTT, pemeriksaan ACK, dan sinkronisasi data.

Folder [`ground-server/`](ground-server/) berisi backend penerima data, penyimpanan SQLite, penerbit ACK, dashboard, serta script untuk Linux dan Windows.

## Menjalankan sistem

Petunjuk pemasangan dipisahkan berdasarkan perangkat supaya README utama tidak terlalu panjang:

- [menjalankan program onboard](onboard/README.md);
- [menjalankan ground server](ground-server/README.md);
- [instalasi ground server pada Raspberry Pi/Linux](ground-server/docs/INSTALL_RASPBERRY_PI_LINUX.md);
- [instalasi ground server pada laptop Windows](ground-server/docs/INSTALL_LAPTOP_WINDOWS.md).

Konfigurasi dimulai dengan menyalin file `.env.example` menjadi `.env` pada komponen yang akan dijalankan. Nilai MQTT, alamat jaringan, dan identitas USV kemudian disesuaikan dengan perangkat yang digunakan.

## Data yang tidak disertakan

Repository ini sengaja tidak memuat `.env`, password MQTT, database hasil operasi, log, virtual environment, dan alamat Tailscale yang digunakan saat pengujian. Data penelitian dan database historis saya simpan terpisah agar source code tetap ringan dan informasi deployment tidak terbuka.

## Pengujian

Masing-masing komponen memiliki test:

```bash
cd onboard
python -m pytest -q
```

```bash
cd ground-server
python -m pytest -q tests
```

## Penulis

Femiel Jubil M. Tambunan  
Program Studi Sarjana Teknik Elektro, Institut Teknologi Del  
2026
