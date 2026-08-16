# Mengalihkan Broker Onboard ke Laptop

Catatan ini dipakai saat ground server dipindahkan sementara dari Raspberry Pi Lab ke laptop Windows.

Pastikan server laptop sudah aktif, lalu cari alamat Tailscale laptop dengan `tailscale ip -4`. Pada Raspberry Pi onboard, simpan konfigurasi lama sebelum melakukan perubahan:

```bash
cd ~/usv_supervisory_final
cp config/.env config/.env.before_laptop_server
nano config/.env
```

Ubah `MQTT_HOST` menjadi alamat Tailscale laptop:

```ini
MQTT_HOST=<IP_TAILSCALE_LAPTOP>
```

Kredensial MQTT pada laptop dan onboard harus sama. Setelah menyimpan file, uji port 1883, jalankan program onboard, lalu periksa apakah ACK diterima dan buffer kembali ke nol.

Untuk kembali memakai Raspberry Pi Lab:

```bash
cp config/.env.before_laptop_server config/.env
```

Jalankan hanya satu ground server untuk deployment yang sama agar data dan ACK tidak menuju dua backend berbeda.
