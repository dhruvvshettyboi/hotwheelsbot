# -*- coding: utf-8 -*-
"""
Blinkit Hot Wheels Monitor - Telegram Bot
Railway-ready version: reads BOT_TOKEN from environment variable
"""

import os
import sys
import json
import logging
import traceback
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import requests

# Fix Windows console encoding (only applies locally)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ======================================================
#  BOT TOKEN
#  - On Railway: set this as an environment variable
#  - Locally: paste your token between the quotes below
# ======================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8670341641:AAEoR3EM2UkWg_w2DUK6cPREEXA-ZnJlOO4")

# ------ Internals ------
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "chat_id": None,
    "pincodes": [],
    "keywords": ["hot wheels"],
    "interval_minutes": 30,
    "notified_ids": [],
    "last_check": "Never",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


# ======================================================
#  CONFIG HELPERS
# ======================================================

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            data.setdefault(k, v)
        return data
    return DEFAULT_CONFIG.copy()


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ======================================================
#  PINCODE -> LAT / LON
# ======================================================

def pincode_to_coords(pincode):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"postalcode": pincode, "country": "India", "format": "json", "limit": 1}
    headers = {"User-Agent": "BlinkitHotWheelsBot/1.0 (personal use)"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as exc:
        logger.error("Geocoding failed for %s: %s", pincode, exc)
    return None, None


# ======================================================
#  BLINKIT SEARCH
# ======================================================

BLINKIT_SEARCH_URL = "https://api.blinkit.com/v2/products/search/"

def search_blinkit(lat, lon, keyword):
    headers = {
        "app_client": "consumer",
        "lat": str(lat),
        "lon": str(lon),
        "User-Agent": "Mozilla/5.0 (compatible; BlinkitBot)",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    params = {"q": keyword, "start": 0, "size": 20}
    try:
        resp = requests.get(BLINKIT_SEARCH_URL, headers=headers, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("Blinkit returned HTTP %s for '%s'", resp.status_code, keyword)
    except Exception as exc:
        logger.error("Blinkit request failed: %s", exc)
    return None


def extract_products(data):
    products = []
    if not data:
        return products
    items = data.get("objects", [])
    if not items:
        items = data.get("data", {}).get("products", [])
    if not items and isinstance(data, list):
        items = data
    for item in items:
        product = item.get("product", item)
        name = product.get("name", "").strip()
        price = product.get("price", product.get("offer_price", "N/A"))
        mrp = product.get("mrp", "N/A")
        in_stock = product.get("in_stock", product.get("available", True))
        product_id = str(product.get("id", product.get("product_id", "")))
        if name and in_stock and product_id:
            products.append({"id": product_id, "name": name, "price": price, "mrp": mrp})
    return products


# ======================================================
#  CORE MONITOR LOGIC
# ======================================================

async def check_and_notify(bot):
    config = load_config()
    chat_id = config.get("chat_id")

    if not chat_id:
        logger.info("No chat_id yet -- send /start to your bot first.")
        return

    pincodes = config.get("pincodes", [])
    keywords = config.get("keywords", ["hot wheels"])
    notified_ids = set(config.get("notified_ids", []))
    new_found = set()

    if not pincodes:
        logger.info("No pincodes configured.")
        return

    logger.info("Checking: %d pincodes x %d keywords", len(pincodes), len(keywords))

    for pincode in pincodes:
        lat, lon = pincode_to_coords(pincode)
        if lat is None:
            await bot.send_message(
                chat_id=chat_id,
                text="Could not find coordinates for pincode " + pincode + ". Please check if it is valid.",
            )
            continue

        for keyword in keywords:
            logger.info("  Pincode %s | keyword: %s", pincode, keyword)
            raw = search_blinkit(lat, lon, keyword)
            found = extract_products(raw)
            logger.info("  -> %d in-stock product(s) found", len(found))

            for p in found:
                unique_key = f"{pincode}_{p['id']}"
                new_found.add(unique_key)

                if unique_key not in notified_ids:
                    price_text = ("Rs." + str(p["price"])) if p["price"] != "N/A" else "Price unavailable"
                    mrp_text = (" (MRP: Rs." + str(p["mrp"]) + ")") if p["mrp"] != "N/A" else ""
                    msg = (
                        "Hot Wheels Alert!\n\n"
                        "Pincode: " + pincode + "\n"
                        "Search: " + keyword + "\n"
                        "Product: " + p["name"] + "\n"
                        "Price: " + price_text + mrp_text + "\n\n"
                        "Order on Blinkit: https://blinkit.com"
                    )
                    await bot.send_message(chat_id=chat_id, text=msg)
                    logger.info("  Notified: %s @ %s", p["name"], pincode)

    config["notified_ids"] = list(new_found)
    config["last_check"] = datetime.now().strftime("%d %b %Y, %I:%M %p")
    save_config(config)
    logger.info("Check complete.")


# ======================================================
#  SCHEDULED JOB
# ======================================================

async def scheduled_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Scheduled check triggered.")
    await check_and_notify(context.bot)


# ======================================================
#  BOT COMMANDS
# ======================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    config["chat_id"] = update.effective_chat.id
    save_config(config)
    await update.message.reply_text(
        "Blinkit Hot Wheels Monitor Bot\n\n"
        "Commands:\n"
        "/addpincode 400001 - Add a location to monitor\n"
        "/removepincode 400001 - Remove a location\n"
        "/addkeyword Hot Wheels Ferrari - Add a search term\n"
        "/removekeyword Hot Wheels Ferrari - Remove a search term\n"
        "/setinterval 15 - Check every 15 minutes (restart needed)\n"
        "/status - Show current settings\n"
        "/checknow - Run a check immediately\n\n"
        "Start by adding your pincode:\n"
        "/addpincode 400001"
    )

async def cmd_addpincode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pincode = ctx.args[0].strip() if ctx.args else ""
    if not pincode or not pincode.isdigit() or len(pincode) != 6:
        await update.message.reply_text("Please provide a valid 6-digit PIN code.\nExample: /addpincode 400001")
        return
    config = load_config()
    if pincode in config["pincodes"]:
        await update.message.reply_text("Pincode " + pincode + " is already being monitored.")
        return
    await update.message.reply_text("Verifying pincode " + pincode + "...")
    lat, lon = pincode_to_coords(pincode)
    config["pincodes"].append(pincode)
    save_config(config)
    if lat:
        await update.message.reply_text("Added pincode " + pincode + " (verified OK)\nNow monitoring " + str(len(config["pincodes"])) + " pincode(s).")
    else:
        await update.message.reply_text("Added pincode " + pincode + " but could not verify it.\nNow monitoring " + str(len(config["pincodes"])) + " pincode(s).")

async def cmd_removepincode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pincode = ctx.args[0].strip() if ctx.args else ""
    config = load_config()
    if pincode in config["pincodes"]:
        config["pincodes"].remove(pincode)
        save_config(config)
        await update.message.reply_text("Removed pincode " + pincode + ".")
    else:
        await update.message.reply_text("Pincode " + pincode + " was not in the list.")

async def cmd_addkeyword(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyword = " ".join(ctx.args).strip()
    if not keyword:
        await update.message.reply_text("Usage: /addkeyword Hot Wheels Ferrari")
        return
    config = load_config()
    if keyword.lower() in [k.lower() for k in config["keywords"]]:
        await update.message.reply_text("Keyword already exists: " + keyword)
        return
    config["keywords"].append(keyword)
    save_config(config)
    await update.message.reply_text("Added keyword: " + keyword + "\nNow monitoring " + str(len(config["keywords"])) + " keyword(s).")

async def cmd_removekeyword(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyword = " ".join(ctx.args).strip()
    config = load_config()
    before = len(config["keywords"])
    config["keywords"] = [k for k in config["keywords"] if k.lower() != keyword.lower()]
    if len(config["keywords"]) < before:
        save_config(config)
        await update.message.reply_text("Removed keyword: " + keyword)
    else:
        await update.message.reply_text("Keyword not found: " + keyword)

async def cmd_setinterval(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        minutes = int(ctx.args[0])
        if minutes < 5:
            await update.message.reply_text("Minimum interval is 5 minutes.")
            return
        config = load_config()
        config["interval_minutes"] = minutes
        save_config(config)
        await update.message.reply_text("Interval set to " + str(minutes) + " minutes.\nPlease restart the bot for this to take effect.")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /setinterval 30")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    pincodes = config.get("pincodes") or ["None yet"]
    keywords = config.get("keywords") or ["None yet"]
    interval = config.get("interval_minutes", 30)
    last = config.get("last_check", "Never")
    await update.message.reply_text(
        "Bot Status\n\n"
        "Pincodes: " + ", ".join(pincodes) + "\n"
        "Keywords: " + ", ".join(keywords) + "\n"
        "Interval: Every " + str(interval) + " minutes\n"
        "Last Check: " + last
    )

async def cmd_checknow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    pincodes = config.get("pincodes", [])
    keywords = config.get("keywords", [])
    if not pincodes:
        await update.message.reply_text("No pincodes configured yet. Use /addpincode 400001 first.")
        return
    await update.message.reply_text("Checking " + str(len(pincodes)) + " pincode(s) x " + str(len(keywords)) + " keyword(s)...")
    await check_and_notify(ctx.bot)
    await update.message.reply_text("Check done! You will get alerts above if anything was found.")


# ======================================================
#  STARTUP HOOK
# ======================================================

async def post_init(application: Application):
    config = load_config()
    interval_minutes = config.get("interval_minutes", 30)
    application.job_queue.run_repeating(
        scheduled_job,
        interval=interval_minutes * 60,
        first=10,
        name="blinkit_monitor",
    )
    logger.info("Scheduler started: checking every %d minutes.", interval_minutes)


# ======================================================
#  ENTRY POINT
# ======================================================

def main():
    if BOT_TOKEN == "PASTE_YOUR_TOKEN_HERE":
        print("ERROR: BOT_TOKEN not set. Set it as an environment variable or paste it in bot.py.")
        return

    try:
        config = load_config()
        interval = config.get("interval_minutes", 30)
        print("=" * 55)
        print("  Blinkit Hot Wheels Bot - STARTING")
        print("  Checking every " + str(interval) + " minutes")
        print("  All activity logged to bot.log")
        print("  Press Ctrl+C to stop")
        print("=" * 55)

        app = (
            Application.builder()
            .token(BOT_TOKEN)
            .post_init(post_init)
            .build()
        )

        app.add_handler(CommandHandler("start",         cmd_start))
        app.add_handler(CommandHandler("addpincode",    cmd_addpincode))
        app.add_handler(CommandHandler("removepincode", cmd_removepincode))
        app.add_handler(CommandHandler("addkeyword",    cmd_addkeyword))
        app.add_handler(CommandHandler("removekeyword", cmd_removekeyword))
        app.add_handler(CommandHandler("setinterval",   cmd_setinterval))
        app.add_handler(CommandHandler("status",        cmd_status))
        app.add_handler(CommandHandler("checknow",      cmd_checknow))

        app.run_polling(allowed_updates=Update.ALL_TYPES)

    except Exception as e:
        print("ERROR - Bot crashed: " + str(e))
        logger.error("Bot crashed: %s", traceback.format_exc())
        if sys.platform == "win32":
            input("Press Enter to close...")


if __name__ == "__main__":
    main()
