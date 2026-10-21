# It's Me Node

Jaringan komputer setara (tanpa hub) yang meriset internet memakai model lokal
(Ollama) saat komputer sedang tidak dipakai, menjaga bot Telegram It's Me tetap
hidup walau komputer utamanya mati, dan membiarkanmu **melihat & mengendalikan
layar** serta **menelusuri berkas** semua komputer yang terhubung (seperti
remote desktop, lewat Tailscale, tanpa aplikasi tambahan).

## Pasang (PowerShell)

**Komputer pertama** (yang sudah menjalankan bot It's Me):

```powershell
irm https://raw.githubusercontent.com/OBKENTUNG01/itsme-node/main/install.ps1 | iex
```

**Komputer berikutnya: tanpa mengetik apa pun.**

1. Kirim `/pasang` ke bot It's Me di Telegram.
2. Bot membalas satu baris, misalnya
   `$env:ITSME_INVITE='K7PM-2XQA-9RTD'; irm https://raw.githubusercontent.com/OBKENTUNG01/itsme-node/main/install.ps1 | iex`.
   Tempel baris itu di PowerShell komputer baru, lalu klik **Ya** saat Windows meminta izin Administrator.
3. Selesai. Pemasang menyambung ke Tailscale, mencari komputer yang mengundang, lalu menerima
   kode gabung dan pengaturan bot (token, pemilik, kunci otak). Node **dan** It's Me terpasang
   sekaligus; memori, otak, dan mode akses ikut disalin oleh node.

- Kode undangan hanya berlaku sekali dan hangus dalam 30 menit. Tebakan salah dibatasi.
- Tanpa API key Tailscale, browser terbuka sekali untuk login Tailscale: pilih akun yang sama.
  Simpan API key Tailscale (`tskey-api-…`, buat di login.tailscale.com → Settings → Keys) dengan
  `/kunci tskey-api-…` di Telegram, maka baris dari `/pasang` ikut membawa izin Tailscale sekali
  pakai dan komputer baru tersambung tanpa login sama sekali.
- Kode gabung manual tetap bisa: `$env:ITSME_JOIN='kode'; irm …/install.ps1 | iex`.

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

## Lihat & kendalikan komputer, telusuri berkas

Kalau It's Me (bot) terpasang, pakai **dashboard** It's Me (`http://localhost:8750`)
→ tab **🖥️ Komputer** — satu tempat untuk layar, kendali, dan berkas semua komputer.
Node ini juga menyajikan halaman mandiri yang sama (cadangan, mis. untuk komputer
tanpa dashboard):

```
http://localhost:7171/
```

- **Layar** — pilih komputer di atas, lihat layarnya diperbarui berkala, dan centang
  **Kendalikan** untuk memakai mouse & keyboard-nya seperti remote desktop. Hanya layar
  utama; ini gambar berkala jadi ada sedikit jeda.
- **Berkas** — telusuri folder komputer itu, lihat isi berkas teks, dan unduh. Berkas
  rahasia (`.env`, kunci, token, login browser/aplikasi, `.ssh`, `.claude`) ditandai dan
  **tidak bisa dibuka/diunduh**.
- Dari HP: lewat bot It's Me kirim `/layar <nama>` untuk foto layar sebuah komputer, atau
  buka halaman di atas lewat Tailscale/Cloudflare (lihat `PANDUAN_AKSES_JARAK_JAUH.md`).

Halaman ini hanya bisa dibuka **dari komputer itu sendiri** (127.0.0.1). Komputer yang
kamu lihat menyajikan layar/berkasnya; komputer tempat kamu duduk meneruskan permintaan
ke sana lewat Tailscale dengan kode gabung bersama — jadi komputer di jaringan/lokasi
berbeda pun tetap bisa diakses.

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
`POST /ask {"q": "..."}`, `GET /recent`, `POST /invite-new {"code", "ttl", "env"}`
(hanya dari 127.0.0.1, dipakai `/pasang`). `POST /invite {"code"}` adalah satu-satunya
jalur tanpa kode gabung: menukar kode undangan sekali pakai.

Akses jarak jauh (butuh kode gabung dari node lain; `?node=<id>` meneruskan ke komputer itu):
`GET /screen?w=1280` (PNG layar), `POST /input {events:[…]}` (mouse/keyboard),
`GET /fs/list?path=…`, `GET /fs/read?path=…`, `GET /fs/download?path=…`.
