# It's Me Node

Jaringan komputer setara (tanpa hub) yang meriset internet memakai model lokal
(Ollama) saat komputer sedang tidak dipakai, dan menjaga bot Telegram It's Me
tetap hidup walau komputer utamanya mati.

## Pasang (PowerShell)

```powershell
irm https://raw.githubusercontent.com/OBKENTUNG01/itsme-node/main/install.ps1 | iex
```

- Komputer pertama: kosongkan kode gabung, lalu catat kode yang ditampilkan.
- Komputer berikutnya: masukkan kode gabung yang sama, dan login Tailscale dengan akun yang sama.
- Supaya komputer bisa menggantikan bot Telegram, pasang juga
  [It's Me](https://github.com/OBKENTUNG01/website/tree/main/jarvis) di komputer itu
  dengan **token bot yang sama**:
  `irm https://raw.githubusercontent.com/OBKENTUNG01/website/main/jarvis/setup.ps1 | iex`

## Cara kerja

- Semua node setara, tidak ada komputer inti. Satu node menjadi **pemimpin**.
- **Bot It's Me ikut pemimpin.** It's Me dipasang di beberapa komputer, tapi hanya
  komputer pemimpin yang mengambil pesan Telegram; komputer lain siaga. Memori,
  otak yang dipilih, dan mode akses disalin dari pemimpin ke komputer lain tiap 20 detik.
- Kalau pemimpin mati, semua node berhenti sejenak, memilih pemimpin baru (yang punya
  It's Me, lalu prioritas, menganggur, skor tertinggi), lalu melanjutkan tugasnya.
  Prosesnya sekitar 20 detik, dan bot Telegram aktif di pemimpin baru.
- Komputer yang baru menyala tidak merebut posisi pemimpin yang sedang berjalan.
  Pemimpin yang menyala ulang sebentar (mis. memasang pembaruan) langsung memimpin lagi.
- Saat komputer menganggur (default 5 menit tanpa input), node menjelajah internet,
  merangkum halaman dengan model lokal, dan menyimpannya. Begitu komputer dipakai lagi,
  riset dijeda dan model dilepas dari memori.
- Domain dibagi antar node yang menganggur (weighted rendezvous hashing), sehingga
  komputer cepat mendapat bagian lebih besar dan satu situs hanya dikunjungi satu node.
- Komputer lambat tanpa model tetap ikut menjelajah dan meminta rangkuman dari node yang punya model.
- Setiap hasil riset disalin ke satu node lain sebagai cadangan.
- Pembaruan otomatis dari repo ini setiap 30 menit.

## Perintah (di folder `%LOCALAPPDATA%\ItsMeNode\app`)

```
python -m inode status            semua node di jaringan (* = pemimpin)
python -m inode ask "pertanyaan"  tanya hasil riset seluruh jaringan
python -m inode recent            hasil riset terbaru
python -m inode code              tampilkan kode gabung
```

Konfigurasi: `%LOCALAPPDATA%\ItsMeNode\data\config.json` (idle_minutes, always_work,
model, leader_priority, leader_start, leader_stop, itsme_dir, dll).

API lokal (tanpa token dari 127.0.0.1): `GET http://127.0.0.1:7171/status`,
`POST /ask {"q": "..."}`, `GET /recent`.
