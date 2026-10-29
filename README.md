# It's Me Node

Jaringan komputer setara (tanpa hub) yang meriset internet memakai model lokal
(Ollama) saat komputer sedang tidak dipakai, menjaga bot Telegram It's Me tetap
hidup walau komputer utamanya mati, dan membiarkanmu **melihat & mengendalikan
layar** serta **menelusuri berkas** semua komputer yang terhubung (seperti
remote desktop, **tanpa Tailscale/VPN** dan tanpa aplikasi tambahan).

Komputer sejaringan saling menemukan lewat IP LAN (node juga **menyapu jaringan
LAN-nya sendiri** mencari node It's Me lain, jadi komputer yang sudah bergabung tapi
belum pernah diberi alamat satu sama lain tetap ketemu — lihat "Saling menemukan &
keamanan" di bawah); komputer di jaringan/lokasi berbeda menyambung **keluar** ke
dashboard It's Me (lewat domain/Cloudflare Tunnel sendiri), jadi tetap jalan di balik NAT
tanpa VPN.

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
- Mau satu tautan yang sama dipakai untuk BEBERAPA komputer (tidak hangus setelah
  dipakai sekali)? Kirim **`/pasang_tetap`** (bukan `/pasang`) — berlaku sampai 30 hari,
  atau sampai `/pasang_tetap` dikirim lagi (tautan lama langsung tak berlaku). ⚠️ Siapa
  pun yang punya tautan itu bisa ikut gabung selama masih berlaku — beda dari `/pasang`
  yang otomatis hangus begitu dipakai sekali.
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
  Satu pengecualian disengaja: komputer **berprioritas lebih tinggi** (Hub) yang menyala
  lagi atau baru ketemu merebut kembali posisi dari komputer berprioritas lebih rendah
  (lihat "Prioritas pemimpin" di bawah).
- Saat komputer menganggur (default 5 menit tanpa input), node menjelajah internet,
  merangkum halaman dengan model lokal, dan menyimpannya. Begitu komputer dipakai lagi,
  riset dijeda dan model dilepas dari memori.
- Domain dibagi antar node yang menganggur (weighted rendezvous hashing), sehingga
  komputer cepat mendapat bagian lebih besar dan satu situs hanya dikunjungi satu node.
- Komputer lambat tanpa model tetap ikut menjelajah dan meminta rangkuman dari node yang punya model.
- Setiap hasil riset disalin ke satu node lain sebagai cadangan.
- Pembaruan otomatis dari repo ini setiap 30 menit.

## Saling menemukan & keamanan antar-komputer

**Penemuan LAN.** Setiap node menyapu `/24` miliknya sendiri (hanya jaringan pribadi,
hanya port gosip 7171, tanpa mengirim rahasia): alamat yang menjawab `401 "kode gabung
salah"` ke permintaan tanpa kredensial dikenali sebagai node It's Me dan dijadikan
kandidat gosip. Sapuan berjalan tiap 60 detik selama node masih sendirian, lalu tiap 10
menit untuk mencari yang belum ketemu. Inilah yang mempertemukan komputer-komputer yang
sama-sama sudah punya kode gabung yang sama tapi dulu tidak pernah diberi tahu alamat
satu sama lain (setiap komputer akhirnya memimpin sendirian dan semuanya menjalankan bot
Telegram dengan token yang sama — saling bentrok `Conflict`). Alamat yang gagal terus
ditunda dulu (jeda makin panjang) supaya satu alamat mati tak memperlambat gosip; alamat
yang menolak kode gabung kita dilewati 10 menit. Matikan dengan `lan_discovery: false`.

**Permintaan ditandatangani.** Setiap permintaan antar-komputer memuat tanda tangan
HMAC-SHA256 (`X-Itsme-Ts`, `X-Itsme-Nonce`, `X-Itsme-Sig`) atas metode, alamat, isi, waktu,
dan nonce, dengan kunci yang diturunkan dari kode gabung. Selisih jam lebih dari 60 detik
ditolak dan setiap nonce hanya berlaku sekali, jadi permintaan yang tertangkap di LAN
tidak bisa diputar ulang — beda dari token Bearer statis lama, yang dikirim polos lewat
HTTP dan berlaku selamanya begitu terlihat. Node versi lama belum bisa menandatangani, jadi
selama masa peralihan server **juga menerima Bearer lama** (`accept_legacy_bearer: true`,
bawaan) dan klien jatuh ke Bearer otomatis untuk node lama yang menolak tanda tangan.
**Setelah SEMUA komputer ter-update, setel `accept_legacy_bearer` ke `false`** supaya token
lama yang pernah terlihat di jaringan tak berguna lagi.

**Prioritas pemimpin.** Urutan menang saat dua klaim pemimpin bertemu: punya bot It's Me →
**prioritas** → lama memimpin → skor → id. Prioritas didahulukan sebelum lama memimpin supaya
ketika kelompok komputer yang tadinya terpisah bertemu, Hub yang menang — bukan PC lain yang
kebetulan sudah lebih lama memimpin sendirian. `leader_priority` yang diisi (≠ 0) dipakai
apa adanya; kalau 0, komputer **tanpa** `bridge_url` (Hub, komputer pertama) otomatis
berprioritas 10 dan komputer yang bergabung lewat `/pasang` (punya `bridge_url`) 0. Pemasang
(`install.ps1`) untuk komputer **pertama** mengisi `leader_priority` = 100 supaya Hub jelas
menang walau ada PC yang konfigurasinya tak lengkap. Pemimpin yang berprioritas lebih rendah
digantikan Hub dalam hitungan detik begitu keduanya saling melihat. Komputer yang masih
menjalankan versi lama (< 0.7.0) tidak mengenal prioritas; mereka tetap rukun dengan Hub
begitu ter-update otomatis (≤ 30 menit).

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
- **Berkas** — telusuri folder komputer itu, lihat isi berkas teks, unduh, dan **unggah**
  (kirim berkas dari komputer tempat dashboard dibuka ke komputer yang dipilih, sampai
  100 MB). Berkas rahasia (`.env`, kunci, token, login browser/aplikasi, `.ssh`, `.claude`)
  ditandai dan **tidak bisa dibuka/diunduh/ditimpa** — symlink & hardlink ke berkas
  rahasia ikut ditolak (lihat `inode/remote.py`).
- Dari HP: lewat bot It's Me kirim `/layar <nama>` untuk foto layar sebuah komputer, atau
  buka dashboard lewat domain/Cloudflare Tunnel sendiri (lihat `PANDUAN_AKSES_JARAK_JAUH.md`).

Halaman ini hanya bisa dibuka **dari komputer itu sendiri** (127.0.0.1). Komputer yang
kamu lihat menyajikan layar/berkasnya; komputer tempat kamu duduk meneruskan permintaan
ke sana lewat jaringan LAN dengan kode gabung bersama, atau — untuk komputer di jaringan
berbeda — lewat jembatan dashboard It's Me, jadi tetap bisa diakses tanpa VPN.

## Menyalakan komputer yang mati (Wake-on-LAN)

Komputer yang terdaftar tapi sedang **mati** (bukan cuma menganggur) bisa dicoba
dibangunkan dari dashboard It's Me (tab 🖥️ Komputer, tombol **⚡ Nyalakan** pada
komputer yang tampil "tidur") atau lewat bot Telegram (`/nyalakan <nama>`).

**Batas fisik, bukan batas kode:** paket ajaib Wake-on-LAN cuma siaran di jaringan
LOKAL, tidak bisa lewat internet langsung ke komputer yang benar-benar mati. Jadi ini
HANYA berhasil kalau ada **komputer It's Me LAIN yang masih menyala satu jaringan
fisik** dengan komputer yang mau dibangunkan, dan node itu **pernah** melihatnya
langsung lewat gosip LAN (diingat otomatis, bertahan walau komputernya sudah mati
berhari-hari). Node yang diminta tapi tidak tahu MAC-nya sendiri otomatis menanyakan
tetangga LAN-nya dulu (satu putaran, lihat `/wake` di bawah); kalau komputernya di
lokasi/jaringan lain (lewat jembatan), dashboard menanyai tiap komputer jembatan yang
online satu per satu. Komputer sendirian tanpa teman sejaringan yang menyala tidak
bisa dibangunkan murni lewat web — perlu alat tambahan (mis. colokan pintar).

Syarat di komputer TUJUAN (bukan di komputer yang mengirim sinyal): "Wake on LAN"
aktif di BIOS/UEFI dan di pengaturan kartu jaringan (Device Manager > kartu jaringan >
Power Management > "Allow this device to wake the computer"), dan komputer dalam
keadaan tidur/mati normal (S3/S4/S5) — bukan benar-benar dicabut dari listrik.

## Perintah (di folder `%LOCALAPPDATA%\ItsMeNode\app`)

```
python -m inode status            semua node di jaringan (* = pemimpin)
python -m inode ask "pertanyaan"  tanya hasil riset seluruh jaringan
python -m inode recent            hasil riset terbaru
python -m inode code              tampilkan kode gabung
python -m inode wake "nama"       bangunkan komputer itu (Wake-on-LAN)
```

Konfigurasi: `%LOCALAPPDATA%\ItsMeNode\data\config.json` (idle_minutes, always_work,
model, leader_priority, leader_start, leader_stop, bridge_url, bridge_token,
accept_legacy_bearer, lan_discovery, tunnel_cmd, dll). `tunnel_cmd` (mis. `cloudflared tunnel run itsme`) dijalankan hanya
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
`GET /fs/list?path=…`, `GET /fs/read?path=…`, `GET /fs/download?path=…`,
`POST /fs/write {"path","b64"}` (tulis/unggah berkas, maks 100 MB, base64 di `b64`;
dipakai tombol unggah di dashboard tab 🖥️ Komputer).

`POST /wake {"name"}` — bangunkan komputer bernama `name` (Wake-on-LAN, lihat
"Menyalakan komputer yang mati" di atas). Kalau node ini sendiri tidak pernah melihat
MAC-nya langsung, otomatis menanyakan tetangga LAN-nya satu putaran (`relay=false`
pada panggilan ke tetangga, supaya tidak tanya-berantai tanpa akhir). Selalu balas
200 dengan `{"ok": true/false, ...}` — cek `ok`, bukan kode HTTP.
