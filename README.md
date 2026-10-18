# It's Me Node

Jaringan komputer setara (tanpa hub) yang meriset internet memakai model lokal
(Ollama) saat komputer sedang tidak dipakai.

## Pasang (PowerShell)

```powershell
irm https://raw.githubusercontent.com/OBKENTUNG01/itsme-node/main/install.ps1 | iex
```

- Komputer pertama: kosongkan kode gabung, lalu catat kode yang ditampilkan.
- Komputer berikutnya: masukkan kode gabung yang sama, dan login Tailscale dengan akun yang sama.

## Cara kerja

- Semua node setara. Satu node menjadi **pemimpin** (yang memegang bot Telegram lewat
  `leader_start` / `leader_stop` di config).
- Kalau pemimpin mati, semua node berhenti sejenak, memilih pemimpin baru (yang
  menganggur, prioritas, lalu skor tertinggi), lalu melanjutkan tugasnya. Prosesnya sekitar 20 detik.
- Saat komputer menganggur (default 5 menit tanpa input), node menjelajah internet,
  merangkum halaman dengan model lokal, dan menyimpannya.
- Domain dibagi antar node yang menganggur (weighted rendezvous hashing), sehingga
  komputer cepat mendapat bagian lebih besar dan satu situs hanya dikunjungi satu node.
- Komputer lambat tanpa model tetap ikut menjelajah dan meminta rangkuman dari node yang punya model.
- Setiap hasil riset disalin ke satu node lain sebagai cadangan.
- Pembaruan otomatis dari repo ini setiap 30 menit.

## Perintah (di folder `%LOCALAPPDATA%\ItsMeNode\app`)

```
python -m inode status            semua node di jaringan
python -m inode ask "pertanyaan"  tanya hasil riset seluruh jaringan
python -m inode recent            hasil riset terbaru
python -m inode code              tampilkan kode gabung
```

Konfigurasi: `%LOCALAPPDATA%\ItsMeNode\data\config.json` (idle_minutes, always_work,
model, leader_priority, leader_start, leader_stop, dll).

API lokal (tanpa token dari 127.0.0.1): `GET http://127.0.0.1:7171/status`,
`POST /ask {"q": "..."}`, `GET /recent`.
