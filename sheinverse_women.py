import time
import json
import re
import requests
from typing import Dict, List, Tuple, Optional
from datetime import datetime

# ================= CONFIG =================

BOT_TOKEN = "8578215997:AAH7lqbFUQbYHh7gm4vkRHkg2tZP0Q7AO7s"

BASE_API = "https://www.sheinindia.in/api/category/sverse-5939-37961"
POLL_INTERVAL_SEC = 10          # Check every 10 seconds
SUMMARY_INTERVAL_SEC = 1200     # Send status every 20 minutes (20 * 60)
STATE_FILE = "sheinverse_state.json"

# ---- Alert filters (optional) ----
WATCH_KEYWORDS: List[str] = []      # e.g. ["hoodie", "dress"]
EXCLUDE_KEYWORDS: List[str] = []    # e.g. ["kids", "pet"]
MIN_PRICE: Optional[float] = None   # e.g. 500.0
MAX_PRICE: Optional[float] = None   # e.g. 1500.0

# =========================================

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ---------- Telegram helpers ----------

def send_telegram_message(text: str, chat_id: int | str) -> None:
    """Send a message to a specific chat."""
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"❌ Telegram error: {e}")


def broadcast_to_subscribers(text: str, subscribers: List[int]) -> None:
    """Send a message to all subscribed chats."""
    if not subscribers:
        print("🐺 No wolves in the pack yet – nothing to broadcast.")
        return

    for chat_id in subscribers:
        send_telegram_message(text, chat_id)


# ---------- State ----------

def load_state() -> Dict:
    """Load tracking state from JSON file."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}

    state.setdefault("seen_products", {})
    state.setdefault("last_total_results", 0)
    state.setdefault("last_summary_time", 0)
    state.setdefault("total_new_detected", 0)   # all new products seen
    state.setdefault("total_new_alerted", 0)    # new products that triggered alerts
    state.setdefault("last_update_id", 0)       # for Telegram commands
    state.setdefault("subscribers", [])         # all chat_ids that joined the pack

    # ensure subscribers are ints
    state["subscribers"] = [int(c) for c in state.get("subscribers", [])]

    return state


def save_state(state: Dict) -> None:
    """Save tracking state to JSON file."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ---------- API fetch ----------

def fetch_page(page: int = 0) -> Dict:
    """Fetch single page from API."""
    params = {
        "query": ":newn",
        "currentPage": page,
    }
    r = requests.get(BASE_API, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_all_products() -> Tuple[List[Dict], int]:
    """Fetch all products across all pages."""
    first = fetch_page(0)
    products = first.get("products", [])
    pagination = first.get("pagination", {})
    total_results = int(pagination.get("totalResults", 0))
    total_pages = int(pagination.get("totalPages", 1))

    for page in range(1, total_pages):
        try:
            data = fetch_page(page)
            products.extend(data.get("products", []))
        except Exception as e:
            print(f"⚠️ Error fetching page {page}: {e}")
            break

    return products, total_results


def extract_product_key(prod: Dict) -> str:
    """Extract unique product identifier."""
    return str(prod.get("code", ""))


# ---------- Price, link & filters ----------

def parse_price(price_str: str) -> Optional[float]:
    """
    Simple price parser:
    - strips currency symbols and other non-digit/non-dot characters
    - handles commas (e.g. '₹1,234.00' -> 1234.00)
    """
    if not price_str:
        return None

    cleaned = re.sub(r"[^\d.,]", "", price_str)
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_price(prod: Dict) -> Tuple[Optional[float], str]:
    """Return numeric price and formatted price string."""
    price_info = prod.get("price") or {}
    price_str = str(price_info.get("displayformattedValue") or "")
    numeric = parse_price(price_str)
    return numeric, price_str


def extract_product_link(prod: Dict) -> str:
    """Build full product link."""
    url_path = prod.get("url", "")
    if url_path.startswith("/"):
        return f"https://www.sheinindia.in{url_path}"
    elif url_path:
        return url_path
    else:
        return "N/A"


def product_matches_filters(prod: Dict, numeric_price: Optional[float]) -> bool:
    """Check if product passes keyword and price filters for alerts."""
    name = (prod.get("name") or "").lower()

    if WATCH_KEYWORDS:
        lowered_watch = [kw.lower() for kw in WATCH_KEYWORDS]
        if not any(kw in name for kw in lowered_watch):
            return False

    if EXCLUDE_KEYWORDS:
        lowered_exclude = [kw.lower() for kw in EXCLUDE_KEYWORDS]
        if any(kw in name for kw in lowered_exclude):
            return False

    if MIN_PRICE is not None:
        if numeric_price is None or numeric_price < MIN_PRICE:
            return False

    if MAX_PRICE is not None:
        if numeric_price is None or numeric_price > MAX_PRICE:
            return False

    return True


# ---------- Stock handling ----------

def extract_stock_status(prod: Dict) -> Tuple[str, Optional[bool]]:
    """
    Try to determine stock status from various possible fields.
    Returns (label, in_stock_bool_or_None).
    """
    in_stock: Optional[bool] = None

    if "inStock" in prod:
        in_stock = bool(prod["inStock"])
    elif "isInStock" in prod:
        in_stock = bool(prod["isInStock"])
    elif "soldOut" in prod:
        in_stock = not bool(prod["soldOut"])
    elif "isSoldOut" in prod:
        in_stock = not bool(prod["isSoldOut"])
    elif "stock" in prod:
        val = prod["stock"]
        try:
            qty = int(val)
            in_stock = qty > 0
        except Exception:
            pass
    elif "stockQuantity" in prod:
        try:
            qty = int(prod["stockQuantity"])
            in_stock = qty > 0
        except Exception:
            pass
    elif "availableStock" in prod:
        try:
            qty = int(prod["availableStock"])
            in_stock = qty > 0
        except Exception:
            pass
    elif "availability" in prod:
        av = str(prod["availability"]).lower()
        if "in" in av and "stock" in av:
            in_stock = True
        elif "out" in av and "stock" in av:
            in_stock = False

    if in_stock is True:
        label = "In stock"
    elif in_stock is False:
        label = "Out of stock"
    else:
        label = "Unknown"

    return label, in_stock


# ---------- Message formatting (wolf theme) ----------

def new_product_message(prod: Dict) -> str:
    """Wolf‑themed message for NEW product."""
    name = prod.get("name", "Unknown Product")
    numeric_price, price_str = extract_price(prod)
    price_display = price_str or "N/A"
    link = extract_product_link(prod)
    stock_label, _ = extract_stock_status(prod)

    return (
        "🐺 <b>NEW PREY ENTERED THE HUNT</b>\n\n"
        f"🧥 <b>Target:</b> {name}\n"
        f"💰 <b>Price:</b> {price_display}\n"
        f"📦 <b>Stock:</b> {stock_label}\n\n"
        f"🌐 <b>Trail:</b> {link}"
    )


def price_drop_message(
    prod: Dict,
    old_price_str: str,
    new_price_str: str,
    drop_amount: float,
    drop_pct: float,
) -> str:
    """Wolf‑themed message for PRICE DROP."""
    name = prod.get("name", "Unknown Product")
    link = extract_product_link(prod)
    stock_label, _ = extract_stock_status(prod)

    return (
        "🐺 <b>PREY PRICE IS WEAKENING</b>\n\n"
        f"🧥 <b>Target:</b> {name}\n"
        f"💰 <b>Old Price:</b> {old_price_str or 'N/A'}\n"
        f"💰 <b>New Price:</b> {new_price_str or 'N/A'}\n"
        f"📉 <b>Drop:</b> {drop_amount:.2f} ({drop_pct:.1f}%)\n"
        f"📦 <b>Stock:</b> {stock_label}\n\n"
        f"🌐 <b>Trail:</b> {link}"
    )


def get_categorical_summary(products: List[Dict]) -> str:
    """Wolf‑themed categorical breakdown of products with stock info."""
    categories: Dict[str, int] = {}

    for prod in products:
        category = "Other"

        if "category" in prod:
            category = prod["category"]
        elif "categoryName" in prod:
            category = prod["categoryName"]
        elif "categories" in prod and prod["categories"]:
            category = (
                prod["categories"][0]
                if isinstance(prod["categories"], list)
                else str(prod["categories"])
            )

        categories[category] = categories.get(category, 0) + 1

    summary = "🐺 <b>PACK TERRITORY SCAN</b>\n\n"
    summary += f"🌙 <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    total = sum(categories.values())
    summary += f"📦 <b>Total prey in territory:</b> {total}\n\n"

    if categories:
        summary += "<b>Prey by zone:</b>\n"
        for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
            percentage = (count / total * 100) if total > 0 else 0
            summary += f"  • {cat}: {count} ({percentage:.1f}%)\n"

    # Stock status summary
    available = 0
    out_of_stock = 0
    unknown = 0
    for prod in products:
        _, in_stock = extract_stock_status(prod)
        if in_stock is True:
            available += 1
        elif in_stock is False:
            out_of_stock += 1
        else:
            unknown += 1

    summary += "\n<b>Prey health (stock):</b>\n"
    summary += f"  • Ready to hunt: {available}\n"
    summary += f"  • Already taken: {out_of_stock}\n"
    if unknown:
        summary += f"  • Unknown scent: {unknown}\n"

    return summary


def build_status_message(state: Dict, total_results: int, subscriber_count: int) -> str:
    """Wolf‑themed /status response."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    total_seen = len(state.get("seen_products", {}))
    total_new_detected = state.get("total_new_detected", 0)
    total_new_alerted = state.get("total_new_alerted", 0)

    last_summary_ts = state.get("last_summary_time", 0)
    last_summary_str = (
        datetime.fromtimestamp(last_summary_ts).strftime('%Y-%m-%d %H:%M:%S')
        if last_summary_ts
        else "never"
    )

    filters_parts = []
    if WATCH_KEYWORDS:
        filters_parts.append(f"include: {', '.join(WATCH_KEYWORDS)}")
    if EXCLUDE_KEYWORDS:
        filters_parts.append(f"exclude: {', '.join(EXCLUDE_KEYWORDS)}")
    if MIN_PRICE is not None:
        filters_parts.append(f"min price: {MIN_PRICE}")
    if MAX_PRICE is not None:
        filters_parts.append(f"max price: {MAX_PRICE}")
    filters_text = "; ".join(filters_parts) if filters_parts else "none"

    msg = (
        "🐺 <b>PACK SYSTEM STATUS</b>\n\n"
        f"🌙 <b>Time:</b> {now}\n"
        f"🌐 <b>Prey in latest sweep:</b> {total_results}\n"
        f"👁 <b>Distinct prey seen (all time):</b> {total_seen}\n"
        f"🆕 <b>New prey seen (all time):</b> {total_new_detected}\n"
        f"📣 <b>Howls sent (alerts, all time):</b> {total_new_alerted}\n"
        f"🐾 <b>Wolves in pack (subscribers):</b> {subscriber_count}\n"
        f"📊 <b>Last territory report:</b> {last_summary_str}\n\n"
        f"🔍 <b>Hunt filters:</b> {filters_text}"
    )
    return msg


def build_new_products_list(state: Dict, limit: int = 10) -> str:
    """Wolf‑themed list of last N new products with link and stock."""
    seen_products = state.get("seen_products", {})
    if not seen_products:
        return "🐺 The pack has not seen any prey yet."

    items = sorted(
        seen_products.items(),
        key=lambda kv: kv[1].get("first_seen", 0),
        reverse=True,
    )

    msg_lines = [
        f"🐺 <b>Last {min(limit, len(items))} fresh prey spotted by the pack</b>\n"
    ]

    for i, (code, info) in enumerate(items[:limit], start=1):
        name = info.get("name", "Unknown")
        url = info.get("url", "N/A")
        stock_label = info.get("last_stock_label", "Unknown")
        ts = info.get("first_seen", 0)
        time_str = (
            datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
            if ts
            else "unknown time"
        )
        msg_lines.append(
            f"{i}. <b>{name}</b>\n"
            f"   📦 Stock: {stock_label}\n"
            f"   🕐 First scent: {time_str}\n"
            f"   🌐 Trail: {url}\n"
        )

    return "\n".join(msg_lines)


# ---------- Telegram commands (multi‑user) ----------

def handle_telegram_commands(
    last_update_id: int,
    products: List[Dict],
    total_results: int,
    state: Dict,
    subscribers: List[int],
) -> Tuple[int, List[int]]:
    """
    Poll Telegram for commands and respond.
    Works for all chats; maintains a subscriber list.
    Returns (new_last_update_id, new_subscribers).
    """
    params = {"timeout": 0}
    if last_update_id:
        params["offset"] = last_update_id + 1

    try:
        r = requests.get(f"{TELEGRAM_API_URL}/getUpdates", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"⚠️ Telegram getUpdates error: {e}")
        return last_update_id, subscribers

    if not data.get("ok", True):
        print(f"⚠️ Telegram getUpdates returned not ok: {data}")
        return last_update_id, subscribers

    for update in data.get("result", []):
        last_update_id = update.get("update_id", last_update_id)

        message = update.get("message") or update.get("edited_message")
        if not message:
            continue

        chat_id = message.get("chat", {}).get("id")
        if chat_id is None:
            continue

        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            continue

        # Handle /cmd@BotName as /cmd
        first_token = text.split()[0]
        cmd = first_token.split("@")[0].lower()

        if cmd == "/start":
            if chat_id not in subscribers:
                subscribers.append(chat_id)
            welcome = (
                "🐺 <b>WELCOME TO THE PACK</b>\n\n"
                "You are now linked to the Shein Sverse hunt.\n"
                "The pack will howl when new prey appears or prices fall.\n\n"
                "Commands:\n"
                "  • /status – pack system status\n"
                "  • /summary – territory scan now\n"
                "  • /new – last fresh prey\n"
                "  • /stop – leave the pack"
            )
            send_telegram_message(welcome, chat_id)

        elif cmd == "/stop":
            if chat_id in subscribers:
                subscribers.remove(chat_id)
            bye = (
                "🐺 <b>YOU LEAVE THE PACK</b>\n\n"
                "The howls will no longer reach you.\n"
                "Call /start again to rejoin the hunt."
            )
            send_telegram_message(bye, chat_id)

        elif cmd == "/ping":
            send_telegram_message("🐺 The pack is awake and hunting.", chat_id)

        elif cmd == "/status":
            status = build_status_message(state, total_results, subscriber_count=len(subscribers))
            send_telegram_message(status, chat_id)

        elif cmd == "/summary":
            summary = get_categorical_summary(products)
            summary += (
                f"\n\n🆕 <b>New prey seen:</b> {state.get('total_new_detected', 0)}\n"
                f"📣 <b>Howls sent:</b> {state.get('total_new_alerted', 0)}"
            )
            send_telegram_message(summary, chat_id)

        elif cmd in ("/new", "/latest"):
            msg = build_new_products_list(state, limit=10)
            send_telegram_message(msg, chat_id)

        elif cmd == "/help":
            help_msg = (
                "🐺 <b>PACK COMMANDS</b>\n\n"
                "/start – Join the pack and start receiving howls\n"
                "/stop – Leave the pack\n"
                "/ping – Check if the pack is awake\n"
                "/status – See hunt statistics\n"
                "/summary – Force a territory scan\n"
                "/new – See last fresh prey spotted\n"
                "/help – Show this menu"
            )
            send_telegram_message(help_msg, chat_id)

    return last_update_id, subscribers


# ---------- Main loop ----------

def main_loop():
    """Main monitoring loop."""
    state = load_state()
    seen_products = state.get("seen_products", {})
    last_summary_time = state.get("last_summary_time", 0)
    total_new_detected = state.get("total_new_detected", 0)
    total_new_alerted = state.get("total_new_alerted", 0)
    last_update_id = state.get("last_update_id", 0)
    subscribers: List[int] = state.get("subscribers", [])

    startup_msg = (
        "🐺 <b>WOLF ENGINE HOWLING ONLINE</b>\n\n"
        "The pack is now watching the Shein Sverse territory.\n"
        f"⏱ Hunt interval: {POLL_INTERVAL_SEC}s\n"
        f"📊 Territory report: every {SUMMARY_INTERVAL_SEC//60} minutes\n\n"
        "Use /start in this bot chat to join the pack."
    )
    if subscribers:
        broadcast_to_subscribers(startup_msg, subscribers)
    print("🐺 Wolf engine started.")

    while True:
        try:
            print(f"🔍 Scanning territory... [{datetime.now().strftime('%H:%M:%S')}]")
            products, total_results = fetch_all_products()
        except Exception as e:
            print(f"❌ Fetch error: {e}")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        now = time.time()

        current_codes = set()
        new_products_found: List[Dict] = []

        for prod in products:
            key = extract_product_key(prod)
            if not key:
                continue

            current_codes.add(key)
            numeric_price, price_str = extract_price(prod)
            link = extract_product_link(prod)
            stock_label, in_stock = extract_stock_status(prod)

            if key not in seen_products:
                # New product
                seen_products[key] = {
                    "first_seen": now,
                    "name": prod.get("name", "Unknown"),
                    "last_price": numeric_price,
                    "last_price_str": price_str,
                    "url": link,
                    "last_stock_label": stock_label,
                    "in_stock": in_stock,
                }
                new_products_found.append(prod)
                total_new_detected += 1
            else:
                # Existing product: check price change
                stored = seen_products[key]
                old_price = stored.get("last_price")
                old_price_str = stored.get("last_price_str", "")

                if numeric_price is not None and old_price is not None and numeric_price < old_price:
                    if product_matches_filters(prod, numeric_price):
                        drop_amount = old_price - numeric_price
                        drop_pct = (drop_amount / old_price * 100) if old_price else 0.0
                        msg = price_drop_message(
                            prod,
                            old_price_str=old_price_str,
                            new_price_str=price_str,
                            drop_amount=drop_amount,
                            drop_pct=drop_pct,
                        )
                        broadcast_to_subscribers(msg, subscribers)
                        print(f"📉 Price drop howl sent: {prod.get('name', 'Unknown')}")

                # Update stored price and stock info
                stored["last_price"] = numeric_price
                stored["last_price_str"] = price_str
                stored["url"] = link
                stored["last_stock_label"] = stock_label
                stored["in_stock"] = in_stock

        # Filter which new products should actually send alerts
        alert_products: List[Dict] = []
        for prod in new_products_found:
            numeric_price, _ = extract_price(prod)
            if product_matches_filters(prod, numeric_price):
                alert_products.append(prod)

        # Send notifications for filtered new products
        for prod in alert_products:
            msg = new_product_message(prod)
            broadcast_to_subscribers(msg, subscribers)
            total_new_alerted += 1
            print(f"✅ New prey howl sent: {prod.get('name', 'Unknown')}")

        # Handle Telegram commands (multi-user)
        last_update_id, subscribers = handle_telegram_commands(
            last_update_id=last_update_id,
            products=products,
            total_results=total_results,
            state=state,
            subscribers=subscribers,
        )

        # Periodic summary
        if now - last_summary_time >= SUMMARY_INTERVAL_SEC:
            summary = get_categorical_summary(products)
            summary += (
                f"\n\n🆕 <b>New prey seen (all time):</b> {total_new_detected}\n"
                f"📣 <b>Howls sent (all time):</b> {total_new_alerted}"
            )
            broadcast_to_subscribers(summary, subscribers)
            last_summary_time = now
            print("📊 Pack territory report howled.")

        # Save state
        state["seen_products"] = seen_products
        state["last_total_results"] = total_results
        state["last_summary_time"] = last_summary_time
        state["total_new_detected"] = total_new_detected
        state["total_new_alerted"] = total_new_alerted
        state["last_update_id"] = last_update_id
        state["subscribers"] = subscribers
        save_state(state)

        print(
            f"💾 State saved | Total prey: {len(current_codes)} | "
            f"New this run: {len(new_products_found)} | Howls this run: {len(alert_products)} | "
            f"Pack size: {len(subscribers)}"
        )

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\n\n⛔ Wolf engine stopped by handler")
    except Exception as e:
        print(f"\n\n💥 Fatal error in den: {e}")
