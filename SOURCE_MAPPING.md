# Source Mapping

Repository ini disusun dari tiga paket implementasi:

1. Raspberry Pi Onboard → `onboard/`
2. Raspberry Pi Server → aset deployment Linux/Raspberry Pi pada `ground-server/`
3. Laptop Server Windows → basis cross-platform `ground-server/` dan aset deployment Windows

Raspberry Pi Server dan Laptop Server tidak disimpan sebagai dua salinan source yang terpisah karena sebagian besar modul intinya identik. Perbedaan platform dipertahankan melalui `scripts/linux/`, `scripts/windows/`, dokumentasi, dan template service.

## Yang tidak dimasukkan

- database historis `usv_server.sqlite3`;
- backup database;
- runtime logs;
- environment credentials;
- empty deployment stub files;
- alamat Tailscale deployment asli.

Kode sumber asli pada paket unggahan tidak ditimpa; repository ini adalah salinan yang dirapikan untuk publikasi.
