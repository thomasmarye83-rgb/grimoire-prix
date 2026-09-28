#!/usr/bin/env python3
"""Grimoire : historique des prix Cardmarket (via MTGJSON), construit chaque nuit.

Usage : python3 tools/prices.py <dossier précédent> <dossier de sortie> [AllIdentifiers] [AllPrices]
Sans fichiers donnés, télécharge AllIdentifiers et AllPrices sur mtgjson.com.

Sortie :
  meta.json            date de mise à jour, jour de départ, nombre de jours
  movers.json          plus fortes hausses et baisses (7 et 30 jours)
  s/<xx>.json          256 morceaux : { clé : { n: nom, p: [centimes|null,…], m: [impressions, rang EDHREC, réserve] } }
La clé d'une carte = nom de la face avant, en minuscules, sans accents.
Le morceau = FNV-1a 32 bits de la clé (UTF-8) modulo 256, en hexadécimal (même calcul côté appli).
Le prix d'un jour = le prix « tendance » Cardmarket de l'impression non foil la moins chère.
"""
import datetime as dt, gzip, json, lzma, math, os, sys, unicodedata, urllib.request
from array import array

BASE = "https://mtgjson.com/api/v5/"
KEEP_DAYS = 400
SKIP_LAYOUTS = {"token", "double_faced_token", "emblem", "art_series", "vanguard", "scheme", "planar"}


def key_of(name):
    front = str(name or "").split(" // ")[0].strip().lower()
    return "".join(ch for ch in unicodedata.normalize("NFD", front) if unicodedata.category(ch) != "Mn")


def shard_of(key):
    h = 0x811C9DC5
    for b in key.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return "%02x" % (h % 256)


def fetch(name, tmp):
    for ext, opener in ((".xz", lzma.open), (".gz", gzip.open)):
        path = os.path.join(tmp, name + ext)
        try:
            if not os.path.exists(path):
                print("Téléchargement", name + ext, flush=True)
                req = urllib.request.Request(BASE + name + ext, headers={"User-Agent": "Grimoire-prices/1.0"})
                with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as f:
                    while True:
                        b = r.read(1 << 20)
                        if not b:
                            break
                        f.write(b)
            return opener(path, "rb")
        except Exception as e:
            print("Échec", name + ext, e, flush=True)
            try:
                os.remove(path)
            except OSError:
                pass
    raise SystemExit("Impossible de télécharger " + name)


def open_any(path):
    if path.endswith(".xz"):
        return lzma.open(path, "rb")
    if path.endswith(".gz"):
        return gzip.open(path, "rb")
    return open(path, "rb")


def iter_data(fh):
    try:
        import ijson
        yield from ijson.kvitems(fh, "data", use_float=True)
    except ImportError:
        yield from json.load(fh)["data"].items()


def main():
    prev_dir, out_dir = sys.argv[1], sys.argv[2]
    tmp = os.environ.get("RUNNER_TEMP") or "/tmp"
    ids_fh = open_any(sys.argv[3]) if len(sys.argv) > 3 else fetch("AllIdentifiers.json", tmp)
    pr_fh = open_any(sys.argv[4]) if len(sys.argv) > 4 else fetch("AllPrices.json", tmp)

    # 1) Impressions papier -> clé de carte
    uuid_key, names, meta = {}, {}, {}
    for uuid, c in iter_data(ids_fh):
        if c.get("isOnlineOnly") or c.get("isOversized") or c.get("isFunny") or c.get("isRebalanced") or c.get("layout") in SKIP_LAYOUTS or "paper" not in (c.get("availability") or ["paper"]):
            continue
        k = key_of(c.get("name"))
        if not k:
            continue
        uuid_key[uuid] = k
        names.setdefault(k, c.get("name"))
        m = meta.setdefault(k, [0, None, 0])
        m[0] = max(m[0], len(c.get("printings") or []))
        r = c.get("edhrecRank")
        if r:
            m[1] = r if m[1] is None else min(m[1], r)
        if c.get("isReserved"):
            m[2] = 1
    print("Impressions :", len(uuid_key), "cartes :", len(names), flush=True)

    # 2) Prix Cardmarket (non foil) par impression, sur la fenêtre de MTGJSON (~90 jours)
    today = dt.date.today()
    base = today - dt.timedelta(days=120)
    span = 122
    by_key = {}
    last_day, first_day = -1, span
    for uuid, p in iter_data(pr_fh):
        k = uuid_key.get(uuid)
        if not k:
            continue
        normal = (((p.get("paper") or {}).get("cardmarket") or {}).get("retail") or {}).get("normal") or {}
        if not normal:
            continue
        a = array("f", [math.nan]) * span
        for d, v in normal.items():
            try:
                i = (dt.date.fromisoformat(d) - base).days
            except ValueError:
                continue
            if 0 <= i < span and v is not None and float(v) >= 0.05:
                a[i] = float(v)
                last_day = max(last_day, i)
                first_day = min(first_day, i)
        # on prolonge le dernier prix connu sur les jours sans relevé
        cur = math.nan
        for i in range(span):
            if a[i] == a[i]:
                cur = a[i]
            else:
                a[i] = cur
        prev = by_key.get(k)
        if prev is None:
            by_key[k] = a
        else:
            for i in range(span):
                x, y = prev[i], a[i]
                if y == y and (x != x or y < x):
                    prev[i] = y
    if last_day < 0:
        raise SystemExit("Aucun prix Cardmarket trouvé")
    last_date = base + dt.timedelta(days=last_day)
    print("Cartes avec prix :", len(by_key), "dernier jour :", last_date, flush=True)

    # 3) Fusion avec l'historique déjà construit
    old_meta = {}
    try:
        with open(os.path.join(prev_dir, "meta.json")) as f:
            old_meta = json.load(f)
    except (OSError, ValueError):
        pass
    old_d0 = dt.date.fromisoformat(old_meta["d0"]) if old_meta.get("d0") else None
    start = base + dt.timedelta(days=first_day)
    d0 = min(old_d0, start) if old_d0 else start
    d0 = max(d0, last_date - dt.timedelta(days=KEEP_DAYS - 1))
    ndays = (last_date - d0).days + 1

    os.makedirs(os.path.join(out_dir, "s"), exist_ok=True)
    shards = {}
    for k in set(by_key) | set(names):
        shards.setdefault(shard_of(k), []).append(k)
    movers = []
    latest = {}
    for sh in ["%02x" % i for i in range(256)]:
        old = {}
        if old_d0:
            try:
                with open(os.path.join(prev_dir, "s", sh + ".json")) as f:
                    old = json.load(f)
            except (OSError, ValueError):
                old = {}
        keys = set(shards.get(sh, [])) | set(old)
        out = {}
        for k in keys:
            series = [None] * ndays
            o = old.get(k)
            if o and old_d0:
                off = (old_d0 - d0).days
                for i, v in enumerate(o.get("p") or []):
                    j = i + off
                    if 0 <= j < ndays:
                        series[j] = v
            a = by_key.get(k)
            if a is not None:
                off = (base - d0).days
                for i in range(span):
                    j = i + off
                    if 0 <= j < ndays and a[i] == a[i]:
                        series[j] = int(round(a[i] * 100))
            if not any(v is not None for v in series):
                continue
            m = meta.get(k) or (o or {}).get("m") or [0, None, 0]
            out[k] = {"n": names.get(k) or (o or {}).get("n") or k, "p": series, "m": m}
            last = series[-1]
            if last:
                def ago(n):
                    j = ndays - 1 - n
                    return series[j] if j >= 0 else None
                w7, w30 = ago(7), ago(30)
                latest[k] = [last, round((last / w7 - 1) * 100, 1) if w7 else None, round((last / w30 - 1) * 100, 1) if w30 else None]
            if last and last >= 200:
                def back(n):
                    j = ndays - 1 - n
                    return series[j] if j >= 0 else None
                w, mo = back(7), back(30)
                movers.append({
                    "n": out[k]["n"], "p": last,
                    "d7": round((last / w - 1) * 100, 1) if w else None,
                    "d30": round((last / mo - 1) * 100, 1) if mo else None,
                    "e": m[1], "r": m[2], "sp": [v for v in series[-30:]],
                })
        with open(os.path.join(out_dir, "s", sh + ".json"), "w") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    def top(field, rev, only_played):
        rows = [x for x in movers if x[field] is not None and (x[field] > 0 if rev else x[field] < 0)
                and (not only_played or (x["e"] and x["e"] <= 20000))]
        rows.sort(key=lambda x: x[field], reverse=rev)
        return rows[:40]

    with open(os.path.join(out_dir, "latest.json"), "w") as f:
        json.dump(latest, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(out_dir, "movers.json"), "w") as f:
        json.dump({
            "up7": top("d7", True, True), "down7": top("d7", False, True),
            "up30": top("d30", True, True), "down30": top("d30", False, True),
        }, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump({"updated": dt.datetime.utcnow().isoformat(timespec="minutes") + "Z", "d0": d0.isoformat(),
                   "days": ndays, "last": last_date.isoformat(), "cards": len(by_key)}, f)
    print("OK :", ndays, "jours depuis", d0, flush=True)


if __name__ == "__main__":
    main()
