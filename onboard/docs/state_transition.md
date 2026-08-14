# USV Supervisory State Transition

Sistem hanya menggunakan empat supervisor state:

- NORMAL
- GCS_LOST
- RECOVERY
- PIXHAWK_LOST

## Prioritas Penentuan

1. MAVLink tidak tersedia menghasilkan PIXHAWK_LOST.
2. MAVLink tersedia dan internet onboard tidak tersedia menghasilkan GCS_LOST.
3. MAVLink dan internet tersedia serta sinkronisasi aktif menghasilkan RECOVERY.
4. MAVLink dan internet tersedia tanpa sinkronisasi aktif menghasilkan NORMAL.

## Status Internet UNKNOWN

Status UNKNOWN tidak langsung menghasilkan GCS_LOST.

GCS_LOST hanya digunakan setelah kegagalan probe internet mencapai jumlah
konfirmasi yang ditentukan.

## Gangguan MQTT dan Server

Gangguan berikut tidak langsung menghasilkan GCS_LOST:

- MQTT disconnected;
- Mosquitto tidak tersedia;
- backend tidak tersedia;
- Raspberry Pi Lab tidak tersedia;
- database gagal;
- ACK timeout.

Ketika internet onboard masih tersedia, kondisi tersebut dicatat melalui status
MQTT, ACK, retry, dan persistent outbox. Supervisor state tetap NORMAL jika
sinkronisasi tidak sedang aktif.

## Pemulihan PIXHAWK_LOST

Setelah MAVLink kembali tersedia:

- internet tidak tersedia menghasilkan GCS_LOST;
- internet tersedia dan sinkronisasi data tertunda dapat dimulai menghasilkan
  RECOVERY;
- internet tersedia tetapi jalur pengiriman belum siap menghasilkan NORMAL;
- internet tersedia dan tidak ada data tertunda menghasilkan NORMAL.
