# Mengalihkan MQTT Onboard ke Laptop

1. Pastikan laptop server aktif.
2. Ambil IP Tailscale laptop dengan `tailscale ip -4`.
3. Backup konfigurasi onboard:

```bash
cd ~/usv_supervisory_final
cp config/.env config/.env.before_laptop_server
```

4. Edit:

```bash
nano config/.env
```

5. Ubah hanya:

```ini
MQTT_HOST=<IP_TAILSCALE_LAPTOP>
```

6. Pastikan kredensial MQTT sama dengan yang dibuat saat setup laptop.
7. Uji port 1883.
8. Jalankan onboard satu kali.
9. Pastikan ACK diterima dan buffer kembali nol.

Untuk kembali ke Raspberry Pi Lab:

```bash
cp config/.env.before_laptop_server config/.env
```
