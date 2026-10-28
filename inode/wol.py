"""Wake-on-LAN: membangunkan komputer lain yang MATI lewat paket ajaib (UDP broadcast).

Batas fisik (bukan batas kode): paket ajaib cuma siaran LOKAL, tidak diteruskan
router/internet. Jadi ini hanya bisa berhasil kalau node It's Me yang MENGIRIM
paket ini sedang berada di jaringan fisik yang SAMA dengan komputer yang mau
dibangunkan - lihat cluster.py (gosip langsung = satu LAN) dan server.py (/wake,
yang meneruskan ke tetangga LAN kalau node ini sendiri tidak tahu MAC-nya) serta
bridge.py (relai lewat komputer jembatan di lokasi lain). Komputer tujuan juga
harus mendukung & mengaktifkan "Wake on LAN" di BIOS/UEFI + kartu jaringannya, dan
masih tersambung listrik (S3/S4/S5 - bukan benar-benar dicabut). Lihat
PANDUAN_AKSES_JARAK_JAUH.md di repo website untuk penjelasan lengkap & langkah BIOS.
"""

import re
import socket

_MAC_RE = re.compile(r"^[0-9a-f]{12}$")


def normalize_mac(mac):
    """MAC jadi 12 huruf-kecil tanpa pemisah, atau "" kalau bukan MAC yang sah."""
    cleaned = re.sub(r"[^0-9a-fA-F]", "", mac or "").lower()
    return cleaned if _MAC_RE.match(cleaned) else ""


def send_magic_packet(mac, ports=(9, 7)):
    """Siarkan paket ajaib WoL ke `mac`. True kalau terkirim - siaran UDP tidak
    punya balasan, jadi ini TIDAK membuktikan komputernya benar-benar menyala."""
    mac = normalize_mac(mac)
    if not mac:
        return False
    payload = bytes.fromhex("ff" * 6 + mac * 16)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in ports:
            s.sendto(payload, ("255.255.255.255", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def wake_by_name(store, name):
    """Kirim paket ajaib ke MAC yang terakhir terlihat LANGSUNG (gosip LAN) untuk
    komputer bernama `name`, disimpan lewat Cluster.learn -> store.remember_seen.
    Kembalikan (terkirim?, mac). mac kosong = node ini tidak pernah melihatnya."""
    mac = store.last_seen_mac(name)
    if not mac:
        return False, ""
    return send_magic_packet(mac), mac
