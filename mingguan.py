import requests, os, re, json
from datetime import datetime, timezone, timedelta

# ============ PENGATURAN ============
WEBHOOK = os.environ["WEBHOOK"]

GAMES = {
    "Dragon Tower Defense": 103076556916764,
    "Steal an Anime Pack": 133151378618377,
    "Summon a pet Army": 89695026030574,
    "Throw Food for animals": 120219252362013,
    "Stand Dice": 118619751815929,
    "Fish for Secret Animals": 107192230357786,
    "Shoot Arrows For Animals!": 128749725449995,
    "Throw a Seed": 129081220403721,
    "+1 Spider Arm for Eggs": 124807263234597,
    "Steal with Spider Arms": 120413462077800,
    "Cast For Fish!": 131424283721618,
}

MAKS_HARI = 7                    # tampilkan event yang mulai dalam N hari ke depan
TERJEMAH = False                 # True = terjemahkan deskripsi ke Indonesia (butuh: pip install deep-translator)
HITUNG_MUNDUR_DISCORD = True     # True = hitung mundur hidup dari Discord (bahasa ikut pengaturan Discord)
                                 # False = hitung mundur bahasa Indonesia (hanya segar sesuai jadwal script)
LAMA_TANDA_BARU_JAM = 24         # tanda 🆕 tampil selama N jam sejak event pertama terdeteksi
STATE_FILE = "state_rekap.json"
# ====================================

WIB = timezone(timedelta(hours=7))
HARI = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def parse(t):
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def fmt(dt):
    d = dt.astimezone(WIB)
    return f"{HARI[d.weekday()]}, {d.day} {BULAN[d.month-1]} {d.year} • {d:%H:%M} WIB"


def lagi(dt):
    s = dt - datetime.now(timezone.utc)
    if s.total_seconds() <= 0:
        return "sedang berlangsung"
    hari, sisa = s.days, s.seconds
    jam, menit = sisa // 3600, (sisa % 3600) // 60
    if hari:
        return f"{hari} hari {jam} jam lagi"
    if jam:
        return f"{jam} jam {menit} menit lagi"
    return f"{menit} menit lagi"


def hitung_mundur(dt):
    if HITUNG_MUNDUR_DISCORD:
        return f"<t:{int(dt.timestamp())}:R>"
    return lagi(dt)


def bersih(teks, maks=120):
    teks = re.sub(r"\s+", " ", teks or "").strip()   # buang enter dan spasi ganda
    if len(teks) <= maks:
        return teks
    return teks[:maks].rsplit(" ", 1)[0] + "…"       # potong di batas kata


def terjemah(teks):
    if not TERJEMAH or not teks:
        return teks
    try:
        from deep_translator import GoogleTranslator
        return GoogleTranslator(source="auto", target="id").translate(teks) or teks
    except Exception:
        return teks


# ---------- baca state ----------
state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
raw_seen = state.get("seen")
pertama = raw_seen is None                      # pengecekan pertama: jangan tandai semua sebagai baru
if isinstance(raw_seen, list):                  # kompatibel dengan state versi lama
    seen_lama = {k: 0 for k in raw_seen}
else:
    seen_lama = raw_seen or {}
seen_baru = {}

now = datetime.now(timezone.utc)
batas = now + timedelta(days=MAKS_HARI)
dari = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
embeds = []

# ---------- ambil data tiap game ----------
for nama, place_id in GAMES.items():
    link = f"https://www.roblox.com/games/{place_id}"
    try:
        uid = requests.get(
            f"https://apis.roblox.com/universes/v1/places/{place_id}/universe", timeout=15
        ).json()["universeId"]
        url = f"https://apis.roblox.com/virtual-events/v1/universes/{uid}/virtual-events"
        data = requests.get(url, params={"fromUtc": dari}, timeout=15).json().get("data", [])
    except Exception as e:
        embeds.append({"title": f"🎮 {nama}", "url": link, "color": 0xED4245,
                       "description": f"⚠️ Gagal mengambil data ({e})"})
        continue

    akan = []
    for ev in data:
        if ev.get("eventStatus") != "active":
            continue
        mulai = parse(ev["eventTime"]["startUtc"])
        if now <= mulai <= batas:          # hanya yang belum mulai dan dalam rentang
            akan.append((mulai, ev))
    akan.sort(key=lambda x: x[0])

    if not akan:
        teks = "Belum ada event terjadwal minggu ini."
    else:
        blok = []
        for mulai, ev in akan:
            key = f"{uid}:{ev['id']}"
            pertama_lihat = seen_lama.get(key)
            if pertama_lihat is None:
                pertama_lihat = 0 if pertama else now.timestamp()
            seen_baru[key] = pertama_lihat
            baru = ""
            if pertama_lihat and (now.timestamp() - pertama_lihat) < LAMA_TANDA_BARU_JAM * 3600:
                baru = "🆕 "

            judul = ev.get("title", "Event")
            desk = terjemah(bersih(ev.get("description", "")))
            ikon = "🚨" if "abuse" in f"{judul} {desk}".lower() else "📌"
            b = f"{ikon} {baru}**{judul}**\n🕒 {fmt(mulai)} ({hitung_mundur(mulai)})"
            if desk:
                b += f"\n> {desk}"
            blok.append(b)

        # batasi panjang supaya total semua embed tidak melewati batas Discord
        bagian, total = [], 0
        for i, b in enumerate(blok):
            if total + len(b) > 550:
                bagian.append(f"…dan {len(blok) - i} event lainnya")
                break
            bagian.append(b)
            total += len(b) + 2
        teks = "\n\n".join(bagian)

    embeds.append({"title": f"🎮 {nama}", "url": link, "description": teks,
                   "color": 0x5865F2 if akan else 0x4F545C})


# ---------- kirim / edit pesan ----------
# Discord: maks 10 embed per pesan -> bagi embeds jadi beberapa pesan
# (pesan 1 = 10 game pertama, pesan 2 = sisanya; state simpan message_id_2)
def upsert_pesan(key, chunk, lanjutan):
    payload = {
        "content": f"📅 **Rekap event** (WIB / GMT+7) • diperbarui {fmt(now)}"
                   + (" (lanjutan)" if lanjutan else ""),
        "embeds": chunk,
    }
    mid = state.get(key)
    if mid:  # edit pesan yang sudah ada
        r = requests.patch(f"{WEBHOOK}/messages/{mid}", json=payload, timeout=15)
        print(f"edit {key}:", r.status_code)
        if r.status_code < 300:
            return mid, True
        if r.status_code == 404:   # pesan sudah dihapus, buat baru
            mid = None
        else:
            print("Gagal edit:", r.text[:200])
    if not mid:  # kirim pesan pertama (atau pengganti kalau pesan lama dihapus)
        r = requests.post(WEBHOOK, params={"wait": "true"}, json=payload, timeout=15)
        print(f"kirim baru {key}:", r.status_code, r.text[:200])
        if r.status_code < 300:
            return r.json()["id"], True
    return mid, False


chunks = [embeds[i:i + 10] for i in range(0, len(embeds), 10)]
ids_baru = {}
ok_semua = True
for i, chunk in enumerate(chunks):
    key = "message_id" if i == 0 else f"message_id_{i + 1}"
    mid_baru, ok = upsert_pesan(key, chunk, lanjutan=(i > 0))
    if mid_baru:
        ids_baru[key] = mid_baru
    ok_semua = ok_semua and ok

# hapus pesan lanjutan kalau tidak lagi dibutuhkan (game berkurang ke <= 10)
if len(chunks) == 1 and state.get("message_id_2"):
    r = requests.delete(f"{WEBHOOK}/messages/{state['message_id_2']}", timeout=15)
    print("hapus pesan lanjutan:", r.status_code)

if ok_semua:
    state_baru = {"message_id": ids_baru.get("message_id"), "seen": seen_baru}
    if ids_baru.get("message_id_2"):
        state_baru["message_id_2"] = ids_baru["message_id_2"]
    json.dump(state_baru, open(STATE_FILE, "w"))
