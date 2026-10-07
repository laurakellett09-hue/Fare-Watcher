"""
Fare Watch - checks flight prices from Melbourne, scores each fare 0-100,
and emails you any fare that scores at or above your minimum (default 80).

Runs on GitHub Actions every 2 hours (see .github/workflows/check-fares.yml).

Fare data: Travelpayouts / Aviasales Data API (free token).
  Note: this API returns prices from a cache of recent searches made by
  other travellers, not a live airline quote. Always confirm the price
  on the booking site before you pay.

Secrets (set in GitHub > Settings > Secrets and variables > Actions):
  TRAVELPAYOUTS_TOKEN  - your free Travelpayouts API token
  GMAIL_ADDRESS        - the Gmail account that sends the alert
  GMAIL_APP_PASSWORD   - a Gmail "app password" (not your normal password)
  ALERT_TO             - where alerts go (can be the same Gmail)

Run locally:
  python fare_watch.py            # real check (needs TRAVELPAYOUTS_TOKEN)
  python fare_watch.py --demo     # fills the app with made-up sample fares
  python fare_watch.py --no-email # real check, but don't send email
"""

import json
import os
import random
import smtplib
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
HISTORY_PATH = DATA_DIR / "history.json"
ALERTED_PATH = DATA_DIR / "alerted.json"
DEALS_PATH = DATA_DIR / "deals.json"

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
BOOKING_BASE = "https://www.aviasales.com"

MAX_MONTHS = 6            # cap how many months are searched per run
HISTORY_DAYS = 45         # how far back "usual fare" looks
MIN_SAMPLES = 5           # fewer data points than this = low confidence, no alert
REALERT_DROP = 0.97       # re-alert a deal only if it gets 3%+ cheaper
DEALS_KEPT = 300          # how many scored fares the app receives

# Score weights (total 100)
W_PRICE = 60      # price compared with the usual fare for that route + month
W_AIRLINE = 15    # preferred / neutral airline
W_STOPS = 15      # direct beats stopovers
W_DURATION = 10   # total travel time compared with the fastest option

AIRLINES = {
    "QF": "Qantas", "VA": "Virgin Australia", "JQ": "Jetstar", "ZL": "Rex",
    "NZ": "Air New Zealand", "SQ": "Singapore Airlines", "TR": "Scoot",
    "CX": "Cathay Pacific", "EK": "Emirates", "QR": "Qatar Airways",
    "EY": "Etihad", "MH": "Malaysia Airlines", "D7": "AirAsia X",
    "AK": "AirAsia", "QZ": "Indonesia AirAsia", "GA": "Garuda Indonesia",
    "TG": "Thai Airways", "VN": "Vietnam Airlines", "VJ": "VietJet",
    "NH": "ANA", "JL": "Japan Airlines", "KE": "Korean Air", "OZ": "Asiana",
    "CI": "China Airlines", "BR": "EVA Air", "PR": "Philippine Airlines",
    "5J": "Cebu Pacific", "FJ": "Fiji Airways", "UA": "United", "AA": "American",
    "DL": "Delta", "AC": "Air Canada", "HA": "Hawaiian", "LA": "LATAM",
    "MU": "China Eastern", "CZ": "China Southern", "CA": "Air China",
    "AI": "Air India", "UL": "SriLankan", "TK": "Turkish Airlines",
    "BA": "British Airways", "LH": "Lufthansa", "AF": "Air France",
    "KL": "KLM", "3K": "Jetstar Asia", "GK": "Jetstar Japan", "TT": "Tigerair",
    "NF": "Air Vanuatu", "SB": "Aircalin", "IE": "Solomon Airlines",
}


# ---------------------------------------------------------------- helpers

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=1, ensure_ascii=False))


def airline_name(code):
    return AIRLINES.get(code, code or "Unknown")


def month_list(date_from, date_to):
    """'2026-11', '2027-03' -> ['2026-11', ..., '2027-03'], never in the past."""
    today = date.today()
    y, m = map(int, date_from.split("-"))
    ey, em = map(int, date_to.split("-"))
    if (y, m) < (today.year, today.month):
        y, m = today.year, today.month
    months = []
    while (y, m) <= (ey, em) and len(months) < MAX_MONTHS:
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return months


def dest_code(d):
    return d["code"] if isinstance(d, dict) else str(d)


def dest_name(d):
    return d.get("name", d["code"]) if isinstance(d, dict) else str(d)


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------- fetching

def fetch_offers(token, origin, dest, month, trip, currency):
    """One API call -> list of normalised offers."""
    params = {
        "origin": origin,
        "destination": dest,
        "departure_at": month,
        "one_way": "true" if trip == "one_way" else "false",
        "direct": "false",
        "sorting": "price",
        "unique": "false",
        "currency": currency,
        "limit": 200,
        "page": 1,
        "token": token,
    }
    url = API_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept-Encoding": "identity",
                                               "User-Agent": "fare-watch/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read().decode("utf-8"))
    if not payload.get("success", True):
        raise RuntimeError(payload.get("error") or "API returned success=false")
    return [normalise(o, trip) for o in payload.get("data", []) if o.get("price")]


def normalise(o, trip):
    dep = parse_dt(o.get("departure_at"))
    ret = parse_dt(o.get("return_at")) if trip == "return" else None
    link = o.get("link") or ""
    return {
        "price": float(o["price"]),
        "airline": o.get("airline") or "",
        "flight_number": str(o.get("flight_number") or ""),
        "depart": dep.date().isoformat() if dep else None,
        "depart_time": dep.strftime("%H:%M") if dep else None,
        "return": ret.date().isoformat() if ret else None,
        "stops": int(o.get("transfers") or 0),
        "return_stops": int(o.get("return_transfers") or 0) if trip == "return" else 0,
        "duration": int(o.get("duration") or 0),   # minutes, total
        "link": (BOOKING_BASE + link) if link.startswith("/") else link,
    }


def demo_offers(origin, dest, month, trip):
    """Made-up fares for --demo mode, so you can see the app working."""
    rnd = random.Random(f"{dest}{month}{trip}{datetime.now(timezone.utc):%Y%m%d%H}")
    base = {"SYD": 120, "BNE": 150, "OOL": 140, "PER": 260, "HBA": 90, "CNS": 230,
            "DPS": 520, "SIN": 560, "NRT": 820, "HND": 850, "AKL": 330, "NAN": 480,
            "BKK": 640, "HKG": 700, "LAX": 1300, "LHR": 1700}.get(dest, 600)
    if trip == "return":
        base *= 1.75
    airlines = ["QF", "VA", "JQ", "SQ", "TR", "NZ", "D7", "EK"]
    y, m = map(int, month.split("-"))
    out = []
    for _ in range(rnd.randint(14, 30)):
        d = date(y, m, rnd.randint(1, 28))
        stops = rnd.choice([0, 0, 0, 1, 1, 2])
        price = base * rnd.uniform(0.62, 1.45) * (1 - 0.08 * stops)
        ret = d + timedelta(days=rnd.randint(4, 18)) if trip == "return" else None
        dur = int((90 + base * 0.6) * (1 + 0.45 * stops) * (2 if trip == "return" else 1))
        out.append({
            "price": round(price), "airline": rnd.choice(airlines),
            "flight_number": str(rnd.randint(10, 999)),
            "depart": d.isoformat(), "depart_time": f"{rnd.randint(6, 21):02d}:{rnd.choice(['05', '30', '45'])}",
            "return": ret.isoformat() if ret else None,
            "stops": stops, "return_stops": stops if trip == "return" else 0,
            "duration": dur, "link": "",
        })
    return out


# ---------------------------------------------------------------- scoring

def score_offer(o, usual, fastest, cfg, confidence):
    savings = (usual - o["price"]) / usual if usual else 0.0
    # usual price or dearer = 0 pts ... 40%+ cheaper than usual = full points.
    # A direct flight on a neutral airline needs roughly 30%+ off to reach 80.
    price_pts = max(0.0, min(1.0, savings / 0.40)) * W_PRICE

    prefs = [a.upper() for a in cfg.get("preferred_airlines", [])]
    if prefs:
        airline_pts = W_AIRLINE if o["airline"].upper() in prefs else W_AIRLINE * 0.5
    else:
        airline_pts = W_AIRLINE * 0.75          # no preference set = neutral

    worst_stops = max(o["stops"], o["return_stops"])
    stops_pts = {0: 1.0, 1: 0.6}.get(worst_stops, 0.2) * W_STOPS

    if o["duration"] and fastest:
        dur_pts = min(1.0, fastest / o["duration"]) * W_DURATION
    else:
        dur_pts = W_DURATION * 0.5

    total = round(price_pts + airline_pts + stops_pts + dur_pts)
    return {
        "score": int(max(0, min(100, total))),
        "usual": round(usual),
        "savings_pct": round(savings * 100),
        "confidence": confidence,
        "breakdown": {
            "price": round(price_pts), "airline": round(airline_pts),
            "stops": round(stops_pts), "duration": round(dur_pts),
        },
    }


def usual_fare(history_days, batch_prices):
    """Median of recent daily medians plus today's batch median."""
    points = list(history_days.values())
    if batch_prices:
        points.append(statistics.median(batch_prices))
    samples = len(points) + max(0, len(batch_prices) - 1)
    if not points:
        return None, "low"
    # With little history, lean on the spread of today's fares instead.
    if len(points) < 3 and len(batch_prices) >= MIN_SAMPLES:
        u = statistics.median(batch_prices)
    else:
        u = statistics.median(points)
    return u, ("ok" if samples >= MIN_SAMPLES else "low")


def trip_ok(o, trip, cfg):
    if cfg.get("max_price") and o["price"] > float(cfg["max_price"]):
        return False
    if o["airline"].upper() in [a.upper() for a in cfg.get("avoid_airlines", [])]:
        return False
    if trip == "return":
        if not (o["depart"] and o["return"]):
            return False
        days = (date.fromisoformat(o["return"]) - date.fromisoformat(o["depart"])).days
        if not (cfg.get("return_min_days", 1) <= days <= cfg.get("return_max_days", 60)):
            return False
    if o["depart"] and date.fromisoformat(o["depart"]) < date.today():
        return False
    return True


# ---------------------------------------------------------------- email

def send_email(deals, cfg):
    sender = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("ALERT_TO") or sender
    if not (sender and password):
        print("Email skipped: GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set.")
        return False

    cur = cfg.get("currency", "aud").upper()
    rows = []
    for d in deals:
        when = d["depart"] + (f" → {d['return']}" if d.get("return") else " (one-way)")
        stops = "Direct" if max(d["stops"], d["return_stops"]) == 0 else f"{max(d['stops'], d['return_stops'])} stop(s)"
        link = d.get("link") or d.get("google_link")
        rows.append(
            f"<tr><td style='padding:8px;font-weight:700;font-size:18px'>{d['score']}</td>"
            f"<td style='padding:8px'><b>{d['origin']} → {d['dest_name']}</b><br>{when}<br>"
            f"{d['airline_name']} · {stops}</td>"
            f"<td style='padding:8px;text-align:right'><b>${d['price']:.0f} {cur}</b><br>"
            f"<span style='color:#666'>usually ${d['usual']:.0f} ({d['savings_pct']}% less)</span><br>"
            f"<a href='{link}'>View fare</a></td></tr>"
        )
    html = (
        "<div style='font-family:Arial,sans-serif'>"
        f"<h2 style='margin:0 0 8px'>✈️ {len(deals)} new fare deal(s) scoring {cfg.get('min_score', 80)}+</h2>"
        "<table style='border-collapse:collapse;width:100%'>" + "".join(rows) + "</table>"
        "<p style='color:#666;font-size:12px'>Prices come from a cache of recent searches and can change "
        "quickly. Check the price on the booking site before paying.</p></div>"
    )
    top = deals[0]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = (f"Fare deal {top['score']}/100: {top['origin']}→{top['dest_name']} "
                      f"${top['price']:.0f}" + (f" (+{len(deals) - 1} more)" if len(deals) > 1 else ""))
    msg["From"] = sender
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(sender, password)
        s.sendmail(sender, [to], msg.as_string())
    print(f"Email sent to {to} with {len(deals)} deal(s).")
    return True


def google_link(origin, dest, depart, ret):
    q = f"Flights from {origin} to {dest} on {depart}" + (f" returning {ret}" if ret else " one way")
    return "https://www.google.com/travel/flights?q=" + urllib.parse.quote(q)


# ---------------------------------------------------------------- main

def run(demo=False, send=True):
    cfg = load_json(CONFIG_PATH, {})
    origin = cfg.get("origin", "MEL")
    currency = cfg.get("currency", "aud")
    dests = cfg.get("destinations", [])
    min_score = int(cfg.get("min_score", 80))
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "")

    status = {"requests": 0, "errors": [], "demo": demo}
    if not dests:
        save_json(DEALS_PATH, {"updated": datetime.now(timezone.utc).isoformat(),
                               "origin": origin, "currency": currency, "deals": [],
                               "status": {**status, "note": "No destinations set yet. Add some in Settings."}})
        print("No destinations in config.json - nothing to check.")
        return
    if not demo and not token:
        sys.exit("TRAVELPAYOUTS_TOKEN is not set. Add it as a GitHub secret (see SETUP.md).")

    history = load_json(HISTORY_PATH, {})
    alerted = load_json(ALERTED_PATH, {})
    today = date.today().isoformat()
    cutoff = (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()

    scored = []
    for d in dests:
        code = dest_code(d).upper()
        for month in month_list(cfg.get("date_from", today[:7]), cfg.get("date_to", today[:7])):
            for trip in cfg.get("trip_types", ["return", "one_way"]):
                key = f"{code}|{trip}|{month}"
                try:
                    offers = demo_offers(origin, code, month, trip) if demo else \
                        fetch_offers(token, origin, code, month, trip, currency)
                    status["requests"] += 1
                except Exception as e:  # keep going if one route fails
                    status["errors"].append(f"{key}: {e}")
                    print("ERROR", key, e)
                    continue

                offers = [o for o in offers if trip_ok(o, trip, cfg)]
                # keep cheapest per (depart, return, airline)
                best = {}
                for o in offers:
                    k = (o["depart"], o["return"], o["airline"])
                    if k not in best or o["price"] < best[k]["price"]:
                        best[k] = o
                offers = list(best.values())
                if not offers:
                    continue

                prices = [o["price"] for o in offers]
                days = {k: v for k, v in history.get(key, {}).items() if k >= cutoff and k != today}
                usual, conf = usual_fare(days, prices)
                days[today] = round(statistics.median(prices))
                history[key] = days
                if not usual:
                    continue
                fastest = min((o["duration"] for o in offers if o["duration"]), default=0)

                for o in offers:
                    s = score_offer(o, usual, fastest, cfg, conf)
                    scored.append({
                        **o, **s,
                        "origin": origin, "dest": code, "dest_name": dest_name(d),
                        "trip": trip, "month": month,
                        "airline_name": airline_name(o["airline"]),
                        "google_link": google_link(origin, code, o["depart"], o["return"]),
                        "id": f"{code}|{trip}|{o['depart']}|{o['return']}|{o['airline']}",
                    })

    # drop history for routes no longer watched
    watched = {f"{dest_code(d).upper()}|" for d in dests}
    history = {k: v for k, v in history.items() if any(k.startswith(w) for w in watched)}

    scored.sort(key=lambda x: (-x["score"], x["price"]))

    # Alerts: new 80+ deals, or old ones that got cheaper
    new_alerts = []
    for s in scored:
        if s["score"] < min_score or s["confidence"] != "ok":
            continue
        prev = alerted.get(s["id"])
        if prev is None or s["price"] <= prev["price"] * REALERT_DROP:
            new_alerts.append(s)
    new_ids = {s["id"] for s in new_alerts}
    for s in scored:
        s["new"] = s["id"] in new_ids

    if new_alerts and send and not demo:
        try:
            if send_email(new_alerts[:15], cfg):
                for s in new_alerts[:15]:   # the rest go out next check
                    alerted[s["id"]] = {"price": s["price"], "at": today, "depart": s["depart"]}
        except Exception as e:
            status["errors"].append(f"email: {e}")
            print("EMAIL ERROR", e)
    alerted = {k: v for k, v in alerted.items() if (v.get("depart") or today) >= today}

    save_json(DEALS_PATH, {
        "updated": datetime.now(timezone.utc).isoformat(),
        "origin": origin, "currency": currency, "min_score": min_score,
        "deals": scored[:DEALS_KEPT], "status": {**status, "alerts_sent": len(new_alerts) if not demo else 0},
    })
    if not demo:
        save_json(HISTORY_PATH, history)
        save_json(ALERTED_PATH, alerted)
    top = sum(1 for s in scored if s["score"] >= min_score)
    print(f"Checked {status['requests']} searches, scored {len(scored)} fares, "
          f"{top} at {min_score}+, {len(new_alerts)} new.")


if __name__ == "__main__":
    run(demo="--demo" in sys.argv, send="--no-email" not in sys.argv)
