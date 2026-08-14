PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS telemetry_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    protocol_version TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    seq_id INTEGER NOT NULL
        CHECK (seq_id >= 1),

    record_timestamp TEXT NOT NULL,
    data_source TEXT NOT NULL,
    delivery_type TEXT NOT NULL
        CHECK (delivery_type IN ('LIVE', 'REPLAY')),

    supervisor_state TEXT NOT NULL
        CHECK (
            supervisor_state IN (
                'NORMAL',
                'GCS_LOST',
                'RECOVERY',
                'PIXHAWK_LOST'
            )
        ),

    mission_status TEXT NOT NULL,

    ap_link TEXT NOT NULL
        CHECK (ap_link IN ('OK', 'LOST')),

    internet_status TEXT NOT NULL
        CHECK (
            internet_status IN (
                'AVAILABLE',
                'UNAVAILABLE',
                'UNKNOWN'
            )
        ),

    mqtt_connection_status TEXT NOT NULL
        CHECK (
            mqtt_connection_status IN (
                'CONNECTING',
                'CONNECTED',
                'DISCONNECTED'
            )
        ),

    mqtt_publish_status TEXT NOT NULL
        CHECK (
            mqtt_publish_status IN (
                'PENDING',
                'PUBLISHED',
                'FAILED'
            )
        ),

    ack_status TEXT NOT NULL
        CHECK (
            ack_status IN (
                'PENDING',
                'ACKED',
                'TIMEOUT',
                'INVALID'
            )
        ),

    ack_latency_ms REAL,

    retry_count INTEGER NOT NULL
        CHECK (retry_count >= 0),

    buffer_count INTEGER NOT NULL
        CHECK (buffer_count >= 0),

    sync_status TEXT NOT NULL
        CHECK (
            sync_status IN (
                'IDLE',
                'SYNCING',
                'SYNCED',
                'FAILED'
            )
        ),

    armed INTEGER NOT NULL
        CHECK (armed IN (0, 1)),

    flight_mode TEXT NOT NULL,

    lat REAL,
    lon REAL,
    heading REAL,
    groundspeed REAL,
    battery_v REAL,
    battery_remaining_pct INTEGER,
    gps_fix INTEGER,
    gps_fix_label TEXT,
    gps_hdop REAL,

    wp_index INTEGER NOT NULL,
    wp_total INTEGER NOT NULL,
    wp_dist REAL,

    mission_loaded INTEGER NOT NULL
        CHECK (mission_loaded IN (0, 1)),

    mission_complete INTEGER NOT NULL
        CHECK (mission_complete IN (0, 1)),

    payload_json TEXT NOT NULL,

    backend_first_received_at TEXT NOT NULL,
    backend_last_received_at TEXT NOT NULL,

    backend_store_status TEXT NOT NULL DEFAULT 'STORED'
        CHECK (
            backend_store_status IN (
                'STORED',
                'DUPLICATE'
            )
        ),

    backend_ack_sent_at TEXT,

    receive_count INTEGER NOT NULL DEFAULT 1
        CHECK (receive_count >= 1),

    duplicate_received INTEGER NOT NULL DEFAULT 0
        CHECK (duplicate_received IN (0, 1)),

    ack_send_count INTEGER NOT NULL DEFAULT 0
        CHECK (ack_send_count >= 0),

    UNIQUE (
        vehicle_id,
        session_id,
        seq_id
    )
);

CREATE INDEX IF NOT EXISTS idx_telemetry_identity
ON telemetry_records (
    vehicle_id,
    session_id,
    seq_id
);

CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp
ON telemetry_records (
    record_timestamp
);

CREATE INDEX IF NOT EXISTS idx_telemetry_state
ON telemetry_records (
    supervisor_state
);

CREATE TABLE IF NOT EXISTS delivery_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    telemetry_record_id INTEGER NOT NULL,

    received_at TEXT NOT NULL,
    source_topic TEXT,

    delivery_type TEXT NOT NULL
        CHECK (delivery_type IN ('LIVE', 'REPLAY')),

    receive_result TEXT NOT NULL
        CHECK (
            receive_result IN (
                'INSERTED',
                'DUPLICATE'
            )
        ),

    payload_json TEXT NOT NULL,

    FOREIGN KEY (telemetry_record_id)
        REFERENCES telemetry_records(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_delivery_record
ON delivery_receipts (
    telemetry_record_id
);

CREATE INDEX IF NOT EXISTS idx_delivery_received_at
ON delivery_receipts (
    received_at
);
