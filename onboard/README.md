# Program Onboard USV

Bagian ini dijalankan pada Raspberry Pi yang berada di USV. Saya menggunakannya untuk membaca data Pixhawk melalui MAVLink dan menjaga agar data operasi tetap tercatat saat komunikasi ke ground station terputus.

Program tidak mengendalikan gerak USV. Navigasi dan eksekusi waypoint tetap dilakukan oleh Pixhawk. Tugas program onboard adalah mencatat data, memantau koneksi, menyimpan record yang belum mendapat ACK, dan mengirimkannya kembali setelah koneksi pulih.

## Alur data

Setiap record ditulis ke log lokal dan persistent outbox sebelum dikirim melalui MQTT. Record baru dihapus dari outbox setelah ground server menyimpannya dan mengirim ACK yang valid. Cara ini dipakai agar keberhasilan publish MQTT saja tidak dianggap sebagai bukti bahwa data sudah tersimpan di server.

State yang digunakan adalah `NORMAL`, `GCS_LOST`, `RECOVERY`, dan `PIXHAWK_LOST`. Penjelasan lengkapnya tersedia di [dokumen transisi state](docs/state_transition.md).

## Persiapan

Jalankan perintah berikut dari folder `onboard`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/.env.example config/.env
```

Buka `config/.env`, lalu sesuaikan alamat broker MQTT, kredensial, dan parameter perangkat. Jangan memasukkan file `.env` ke Git karena file tersebut berisi konfigurasi deployment.

## Menjalankan program

Jika data MAVLink sudah diteruskan oleh MAVProxy ke UDP lokal:

```bash
source .venv/bin/activate
python -m onboard.app
```

Script berikut dapat dipakai untuk menjalankan MAVProxy dan program onboard bersama-sama:

```bash
MISSION_PLANNER_IP=<IP_TAILSCALE_GCS> \
PROJECT_DIR="$PWD" \
bash scripts/run_usv.sh
```

Konfigurasi bawaan script menggunakan:

- Pixhawk pada `/dev/ttyACM0` dengan baud rate 115200;
- keluaran MAVLink supervisory pada `127.0.0.1:14551`;
- Mission Planner pada alamat `MISSION_PLANNER_IP`, port 14550.

Periksa kembali nilai tersebut jika nama port serial atau susunan jaringan pada perangkat berbeda.

## Data yang dibuat saat program berjalan

Data runtime berada di:

```text
data/sessions/
data/logs/
data/database/onboard_outbox.sqlite3
```

Folder tersebut tidak berisi data pengujian di repository. Jangan menghapus database outbox hanya untuk mengosongkan buffer karena mungkin masih ada record yang belum mendapat ACK.

## Pengujian

```bash
python -m pytest -q
```

Kontrak payload dan ACK yang digunakan bersama ground server dapat dibaca pada [dokumen protokol](docs/protocol_contract.md).
