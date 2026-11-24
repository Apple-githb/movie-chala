import json
import os
import random
import re
import time
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from curl_cffi import requests

PROXY_URL = os.environ.get("RESIDENTIAL_PROXY_URL", "").strip()
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://in.bookmyshow.com/mumbai/cinemas",
}

MUMBAI_HUB_VENUES = [
    {"code": "JWPB", "id": "maison-bkc", "name": "Maison INOX: Jio World Plaza, BKC", "lat": 19.0672, "lng": 72.8679},
    {"code": "PVDJ", "id": "pvr-dynamic-juhu", "name": "PVR: Dynamic Mall, Juhu", "lat": 19.1124, "lng": 72.8265},
    {"code": "INOM", "id": "inox-megaplex-malad", "name": "INOX: Megaplex, Inorbit Mall Malad", "lat": 19.1759, "lng": 72.8351},
    {"code": "INRC", "id": "inox-rcity", "name": "INOX: R-City, Ghatkopar", "lat": 19.0996, "lng": 72.9164},
    {"code": "PVPK", "id": "pvr-phoenix-kurla", "name": "PVR: Phoenix Marketcity, Kurla", "lat": 19.0865, "lng": 72.8890},
    {"code": "PVPL", "id": "pvr-icon-lowerparel", "name": "PVR ICON: Phoenix Palladium, Lower Parel", "lat": 18.9949, "lng": 72.8245},
    {"code": "CNSW", "id": "cinepolis-seawoods", "name": "Cinépolis: Nexus Seawoods, Navi Mumbai", "lat": 19.0222, "lng": 73.0182},
    {"code": "INRV", "id": "inox-raghuleela-vashi", "name": "INOX: Raghuleela Mall, Vashi", "lat": 19.0645, "lng": 72.9975},
]

def extract_initial_state(html_text: str) -> dict:
    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup.find_all("script"):
        if script.string and "window.__INITIAL_STATE__" in script.string:
            match = re.search(
                r"window\.__INITIAL_STATE__\s*=\s*({.*?});?\s*(?:window\.|</script>|$)",
                script.string,
                re.DOTALL,
            )
            if match:
                return json.loads(match.group(1))
    return {}

def discover_mumbai_venues() -> list[dict]:
    """Discovers live Mumbai venue codes from BookMyShow cinemas page."""
    try:
        resp = requests.get(
            "https://in.bookmyshow.com/mumbai/cinemas",
            headers=HEADERS,
            proxies=PROXIES,
            impersonate="chrome",
            timeout=20,
        )
        state = extract_initial_state(resp.text)
        queries = state.get("fetchVenuesListingApi", {}).get("queries", {})
        venues = []
        for q_data in queries.values():
            for v in q_data.get("data", {}).get("venues", []):
                if v.get("VenueCode") and v.get("VenueLatitude"):
                    venues.append({
                        "code": v.get("VenueCode"),
                        "id": v.get("VenueCode").lower(),
                        "name": v.get("VenueName"),
                        "lat": float(v.get("VenueLatitude")),
                        "lng": float(v.get("VenueLongitude")),
                    })
        if len(venues) >= 10:
            print(f"Discovered {len(venues)} live Mumbai venues.")
            return venues
    except Exception as e:
        print(f"Venue discovery fallback triggered: {e}")
    return MUMBAI_HUB_VENUES

def parse_time_24h(show_time_str: str) -> str:
    """Converts '07:30 PM' -> '19:30'."""
    try:
        dt = datetime.strptime(show_time_str.strip(), "%I:%M %p")
        return dt.strftime("%H:%M")
    except Exception:
        return "19:00"

def fetch_venue_schedule(venue: dict, date_str: str) -> list[dict]:
    url = f"https://in.bookmyshow.com/cinemas/mumbai/x/buytickets/{venue['code']}/{date_str}"
    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            proxies=PROXIES,
            impersonate="chrome",
            allow_redirects=True,
            timeout=20,
        )
        state = extract_initial_state(resp.text)
        queries = state.get("venueShowtimesFunctionalApi", {}).get("queries", {})
        key = f"getShowtimesByVenue-{venue['code']}-{date_str}"
        events = queries.get(key, {}).get("data", {}).get("showDetailsTransformed", {}).get("Event", [])

        grouped = []
        for ev in events:
            title = ev.get("EventTitle", "Unknown Title")
            for child in ev.get("ChildEvents", []):
                lang = child.get("EventLanguage", "Hindi")
                fmt = child.get("EventDimension", "2D")
                times = []
                min_price = 350
                for st in child.get("ShowTimes", []):
                    avail = str(st.get("AvailStatus", "3"))
                    if avail == "0":
                        continue
                    seat_pct = 75 if avail == "3" else 32
                    if st.get("MinPrice"):
                        try:
                            min_price = int(float(st["MinPrice"]))
                        except Exception:
                            pass
                    times.append({
                        "time": parse_time_24h(st.get("ShowTime", "07:00 PM")),
                        "seatPct": seat_pct,
                        "deepLink": resp.url,
                    })
                if times:
                    grouped.append({
                        "id": f"{venue['id']}-{title[:10]}-{fmt}-{date_str}",
                        "date": date_str,
                        "movie": title,
                        "lang": lang,
                        "format": fmt,
                        "venueId": venue["id"],
                        "basePrice": min_price,
                        "times": times,
                    })
        return grouped
    except Exception as e:
        print(f"Skipped {venue['name']} ({date_str}): {e}")
        return []

def main():
    venues = discover_mumbai_venues()
    # Fetch today and tomorrow in IST (UTC + 5:30)
    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    target_dates = [
        ist_now.strftime("%Y%m%d"),
        (ist_now + timedelta(days=1)).strftime("%Y%m%d"),
    ]

    all_shows = []
    # Cap at top 45 theatres per run so GitHub Actions finishes in ~2 mins
    for venue in venues[:45]:
        for d_str in target_dates:
            shows = fetch_venue_schedule(venue, d_str)
            all_shows.extend(shows)
            time.sleep(random.uniform(1.0, 2.2))

    output_path = "public/mumbai_shows.json"
    if all_shows:
        payload = {
            "updated_at_ist": ist_now.strftime("%Y-%m-%d %I:%M %p IST"),
            "source": "live",
            "venues": venues[:45],
            "shows": all_shows,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"Success: Wrote {len(all_shows)} live show groups to {output_path}")
    else:
        print("Warning: 0 live shows fetched (check RESIDENTIAL_PROXY_URL secret). Preserving existing JSON.")

if __name__ == "__main__":
    main()
