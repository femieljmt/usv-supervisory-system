# USV Supervisory Onboard

Program Raspberry Pi Onboard yang membaca data MAVLink dari Pixhawk dan menjalankan mekanisme Supervisory untuk menjaga kontinuitas informasi operasi ketika komunikasi menuju Ground Station terganggu.

## Fungsi utama

- membaca telemetry dan informasi misi dari Pixhawk melalui MAVLink;
- membentuk local operation log;
- menyimpan record yang belum memperoleh ACK pada persistent outbox SQLite;
- memantau ketersediaan jaringan;
- mengirim telemetry melalui MQTT;
- menerima application-level ACK;
- melakukan replay dan sinkronisasi setelah koneksi pulih;
- mengelola empat state: `NORMAL`, `GCS_LOST`, `RECOVERY`, dan `PIXHAWK_LOST`.

Navigasi tetap dijalankan oleh Pixhawk. Program ini tidak mengambil alih low-level control atau path planning.

## Persiapan

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config/.env.example config/.env
```

Ubah `config/.env` sesuai deployment, khususnya alamat MQTT dan kredensial. File `.env` tidak boleh di-commit.

## Menjalankan program

Jika MAVProxy sudah menyediakan data pada UDP lokal:

```bash
source .venv/bin/activate
python -m onboard.app
```

Untuk menjalankan MAVProxy dan program Onboard dalam satu script:

```bash
MISSION_PLANNER_IP=<IP_TAILSCALE_GCS> \
PROJECT_DIR="$PWD" \
bash scripts/run_usv.sh
```

Script menggunakan:

- Pixhawk: `/dev/ttyACM0`, 115200 bit/s;
- Supervisory MAVLink: `127.0.0.1:14551`;
- Mission Planner: alamat pada `MISSION_PLANNER_IP`, port 14550.

## Data runtime

Data runtime disimpan di bawah direktori `data/` dan sengaja tidak disertakan dalam repository:

- `data/sessions/`
- `data/logs/`
- `data/database/onboard_outbox.sqlite3`

Persistent outbox tidak boleh dihapus hanya untuk "membersihkan buffer", karena record yang belum memperoleh ACK dapat masih berada di sana.

## Pengujian

```bash
python -m pytest -q
```
