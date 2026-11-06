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

- **Ollama wajib.** Pemasang ikut memasang Ollama di tiap komputer yang bergabung (winget, lalu
  pemasang resmi ollama.com sebagai cadangan) — tanpa syarat RAM; komputer lemah tetap punya
  Ollama dan node-nya sendiri yang memutuskan model mana yang dipakai. Kalau pemasangan Ollama
  gagal (mis. tak ada internet), komputer tetap bergabung dan tampil **belum terpasang** di
  dashboard → **Pengaturan → Ollama per komputer → Pasang** (lihat "Ollama per komputer" di bawah).
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

**Install ulang OS?** Jika `config.json` lama masih ada, kode gabung yang tersimpan tetap
bisa dipakai. Jika konfigurasi hilang, minta kode undangan baru lewat `/pasang` lalu tempel
barisnya. Gabung ulang otomatis hanya berdasarkan sidik jari perangkat keras sudah
dinonaktifkan: sidik jari adalah identitas yang dapat ikut terlihat pada gossip HTTP, bukan
bukti rahasia bahwa pemanggil memiliki komputer itu.

## Cara kerja

- Semua node setara, tidak ada komputer inti. Satu node menjadi **pemimpin**.
- **Bot It's Me ikut pemimpin.** It's Me dipasang di beberapa komputer, tapi hanya
  komputer pemimpin yang mengambil pesan Telegram; komputer lain siaga. Memori,
  otak yang dipilih, dan mode akses disalin dari pemimpin ke komputer lain tiap 20 detik.
  Isi lama yang ditimpa **dicadangkan dulu** di `<folder It's Me>\data\sync-backup\` (20
  salinan terakhir per berkas; memori yang kosong tidak perlu dicadangkan), jadi catatan
  di komputer yang baru bergabung tidak hilang tanpa jejak.
- Kalau pemimpin mati, semua node berhenti sejenak, memilih pemimpin baru (yang punya
  It's Me, lalu prioritas, menganggur, skor tertinggi), lalu melanjutkan tugasnya.
  Prosesnya sekitar 20 detik, dan bot Telegram aktif di pemimpin baru.
- Komputer yang baru menyala tidak merebut posisi pemimpin yang sedang berjalan.
  Pemimpin yang menyala ulang sebentar (mis. memasang pembaruan) langsung memimpin lagi.
  Satu pengecualian disengaja: komputer **berprioritas lebih tinggi** (Hub) yang menyala
  lagi atau baru ketemu merebut kembali posisi dari komputer berprioritas lebih rendah
  (lihat "Prioritas pemimpin" di bawah).
- Saat komputer menganggur (default 5 menit tanpa input), node menjelajah internet,
  merangkum halaman dengan model lokal, dan menyimpannya. Begitu komputer diakses jarak
  jauh, riset dijeda. Model **tidak lagi dilepas** dari memori kalau `think` aktif (bawaan):
  komputer tetap siap berpikir untuk bot (lihat "Berpikir bersama" di bawah).
- Domain dibagi antar node yang menganggur (weighted rendezvous hashing), sehingga
  komputer cepat mendapat bagian lebih besar dan satu situs hanya dikunjungi satu node.
- Komputer lambat tanpa model tetap ikut menjelajah dan meminta rangkuman dari node yang punya model.
- Setiap hasil riset disalin ke satu node lain sebagai cadangan.
- Pembaruan otomatis dari repo ini setiap 30 menit.

## Berpikir bersama ("otak lokal bersama")

Bot It's Me (di komputer pemimpin) membagi pekerjaan **berpikir** ke semua komputer yang sudah
bergabung, lewat rute `POST /think` (bertanda tangan; bot cukup bicara dengan node di komputernya
sendiri, node meneruskan ke komputer tujuan dengan `"node": "<id>"`). Isi `/status` tiap komputer
memuat `think`: model, kecepatan nyata (token/detik), antrean, apakah `think:false` dipatuhi, dan
apakah berjalan di CPU saja. Itu ikut bergosip tiap 2 detik, jadi bot selalu tahu siapa yang siap.

Ini load-balancing **per permintaan** untuk menambah throughput, bukan distributed inference: satu
permintaan dikerjakan utuh oleh satu node/model; bobot model tidak dibagi antar-komputer. POST aktif
membatasi badan 16 MiB dan menolak lebih awal dengan HTTP 413; `/fs/write` diberi 128 MiB agar unggah
berkas 100 MB yang dienkode base64 tetap muat. Batas eksplisit ini membatasi konsumsi per permintaan
(lihat [MITRE CWE-400](https://cwe.mitre.org/data/definitions/400.html)). Endpoint rejoin lama `/enroll`
berbeda: ia langsung menolak HTTP 410 sebelum membaca badan, karena jalur sidik jari sudah dinonaktifkan.

Supaya komputer **tidak berhenti berpikir walau sedang dipakai** (mengetik, dsb.):

- **Ollama dijaga hidup.** Mati → dinyalakan lagi otomatis (jeda 15, 30, 45 ... detik, paling lama 5 menit).
- **Model tetap termuat** (`keep_alive: -1`); tidak dilepas saat komputer dipakai. Kalau dilepas pihak lain,
  penjaga memuatnya lagi dalam ±45 detik.
- **Prioritas rendah (best-effort, Windows).** Proses server dan runner Ollama yang terdeteksi berjalan
  di bawah prioritas normal; ini hanya mengatur giliran CPU, bukan membatasi penggunaan CPU/RAM, sehingga
  beban model masih dapat membuat komputer lambat atau kehabisan memori. Pemindaian berdasarkan nama proses
  juga dapat menurunkan prioritas instance Ollama lain di komputer yang sama, bukan hanya instance yang
  dipakai node ini.
- **Mencegah tidur otomatis hanya selama ada permintaan berpikir (Windows, bawaan aktif)** dengan
  `SetThreadExecutionState`; saat idle node melepaskannya agar Windows boleh tidur. Microsoft
  [memperingatkan](https://learn.microsoft.com/en-us/windows/desktop/Power/system-sleep-criteria?redirectedfrom=MSDN)
  bahwa menahan permintaan itu terus-menerus dapat menguras baterai perangkat Modern Standby;
  setel `"think_keep_awake": false` di `config.json` untuk menonaktifkan pencegahan tidur bahkan saat bekerja. Sleep/tutup laptop oleh
  pemilik tetap berlaku — itu keputusan pemilik, bukan "mati sendiri". Di sistem selain Windows fitur ini
  tidak diterapkan.
- **Riset latar mengalah** selama ada permintaan berpikir.
- **Tangga keamanan.** Kalau proses model mati mendadak (mis. `ggml_vulkan: device lost` di kartu grafis
  lama), panggilan diulang dengan konteks lebih kecil lalu CPU saja; tingkat yang jalan diingat di
  `think_state.json`. Dulu tes kemampuan mencatat komputer seperti ini "0 tok/s, tanpa model" dan
  komputernya tidak pernah dipakai berpikir.

Satu-satunya saat SEMUA komputer berhenti berpikir: **pemimpin mati dan pemilihan pemimpin baru
berjalan** (fase `electing`, ±20 detik) — `/think` menjawab 503 sampai pemimpin baru terdengar, lalu
langsung berpikir lagi. Rancangan ini sudah ada sejak awal (lihat docstring `cluster.py`).

Model yang dipakai: hasil tes kemampuan (`qwen3:14b/8b/4b/1.7b` menurut RAM & VRAM), atau paksa dengan
`think_model`. **Catatan model 4B:** tag polos `qwen3:4b` kini menunjuk build *thinking-2507* yang SELALU
berpikir (`think:false` diabaikan, penalarannya bocor ke jawaban), jadi tingkat 4B memakai
`qwen3:4b-instruct-2507-q4_K_M`. Setelah pembaruan, komputer menguji ulang kemampuannya sekali
(`BENCH_VERSION` 2) dan, bila perlu, mengunduh model itu (±2,5 GB).

Pengaturan di `config.json` (semuanya opsional):

| Kunci | Bawaan | Arti |
|---|---|---|
| `think` | `true` | `false` = komputer ini tidak ikut berpikir (dan model kembali dilepas saat dipakai) |
| `think_model` | `""` | paksa model Ollama tertentu; kosong = hasil tes kemampuan |
| `think_parallel` | `0` | permintaan bersamaan; 0 = otomatis (1 untuk CPU, 2 untuk GPU ≥ 6 GB) |
| `think_queue` | `4` | permintaan yang boleh menunggu giliran; lebihnya ditolak (bot pakai komputer lain) |
| `think_keep_awake` | `true` | cegah tidur otomatis selama node hidup |

Batas jujur: komputer berRAM < 6 GB (atau tanpa Ollama) tidak punya model dan tidak ikut berpikir;
kecepatan menurut perangkat keras (±5–10 token/detik untuk model 1,7–4B di CPU biasa). Model lokal
kecil tidak sepintar model cloud — bot menutupinya dengan alur memahami → menjawab → memeriksa.

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

**HMAC bukan enkripsi.** Permintaan antar-node saat ini memakai HTTP; prompt, jawaban, dan metadata dapat
dibaca oleh pengamat jaringan. Gunakan hanya LAN tepercaya atau jaringan/tunnel terenkripsi, percayai setiap
komputer yang bergabung, dan jangan membuka port node ke internet publik. HMAC memberi autentikasi/integritas
serta anti-replay untuk mode tanda tangan, bukan kerahasiaan isi.

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
  - **Kursor terlihat.** Tangkapan layar Windows (GDI) memang tidak pernah memuat kursor mouse,
    jadi dashboard menggambar kursor komputer tujuan sendiri di atas gambar (seperti Chrome
    Remote Desktop: kursor bukan bagian video). Posisi, terlihat/tidaknya, dan **bentuknya**
    (panah, I-beam, tangan, ubah-ukuran, tunggu, dst.) dibaca dari `GET /cursor`.
  - **Mode trackpad** (HP): 1 jari geser = kursor bergerak relatif, ketuk = klik, ketuk 2 jari =
    klik kanan, ketuk 3 jari = klik tengah, tahan lalu geser = seret (lingkaran abu-abu), 2 jari
    geser = gulir (vertikal & horizontal; "Gulir alami" bisa dimatikan), cubit = zoom (tampilan
    mengikuti kursor), 3 jari geser = geser tampilan. Posisi kursor dipegang klien dan dikirim
    ABSOLUT (`move` x,y 0..1), jadi tidak terpengaruh kecepatan/akselerasi pointer Windows;
    saat tidak dipakai kursor mengikuti posisi asli komputer tujuan lagi. Di komputer (mouse),
    trackpad memakai Pointer Lock; tanpa trackpad kursor lokalmu berubah bentuk mengikuti
    kursor komputer tujuan.
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

## Ollama per komputer (pasang / copot dari dashboard)

Dashboard → **Pengaturan → Ollama per komputer** menampilkan semua komputer yang terhubung dan
mana yang **sudah** / **belum** punya Ollama (status ikut gosip antar-node, ±10 detik), dengan
tombol **Pasang** / **Copot** per komputer dan **Pasang di semua yang belum terpasang**. Pemilik
tetap diminta konfirmasi lewat Telegram sebelum jalan.

- **Pasang** — winget (`Ollama.Ollama`, per-pengguna, tanpa admin; kode `0x8A15002B`/`0x8A150061`
  = sudah terpasang), lalu pemasang resmi ollama.com sebagai cadangan. Unduhan ±1,7 GB jadi ini
  PEKERJAAN LATAR di komputer tujuan; kemajuannya tampil di daftar. Aplikasi tray dimatikan dan
  dicegah menyala sendiri (node yang menjalankan `ollama serve` tersembunyi).
- **Copot** — uninstaller resmi (`/VERYSILENT`). **Model di `~/.ollama` tetap tersimpan**, jadi
  pasang ulang tidak mengunduh model lagi. Dua jebakan nyata ditangani: `unins000.exe` menyalin
  dirinya ke TEMP lalu langsung keluar (hasil dipantau sampai registry & berkas benar-benar
  hilang, bukan dari kode keluar), dan proses Ollama yang menyala lagi di tengah pekerjaan
  mengunci berkas (dimatikan terus selama pekerjaan; `OLLAMA_MAINTENANCE` mencegah penjaga
  `thinker.py` menyalakannya lagi).
- Sisa uninstall yang tak tuntas (berkas ada, registry hilang) tampil `leftover` dan dihitung
  **belum terpasang** — tombol Pasang memperbaikinya.
- API: `GET /ollama?node=<id>` (status + pekerjaan berjalan), `POST /ollama {"node","action":"install"|"uninstall"}`
  (200 `{ok, job}`, atau 409 bila masih ada pekerjaan). Field `ollama` ikut di `/status` tiap node.
- Hanya Windows; di macOS/Linux dashboard melaporkan itu apa adanya.

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
sekali pakai; batas salah 30 tebakan per alamat pemanggil per 10 menit agar satu klien tidak
mengunci semua undangan. Jalur ini tanpa kode gabung karena kode undangannya sendiri adalah
otorisasi. Biasanya dashboard Hub meneruskan `/api/node/invite` ke sini dari 127.0.0.1,
sehingga komputer baru tidak perlu Tailscale. Endpoint lama `POST /enroll` dan
`/api/node/enroll` sekarang selalu membalas HTTP 410 dan tidak mengembalikan kode gabung,
token jembatan, atau `.env`; jika konfigurasi perangkat hilang, gunakan undangan sekali-pakai.

Tanda tangan HMAC antar-node menjaga autentikasi/integritas, **bukan enkripsi**. Gossip masih
menggunakan HTTP, sehingga pengamat jaringan dapat membaca fingerprint dalam payload; karena
itu fingerprint tidak boleh dipakai sebagai kredensial. OWASP menyarankan fingerprint perangkat
hanya menjadi sinyal risiko, bukan pengganti autentikasi, dan MITRE menguraikan risiko capture/replay
jika trafik dapat disadap: [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
[MITRE CWE-294](https://cwe.mitre.org/data/definitions/294.html). Hindari membuka port node ke internet.

Akses jarak jauh (butuh kode gabung dari node lain; `?node=<id>` meneruskan ke komputer itu):
`GET /screen?w=1280` (PNG layar), `GET /cursor` (posisi/bentuk kursor), `POST /input {events:[…]}`
(mouse/keyboard; balasannya memuat `cursor`; kejadian: `move` x,y 0..1 = piksel/(lebar-1) mendarat
tepat di piksel itu, `move_rel` dx,dy, `down`/`up`/`click`/`dblclick`, `scroll` dy/dx, `text`, `key`),
`POST /power {"action":"shutdown"|"restart"}`, `GET/POST /ollama` (lihat "Ollama per komputer"),
`GET /fs/list?path=…`, `GET /fs/read?path=…`, `GET /fs/download?path=…`,
`POST /fs/write {"path","b64"}` (tulis/unggah berkas, maks 100 MB, base64 di `b64`;
dipakai tombol unggah di dashboard tab 🖥️ Komputer).

`POST /wake {"name"}` — bangunkan komputer bernama `name` (Wake-on-LAN, lihat
"Menyalakan komputer yang mati" di atas). Kalau node ini sendiri tidak pernah melihat
MAC-nya langsung, otomatis menanyakan tetangga LAN-nya satu putaran (`relay=false`
pada panggilan ke tetangga, supaya tidak tanya-berantai tanpa akhir). Selalu balas
200 dengan `{"ok": true/false, ...}` — cek `ok`, bukan kode HTTP.
