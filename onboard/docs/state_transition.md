# Aturan Transisi State Supervisory

Dokumen ini mencatat aturan yang saya gunakan untuk menentukan kondisi supervisory pada program onboard. State dibuat sesedikit mungkin agar kondisi navigasi, jaringan, dan pengiriman data tidak tercampur.

## Empat state yang digunakan

- `NORMAL`: MAVLink dan internet tersedia, serta sinkronisasi tidak aktif.
- `GCS_LOST`: MAVLink tersedia, tetapi internet onboard sudah dinyatakan terputus.
- `RECOVERY`: MAVLink dan internet tersedia, serta data tertunda sedang disinkronkan.
- `PIXHAWK_LOST`: heartbeat atau data MAVLink tidak diterima dalam batas timeout.

Prioritas pemeriksaannya adalah Pixhawk, internet, proses sinkronisasi, lalu kondisi normal. Karena itu, kehilangan MAVLink selalu menghasilkan `PIXHAWK_LOST`, terlepas dari status komponen lain.

## Saat status internet belum pasti

Nilai `UNKNOWN` tidak langsung mengubah state menjadi `GCS_LOST`. Program menunggu jumlah kegagalan probe yang sudah ditentukan agar gangguan singkat tidak dianggap sebagai kehilangan komunikasi.

## Gangguan MQTT atau server

Broker terputus, backend berhenti, database gagal, atau ACK timeout tidak otomatis berarti `GCS_LOST`. Selama internet onboard masih tersedia, masalah tersebut dicatat melalui status MQTT, ACK, retry, dan persistent outbox.

Jika tidak ada sinkronisasi yang sedang berjalan, state tetap `NORMAL`. Record yang belum mendapat ACK tetap berada di outbox untuk dicoba kembali.

## Pemulihan setelah PIXHAWK_LOST

Ketika MAVLink kembali tersedia, program tidak langsung menetapkan `NORMAL`. Kondisi berikutnya ditentukan kembali:

- internet belum tersedia: `GCS_LOST`;
- internet tersedia dan data tertunda mulai dikirim: `RECOVERY`;
- internet tersedia tetapi jalur pengiriman belum siap: `NORMAL`, dengan masalah pengiriman dicatat pada status komunikasi;
- internet tersedia dan tidak ada data tertunda: `NORMAL`.

Aturan payload dan status komunikasi lengkap tersedia pada [kontrak protokol](protocol_contract.md).
