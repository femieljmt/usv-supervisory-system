# USV Supervisory System

Implementasi kode untuk Tugas Akhir:

**Perancangan Mekanisme Supervisory Operasi Sistem USV untuk Mendukung Keberlanjutan Pelaksanaan Misi dan Menjaga Kontinuitas Informasi pada Kondisi Kehilangan Komunikasi Telemetri**

Program memisahkan fungsi navigasi dan pengelolaan informasi operasi. Pixhawk tetap menjalankan navigasi berbasis waypoint, sedangkan Raspberry Pi Onboard mengelola pencatatan lokal, persistent outbox, pemantauan kondisi komunikasi, pengiriman MQTT, ACK, dan sinkronisasi data. Ground Server menerima record, menyimpannya ke SQLite, menerbitkan ACK aplikasi, dan menyediakan dashboard monitoring.

## Struktur repository

```text
USV_Supervisory_GitHub_Ready/
├── onboard/        # Raspberry Pi pada USV
└── ground-server/  # Backend + database + dashboard
                    # mendukung Raspberry Pi/Linux dan Windows
```

### `onboard/`

Komponen utama:

- MAVLink reader dan mission reader;
- state machine `NORMAL`, `GCS_LOST`, `RECOVERY`, `PIXHAWK_LOST`;
- local logging;
- persistent outbox SQLite;
- MQTT publisher;
- application-level ACK;
- replay/synchronization.

### `ground-server/`

Komponen utama:

- Mosquitto MQTT broker configuration;
- backend penerima telemetry dan mission;
- penyimpanan SQLite;
- duplicate handling;
- application-level ACK;
- dashboard monitoring read-only;
- deployment scripts untuk Linux/Raspberry Pi dan Windows.

## Arsitektur ringkas

```text
Pixhawk 6C
   │ MAVLink
   ▼
Raspberry Pi Onboard
   ├── Local Log
   ├── Persistent Outbox
   ├── Supervisory State Machine
   └── MQTT
         │
         ▼
Ground Server
   ├── MQTT Broker
   ├── Backend
   ├── SQLite
   ├── Application ACK
   └── Dashboard
```

Jalur Mission Planner tetap terpisah dari jalur data Supervisory. Mekanisme Supervisory tidak mengambil alih fungsi navigasi Pixhawk.

## Keamanan repository

Repository tidak menyertakan:

- file `.env`;
- password MQTT;
- database runtime;
- database historis pengujian;
- log runtime;
- virtual environment;
- alamat Tailscale deployment asli.

Salin file `.env.example` pada masing-masing komponen dan isi sesuai perangkat yang digunakan.

## Menjalankan

Lihat:

- [`onboard/README.md`](onboard/README.md)
- [`ground-server/README.md`](ground-server/README.md)
- [`ground-server/docs/INSTALL_RASPBERRY_PI_LINUX.md`](ground-server/docs/INSTALL_RASPBERRY_PI_LINUX.md)
- [`ground-server/docs/INSTALL_LAPTOP_WINDOWS.md`](ground-server/docs/INSTALL_LAPTOP_WINDOWS.md)

## Data penelitian

Data hasil pengujian dan database historis sebaiknya disimpan sebagai dataset/arsip terpisah dari source code. Hal ini menjaga repository tetap ringan dan mencegah tercampurnya kode dengan data runtime.

## Author

Femiel Jubil M. Tambunan  
Bachelor of Electrical Engineering, Institut Teknologi Del  
2026

## License

Belum ada lisensi open-source yang ditetapkan. Hak penggunaan dan distribusi source code mengikuti keputusan pemilik repository.
