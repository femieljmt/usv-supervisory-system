# USV Supervisory Protocol Contract

Protocol name: USV Supervisory Protocol  
Protocol version: 1.0.0  
Implementation status: Final contract for initial implementation

Dokumen ini menjadi acuan bersama untuk program onboard Raspberry Pi USV,
MQTT backend Raspberry Pi Lab, persistent buffer, database SQLite, mekanisme
ACK, dan dashboard.

---

## 1. System Roles

### Raspberry Pi USV

Raspberry Pi USV bertanggung jawab untuk:

- menerima data MAVLink dari Pixhawk melalui MAVProxy;
- membentuk record data operasi;
- menyimpan local log;
- menyimpan record ke persistent buffer;
- memantau ketersediaan internet onboard;
- mengirim data melalui MQTT;
- menerima dan memvalidasi ACK;
- menyinkronkan data tertunda;
- menentukan supervisor state.

### Raspberry Pi Lab

Raspberry Pi Lab bertanggung jawab untuk:

- menjalankan Mosquitto MQTT broker;
- menerima payload telemetry;
- memvalidasi payload;
- menyimpan data ke SQLite;
- mengirim ACK setelah transaksi database berhasil;
- menyediakan data untuk dashboard.

### Dashboard

Dashboard hanya membaca dan menampilkan data dari database.

Dashboard tidak:

- menentukan supervisor state;
- mengirim ACK;
- mengubah isi persistent buffer;
- membuat data simulasi sebagai data operasi.

---

## 2. Record Identity

### vehicle_id

Identitas tetap untuk setiap USV.

Nilai awal:

    usv-01

### session_id

Identitas satu sesi operasi USV.

Ketentuan:

- dibuat oleh Raspberry Pi USV;
- disimpan secara persisten;
- tidak dibuat oleh backend;
- tidak berubah hanya karena program restart;
- berubah ketika sesi operasi baru dimulai.

Contoh:

    20260710T010000Z-a1b2c3

### seq_id

Nomor urut record dalam satu session.

Ketentuan:

- berupa bilangan bulat positif;
- dimulai dari 1 untuk session baru;
- bertambah satu untuk setiap record;
- nilainya disimpan secara persisten;
- tidak kembali ke 1 hanya karena program restart.

Identitas unik satu record adalah:

    vehicle_id + session_id + seq_id

Database dan ACK tidak boleh menggunakan seq_id sebagai identitas tunggal.

---

## 3. Supervisor States

Sistem hanya memiliki empat supervisor state:

    NORMAL
    GCS_LOST
    RECOVERY
    PIXHAWK_LOST

Tidak diperbolehkan menambahkan state seperti:

    BACKEND_LOST
    MQTT_LOST
    SERVER_LOST
    INITIALIZING

### NORMAL

Digunakan ketika:

- data MAVLink diterima;
- internet Raspberry Pi USV tersedia;
- sistem tidak sedang melakukan sinkronisasi data tertunda.

Gangguan broker, backend, database server, atau ACK tidak langsung mengubah
state NORMAL menjadi GCS_LOST.

### GCS_LOST

Digunakan ketika:

- data MAVLink masih diterima;
- internet Raspberry Pi USV tidak tersedia.

GCS_LOST tidak ditentukan hanya berdasarkan:

- MQTT disconnected;
- Mosquitto berhenti;
- backend berhenti;
- Raspberry Pi Lab berhenti;
- database server gagal;
- ACK timeout.

### RECOVERY

Digunakan ketika:

- MAVLink tersedia;
- internet onboard tersedia;
- jalur pengiriman siap;
- data tertunda sedang dikirim dari persistent buffer.

Setelah persistent buffer kosong, state kembali menjadi NORMAL.

### PIXHAWK_LOST

Digunakan ketika heartbeat atau data MAVLink dari Pixhawk tidak diterima dalam
batas timeout.

Setelah MAVLink pulih, state berikutnya harus ditentukan kembali berdasarkan:

- status internet onboard;
- kesiapan jalur pengiriman;
- keberadaan data tertunda;
- proses sinkronisasi.

Sistem tidak boleh langsung menetapkan NORMAL tanpa evaluasi tersebut.

---

## 4. Communication Status

Status komunikasi berikut bukan supervisor state.

### ap_link

Nilai:

    OK
    LOST

### internet_status

Nilai:

    AVAILABLE
    UNAVAILABLE
    UNKNOWN

### mqtt_connection_status

Nilai:

    CONNECTING
    CONNECTED
    DISCONNECTED

### mqtt_publish_status

Nilai:

    PENDING
    PUBLISHED
    FAILED

### ack_status

Nilai:

    PENDING
    ACKED
    TIMEOUT
    INVALID

### sync_status

Nilai:

    IDLE
    SYNCING
    SYNCED
    FAILED

### delivery_type

Nilai:

    LIVE
    REPLAY

LIVE digunakan untuk pengiriman awal.

REPLAY digunakan ketika record dikirim kembali dari persistent buffer.

---

## 5. MQTT Topics

Topic telemetry:

    usv/{vehicle_id}/telemetry

Contoh:

    usv/usv-01/telemetry

Topic ACK:

    usv/{vehicle_id}/ack

Contoh:

    usv/usv-01/ack

Topic status:

    usv/{vehicle_id}/status

Contoh:

    usv/usv-01/status

Topic waypoint:

    usv/{vehicle_id}/waypoints

Contoh:

    usv/usv-01/waypoints

Data LIVE dan REPLAY menggunakan topic telemetry yang sama.

Perbedaannya ditentukan oleh field delivery_type.

MQTT menggunakan QoS 1.

Keberhasilan MQTT publish atau QoS MQTT tidak menggantikan ACK aplikasi.

---

## 6. Telemetry Payload

Payload telemetry menggunakan JSON.

Field yang digunakan:

    protocol_version
    vehicle_id
    session_id
    seq_id
    timestamp
    data_source
    delivery_type
    supervisor_state
    mission_status
    ap_link
    internet_status
    mqtt_connection_status
    mqtt_publish_status
    ack_status
    ack_latency_ms
    retry_count
    buffer_count
    sync_status
    armed
    flight_mode
    lat
    lon
    heading
    groundspeed
    battery_v
    battery_remaining_pct
    gps_fix
    gps_fix_label
    gps_hdop
    wp_index
    wp_total
    wp_dist
    mission_loaded
    mission_complete

Contoh payload:

    {
      "protocol_version": "1.0.0",
      "vehicle_id": "usv-01",
      "session_id": "20260710T010000Z-a1b2c3",
      "seq_id": 1,
      "timestamp": "2026-07-10T01:00:00.000Z",
      "data_source": "PIXHAWK_MAVLINK",
      "delivery_type": "LIVE",
      "supervisor_state": "NORMAL",
      "mission_status": "RUNNING",
      "ap_link": "OK",
      "internet_status": "AVAILABLE",
      "mqtt_connection_status": "CONNECTED",
      "mqtt_publish_status": "PENDING",
      "ack_status": "PENDING",
      "ack_latency_ms": null,
      "retry_count": 0,
      "buffer_count": 1,
      "sync_status": "IDLE",
      "armed": true,
      "flight_mode": "AUTO",
      "lat": 2.1234567,
      "lon": 99.1234567,
      "heading": 90.0,
      "groundspeed": 1.2,
      "battery_v": 15.8,
      "battery_remaining_pct": 80,
      "gps_fix": 3,
      "gps_fix_label": "3D_FIX",
      "gps_hdop": 0.9,
      "wp_index": 1,
      "wp_total": 5,
      "wp_dist": 12.5,
      "mission_loaded": true,
      "mission_complete": false
    }

Field telemetry yang belum tersedia menggunakan null.

Program tidak boleh membuat nilai telemetry palsu untuk menggantikan data yang
tidak diterima.

---

## 7. ACK Payload

ACK diterbitkan oleh backend Raspberry Pi Lab setelah record berhasil disimpan
dan transaksi SQLite berhasil di-commit.

Format ACK:

    {
      "protocol_version": "1.0.0",
      "vehicle_id": "usv-01",
      "session_id": "20260710T010000Z-a1b2c3",
      "seq_id": 1,
      "status": "ACKED",
      "stored_at": "2026-07-10T01:00:01.250Z"
    }

ACK hanya valid jika:

- protocol_version didukung;
- vehicle_id sesuai;
- session_id sesuai;
- seq_id sesuai;
- status bernilai ACKED.

ACK dengan identitas berbeda harus diabaikan.

ACK dengan status selain ACKED tidak boleh menghapus record dari persistent
buffer.

---

## 8. Persistent Buffer Rules

Persistent buffer berada pada Raspberry Pi USV.

Urutan pengelolaan setiap record:

    Record dibentuk
    -> local log ditulis
    -> record disimpan ke persistent buffer
    -> MQTT publish dilakukan
    -> backend menyimpan record
    -> transaksi database di-commit
    -> backend mengirim ACK
    -> onboard memvalidasi ACK
    -> record dihapus dari persistent buffer

Ketentuan:

- buffer harus tetap tersedia setelah program restart;
- buffer harus tetap tersedia setelah Raspberry Pi restart;
- record disimpan sebelum proses pengiriman;
- publish berhasil tidak menghapus record;
- QoS MQTT tidak menghapus record;
- record hanya dihapus setelah ACK aplikasi yang valid;
- record tertua dikirim terlebih dahulu;
- retry_count bertambah ketika pengiriman ulang dilakukan;
- record baru tetap dicatat ketika sinkronisasi berlangsung.

---

## 9. Backend Storage Rules

Backend melakukan urutan berikut:

    menerima payload
    -> memvalidasi payload
    -> menyimpan atau mengenali duplikat
    -> commit transaksi SQLite
    -> mengirim ACK

Jika transaksi database gagal:

    ACK tidak dikirim

Jika record duplikat diterima dan record tersebut sudah tersimpan:

    backend boleh mengirim ACK kembali

Hal tersebut diperlukan jika data telah tersimpan tetapi ACK sebelumnya tidak
diterima onboard.

---

## 10. Database Uniqueness

Database Raspberry Pi Lab menggunakan constraint:

    UNIQUE(vehicle_id, session_id, seq_id)

Database tidak boleh menggunakan:

    UNIQUE(seq_id)

Backend harus menyimpan informasi penerimaan secara terpisah, seperti:

    backend_received_at
    backend_store_status
    backend_ack_sent_at
    duplicate_received

Backend tidak boleh menimpa supervisor_state yang ditentukan onboard.

---

## 11. State Determination Priority

Prioritas penentuan state:

1. Jika MAVLink tidak tersedia, state adalah PIXHAWK_LOST.
2. Jika MAVLink tersedia tetapi internet onboard tidak tersedia, state adalah GCS_LOST.
3. Jika MAVLink dan internet tersedia serta sinkronisasi aktif, state adalah RECOVERY.
4. Jika MAVLink dan internet tersedia serta sinkronisasi tidak aktif, state adalah NORMAL.

Gangguan MQTT, backend, database, atau ACK dicatat melalui status komunikasi dan
persistent buffer, bukan sebagai supervisor state tambahan.

---

## 12. Timestamp Standard

Semua timestamp menggunakan UTC dan format ISO 8601.

Contoh:

    2026-07-10T01:00:00.000Z

Timestamp onboard menunjukkan waktu record dibuat.

Timestamp stored_at menunjukkan waktu backend berhasil menyimpan record.

---

## 13. Final Data Ownership

Raspberry Pi USV menentukan:

- vehicle_id;
- session_id;
- seq_id;
- supervisor_state;
- data MAVLink;
- status internet onboard;
- jumlah persistent buffer;
- status sinkronisasi.

Raspberry Pi Lab menentukan:

- backend_received_at;
- backend_store_status;
- stored_at;
- backend_ack_sent_at;
- status duplikasi database.

Dashboard hanya membaca dan menampilkan data yang tersedia.

Dokumen ini harus identik pada Raspberry Pi USV dan Raspberry Pi Lab.
