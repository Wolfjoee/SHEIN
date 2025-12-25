import time
import json
import requests
from typing import Dict, List

# ================= CONFIG =================

BOT_TOKEN = "8012443558:AAFXDhAfkUGMPSiEJZIvBSBJadfDPQO92WM"
CHAT_ID = "6410261917"

BASE_API = "https://www.sheinindia.in/api/category/sverse-5939-37961"
POLL_INTERVAL_SEC = 10          # API poll interval
SUMMARY_INTERVAL_SEC = 60       # Send total every 1 minute
STATE_FILE = "sheinverse_state.json"

# =========================================


def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print("Telegram error:", e)


def load_state() -> Dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "seen_products": {},
            "last_total_results": 0,
            "last_summary_time": 0
        }


def save_state(state: Dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def fetch_page(page: int = 0) -> Dict:
    params = {
        "query": ":newn",   # ✅ BOTH MEN + WOMEN
        "currentPage": page,
    }
    r = requests.get(BASE_API, params=params, timeout=5)
    r.raise_for_status()
    return r.json()


def fetch_all_products() -> tuple[List[Dict], int]:
    first = fetch_page(0)
    products = first.get("products", [])
    pagination = first.get("pagination", {})
    total_results = int(pagination.get("totalResults", 0))
    total_pages = int(pagination.get("totalPages", 1))

    for page in range(1, total_pages):
        try:
            data = fetch_page(page)
            products.extend(data.get("products", []))
        except Exception:
            break

    return products, total_results


def extract_product_key(prod: Dict) -> str:
    return str(prod.get("code", ""))


def product_to_message(prod: Dict, event_type: str = "NEW") -> str:
    price = prod["price"]["displayformattedValue"]
    url_path = prod.get("url", "")
    link = f"https://www.sheinindia.in{url_path}" if url_path.startswith("/") else url_path

    return (
        f"<b>{event_type}</b>\n"
        f"{link}\n"
        f"<b>Price:</b> {price}"
    )


def main_loop():
    state = load_state()
    seen_products = state.get("seen_products", {})
    last_total_results = state.get("last_total_results", 0)
    last_summary_time = state.get("last_summary_time", 0)

    # 🔥 Startup message
    send_telegram_message("🔥 <b>HI WOLF — START HUNTING</b> 🐺")

    while True:
        try:
            products, total_results = fetch_all_products()
        except Exception as e:
            print("Fetch error:", e)
            time.sleep(POLL_INTERVAL_SEC)
            continue

        now = time.time()

        # 🔔 Every 1 minute summary
        if now - last_summary_time >= SUMMARY_INTERVAL_SEC:
            send_telegram_message(f"📦 <b>PRODUCTS AVAILABLE:</b> {total_results}")
            last_summary_time = now

        # 🆕 New product detection
        current_codes = set()
        for prod in products:
            key = extract_product_key(prod)
            if not key:
                continue

            current_codes.add(key)

            if key not in seen_products:
                seen_products[key] = {"first_seen": time.time()}
                send_telegram_message(product_to_message(prod, "NEW"))

        state["seen_products"] = seen_products
        state["last_total_results"] = total_results
        state["last_summary_time"] = last_summary_time
        save_state(state)

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main_loop()
