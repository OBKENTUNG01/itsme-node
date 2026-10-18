"""Konfigurasi node. Disimpan di <data>/config.json, tidak pernah masuk repo."""

import hashlib
import json
import os
import secrets
import socket
import uuid

DEFAULTS = {
    "name": socket.gethostname(),
    "port": 7171,
    # Alamat yang diumumkan ke node lain (kosong = IP Tailscale otomatis).
    "advertise": "",
    # Alamat node lain yang dihubungi saat mulai, mis. ["100.1.2.3:7171"].
    "seeds": [],
    # Cari node lain otomatis di jaringan Tailscale.
    "tailscale_discovery": True,
    # Kode gabung bersama; tanpa ini node tidak saling percaya.
    "join_code": "",
    # Komputer dianggap menganggur setelah tidak ada input sekian menit.
    "idle_minutes": 5,
    # True = tetap bekerja walau komputer sedang dipakai.
    "always_work": False,
    # "auto" = dipilih dari hasil tes; "" = tanpa model; atau nama model Ollama.
    "model": "auto",
    "ollama_url": "http://127.0.0.1:11434",
    # Prioritas tambahan saat pemilihan pemimpin (lebih besar = lebih diutamakan).
    "leader_priority": 0,
    # Perintah yang dijalankan saat node ini menjadi / berhenti jadi pemimpin
    # (mis. menyalakan bot Telegram It's Me).
    "leader_start": "",
    "leader_stop": "",
    "max_pages_per_domain": 2000,
    "domain_delay": 2.0,
    "workers": 0,  # 0 = otomatis dari skor
    "auto_update": True,
}


class Config(dict):
    def __init__(self, data_dir):
        super().__init__(DEFAULTS)
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.path = os.path.join(self.data_dir, "config.json")
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8-sig") as f:
                self.update(json.load(f))
        if not self.get("node_id"):
            self["node_id"] = uuid.uuid4().hex[:12]
            self.save()

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(self), f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    @property
    def token(self):
        """Token antar-node, diturunkan dari kode gabung."""
        return hashlib.sha256(("itsme-node:" + self["join_code"]).encode()).hexdigest()


def new_join_code():
    return secrets.token_urlsafe(18)
