# It's Me Node

Jaringan komputer setara (tanpa hub) yang meriset internet memakai model lokal
(Ollama) saat komputer sedang tidak dipakai, menjaga bot Telegram It's Me tetap
hidup walau komputer utamanya mati, dan membiarkanmu **melihat & mengendalikan
layar** serta **menelusuri berkas** semua komputer yang terhubung (seperti
remote desktop, **tanpa Tailscale/VPN** dan tanpa aplikasi tambahan).

Komputer sejaringan saling menemukan lewat IP LAN; komputer di jaringan/lokasi
berbeda menyambung **keluar** ke dashboard It's Me (lewat domain/Cloudflare Tunnel
sendiri), jadi tetap jalan di balik NAT tanpa VPN.

## Pasang (PowerShell)

**Komputer pertama** (yang sudah menjalankan bot It's Me):

```powershell
irm https://raw.githubusercontent.com/OBKENTUNG01/itsme-node/main/install.ps1 | iex
```

**Komputer berikutnya: tanpa mengetik apa pun.**

1. Kirim `/pasang` ke bot It's Me di Telegram.
2. Bot membalas satu baris, misalnya
   `$env:ITSME_INVITE='K7PM-2XQA-9RTD'; $env:ITSME_HUB='https://domainku.com'; irm https://raw.githubusercontent.com/OBKENTUNG01/itsme-node/main/install.ps1 | iex`.
   Tempel baris itu di PowerShell komputer baru, lalu klik **Ya** saat Windows meminta izin Administrator.
3. Selesai. **Tanpa Tailscale/login apa pun.** Pemasang menukar kode undangan di dashboard Hub
   (lewat alamat publiknya), menerima kode gabung + token jembatan + pengaturan bot (token, pemilik,
   kunci otak), lalu menyambung **keluar** ke Hub. Node **dan** It's Me terpasang sekaligus; memori,
   otak, dan mode akses ikut disalin oleh node. Tailscale yang masih terpasang ikut dicopot.

- Kode undangan hanya berlaku sekali dan hangus dalam 30 menit. Tebakan salah dibatasi.
- Syaratnya: Hub (komputer yang mengundang) punya alamat publik — buka dashboard lewat
  domain/Cloudflare Tunnel sendiri lalu isi `ITSME_PUBLIC_URL=https://domainmu` di `.env`-nya
  (lihat `PANDUAN_AKSES_JARAK_JAUH.md`). Komputer sejaringan juga bisa pakai alamat LAN Hub.
- Kode gabung manual tetap bisa: `$env:ITSME_JOIN='kode'; irm …/install.ps1 | iex`.

**Install ulang OS? Komputer tidak hilang dari jaringan.** Kalau komputer yang sudah
terdaftar diinstal ulang Windows-nya, kirim `/pasang` lagi lalu tempel baris barunya.
Jaringan mengenali komputer itu dari **sidik jari perangkat kerasnya** (UUID
motherboard/BIOS — tetap sama walau OS baru) dan mengembalikan kode gabung + pengaturan
bot otomatis lewat Hub, tanpa kode undangan baru kalau komputer itu **pernah** terdaftar.

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
  buka dashboard lewat domain/Cloudflare Tunnel sendiri (lihat `PANDUAN_AKSES_JARAK_JAUH.md`).

Halaman ini hanya bisa dibuka **dari komputer itu sendiri** (127.0.0.1). Komputer yang
kamu lihat menyajikan layar/berkasnya; komputer tempat kamu duduk meneruskan permintaan
ke sana lewat jaringan LAN dengan kode gabung bersama, atau — untuk komputer di jaringan
berbeda — lewat jembatan dashboard It's Me, jadi tetap bisa diakses tanpa VPN.

## Perintah (di folder `%LOCALAPPDATA%\ItsMeNode\app`)

```
python -m inode status            semua node di jaringan (* = pemimpin)
python -m inode ask "pertanyaan"  tanya hasil riset seluruh jaringan
python -m inode recent            hasil riset terbaru
python -m inode code              tampilkan kode gabung
```

Konfigurasi: `%LOCALAPPDATA%\ItsMeNode\data\config.json` (idle_minutes, always_work,
model, leader_priority, leader_start, leader_stop, bridge_url, bridge_token,
tunnel_cmd, dll). `tunnel_cmd` (mis. `cloudflared tunnel run itsme`) dijalankan hanya
selama node ini pemimpin, supaya alamat publik mengikuti pemimpin saat failover
(lihat `PANDUAN_AKSES_JARAK_JAUH.md`).

API lokal (tanpa token dari 127.0.0.1): `GET http://127.0.0.1:7171/status`,
`POST /ask {"q": "..."}`, `GET /recent`, `POST /invite-new {"code", "ttl", "env"}`
(hanya dari 127.0.0.1, dipakai `/pasang`). `POST /invite {"code"}` menukar kode undangan
sekali pakai. `POST /enroll {"fingerprint","name"}` mengembalikan kode gabung untuk komputer
yang sidik jarinya sudah terdaftar (gabung-ulang setelah install ulang). Keduanya jalur tanpa
token kode gabung, dan biasanya dipanggil lewat dashboard Hub (`/api/node/invite`, `/api/node/enroll`)
yang meneruskannya ke sini dari 127.0.0.1 — jadi komputer baru tidak perlu Tailscale.

Akses jarak jauh (butuh kode gabung dari node lain; `?node=<id>` meneruskan ke komputer itu):
`GET /screen?w=1280` (PNG layar), `POST /input {events:[…]}` (mouse/keyboard),
`GET /fs/list?path=…`, `GET /fs/read?path=…`, `GET /fs/download?path=…`.
