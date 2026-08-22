"""
Arkhashom Cloud Runner — single-pass headless executor for GitHub Actions.
Runs ONE posting cycle per bot, then exits. State files persist via git commit.
"""
import os
import sys
import json
import time
import importlib
import re
from datetime import datetime

from ai_caption import CaptionGenerator
from telegram_poster import TelegramPoster
from posted_history import PostedHistory
from events_store import EventsStore
from promotions_store import PromotionsStore, extract_psp_id, psp_url

DATA_DIR = "data"
SHARED_EVENTS_FILE  = os.path.join(DATA_DIR, "events_store_shared.json")
SHARED_BUTTONS_FILE = os.path.join(DATA_DIR, "buttons_store_shared.json")

# ─── Config from environment ─────────────────────────────────────────────────
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "@arkhashomoffers")
AFFILIATE_TAG     = os.environ.get("AFFILIATE_TAG", "arkhashom-21")
BANNER_PATH       = os.environ.get("BANNER_PATH", "arkhashom_banner.png")
MAX_POSTS_PER_BOT = int(os.environ.get("MAX_POSTS_PER_BOT", "3"))

# Bot tokens mapped by key (from individual secrets)
TOKEN_MAP = {
    "fashion":      os.environ.get("BOT_TOKEN_FASHION", ""),
    "electronics":  os.environ.get("BOT_TOKEN_ELECTRONICS", ""),
    "supermarket":  os.environ.get("BOT_TOKEN_SUPERMARKET", ""),
    "home":         os.environ.get("BOT_TOKEN_HOME", ""),
    "beauty":       os.environ.get("BOT_TOKEN_BEAUTY", ""),
    "promotions":   os.environ.get("BOT_TOKEN_PROMOTIONS", ""),
}


def log(bot_key, msg):
    print(f"[{bot_key}] {msg}")


def run_single_cycle(spec, shared):
    """Run one posting cycle for a single bot. Returns number of posts made."""
    key = spec["key"]
    mode = spec.get("mode", "products")
    token = TOKEN_MAP.get(key, "") or spec.get("telegram_token", "")

    if not token or "PASTE" in token:
        log(key, "⚠️ No token — skipping")
        return 0

    # Override token from secret
    spec["telegram_token"] = token

    os.makedirs(DATA_DIR, exist_ok=True)
    history_file = os.path.join(DATA_DIR, f"posted_history_{key}.json")
    min_discount = int(spec.get("min_discount_pct", 10))
    categories = list(spec.get("categories", []))

    # Build components
    try:
        scraper_mod = importlib.import_module(spec["scraper_module"])
    except Exception as e:
        log(key, f"❌ Import error: {e}")
        return 0

    scraper = scraper_mod.AmazonScraper(
        headless=True,
        affiliate_tag=shared["affiliate_tag"],
        banner_path=shared["banner_path"],
        banner_text=spec.get("banner_text", "Arkhashom Offers"),
        force_arabic=True,
    )
    captioner = CaptionGenerator(shared["gemini_api_key"])
    poster = TelegramPoster(token, shared["telegram_chat_id"])
    poster.buttons_store.file_path = SHARED_BUTTONS_FILE
    poster.buttons_store.config = poster.buttons_store._load()
    history = PostedHistory(history_file)
    events = EventsStore(SHARED_EVENTS_FILE)

    # Test connection
    ok, info = poster.test_connection()
    if not ok:
        log(key, f"❌ Telegram failed: {info}")
        scraper.close()
        return 0
    bot_username = info.get("username", "")
    log(key, f"✅ @{bot_username} connected → {shared['telegram_chat_id']}")

    posted_count = 0

    try:
        if mode == "promotions":
            posted_count = _run_promotions_cycle(
                key, spec, scraper, captioner, poster, history, events, shared, categories)
        else:
            posted_count = _run_products_cycle(
                key, spec, scraper, captioner, poster, history, events, shared, categories, min_discount)
    except Exception as e:
        log(key, f"❌ Error: {e}")
    finally:
        scraper.close()

    log(key, f"✅ Done — posted {posted_count} items")
    return posted_count


def _run_products_cycle(key, spec, scraper, captioner, poster, history, events, shared, categories, min_discount):
    """Single products cycle: discover → post up to MAX_POSTS_PER_BOT items."""
    posted = 0
    screenshot = os.path.join(DATA_DIR, f"post_image_{key}.png")

    # Check events first
    event = events.pick_next_event_to_post()
    if event:
        log(key, f"🎯 Event: {event['title']}")
        try:
            url = event["url"]
            if scraper.capture_branded_screenshot(url, screenshot):
                info = scraper.get_event_info(url)
                info["title"] = event["title"]
                caption = captioner.generate_event(info)
                ok, res = poster.post_event(screenshot, caption, url)
                if ok:
                    log(key, f"   ✅ Event posted (id {res})")
                    events.mark_posted(event["id"])
                    posted += 1
        except Exception as e:
            log(key, f"   ❌ Event error: {e}")

    # Discover products
    if posted < MAX_POSTS_PER_BOT:
        log(key, f"🔄 Discovering across {len(categories)} categories...")
        urls = scraper.discover_product_urls(categories, max_per_category=20)
        fresh = history.filter_unposted(urls)
        log(key, f"   {len(fresh)} fresh candidates")

        opens = 0
        max_opens = 15  # limit Selenium page loads per bot per run
        best_fallback = None

        for url in fresh:
            if posted >= MAX_POSTS_PER_BOT or opens >= max_opens:
                break

            opens += 1
            disc = (scraper.deal_discount_for_url(url)
                    if hasattr(scraper, "deal_discount_for_url") else 0)

            if best_fallback is None or disc > best_fallback[0]:
                best_fallback = (disc, url)

            info = scraper.get_product_info(url)
            if not info.get("title") or not info.get("current_price"):
                continue

            # Calculate discount
            d = str(info.get("discount_pct") or "")
            m = re.search(r"\d+", d)
            pct = int(m.group(0)) if m else 0
            if pct == 0:
                cur = re.sub(r"[^\d]", "", str(info.get("current_price") or ""))
                lst = re.sub(r"[^\d]", "", str(info.get("list_price") or ""))
                if cur and lst and int(lst) > int(cur) > 0:
                    pct = round((1 - int(cur) / int(lst)) * 100)

            if pct < min_discount:
                continue

            log(key, f"   ✅ {info['title'][:55]} [{pct}% off]")

            if not scraper.capture_product_screenshot(url, screenshot):
                if not info.get("image_url") or not scraper.download_image(
                        info["image_url"], screenshot):
                    continue

            if not os.path.exists(screenshot) or os.path.getsize(screenshot) == 0:
                continue

            caption = captioner.generate(info)
            affiliate_url = info.get("affiliate_url") or info.get("url")
            ok, res = poster.post_product(screenshot, caption, affiliate_url)
            if ok:
                log(key, f"   ✅ Posted (id {res})")
                history.mark_posted(url, info["title"])
                posted += 1
                time.sleep(3)

        # Force-post best fallback if nothing cleared the threshold
        if posted == 0 and best_fallback:
            disc, url = best_fallback
            log(key, f"⚡ Force-posting best available ({disc}% off)")
            info = scraper.get_product_info(url)
            if info.get("title") and info.get("current_price"):
                if scraper.capture_product_screenshot(url, screenshot):
                    caption = captioner.generate(info)
                    ok, res = poster.post_product(screenshot, caption,
                                                  info.get("affiliate_url") or url)
                    if ok:
                        history.mark_posted(url, info.get("title", ""))
                        posted += 1

    return posted


def _run_promotions_cycle(key, spec, scraper, captioner, poster, history, events, shared, categories):
    """Single promotions cycle."""
    posted = 0
    screenshot = os.path.join(DATA_DIR, f"post_image_{key}.png")
    store = PromotionsStore(os.path.join(DATA_DIR, f"promotions_{key}.json"))
    reshare_h = int(spec.get("reshare_cooldown_hours", 12))

    # Seed
    for seed in spec.get("seeds", []):
        pid = extract_psp_id(seed)
        if pid:
            _, isnew = store.add(pid, source="seed")
            if isnew:
                log(key, f"🌱 Seeded promotion {pid}")

    # Discovery: scan a few products for promotion links
    if categories:
        try:
            urls = scraper.discover_product_urls(categories[:3], max_per_category=10)
            for purl in urls[:8]:
                try:
                    for pid in scraper.harvest_promotions_from_product(purl):
                        _, isnew = store.add(pid, source="discovered")
                        if isnew:
                            log(key, f"   ✨ New promotion: {pid}")
                except Exception:
                    pass
        except Exception as e:
            log(key, f"❌ Promo discovery: {e}")

    # Post
    pending = store.never_posted()
    target = pending[0] if pending else None
    if not target:
        reshare = store.due_for_reshare(reshare_h)
        if reshare:
            target = reshare[0]
            log(key, "♻️ Resharing an active promotion")

    if target:
        url = target["url"]
        log(key, f"🎟️ Posting promotion {target['id']}")
        info = scraper.get_promotion_info(url)
        title = info.get("title") or target.get("title")
        products = info.get("product_urls") or []

        if not products:
            # Re-try once
            info = scraper.get_promotion_info(url)
            title = info.get("title") or target.get("title")
            products = info.get("product_urls") or []

        if products and title:
            if scraper.capture_branded_screenshot(url, screenshot):
                info["title"] = title
                info["product_count"] = len(products)
                info["affiliate_url"] = psp_url(target["id"], shared["affiliate_tag"])
                caption = captioner.generate_promotion(info)
                ok, res = poster.post_event(screenshot, caption, info["affiliate_url"])
                if ok:
                    log(key, f"   ✅ Promotion posted (id {res})")
                    store.mark_posted(target["id"], title, len(products))
                    posted += 1
        elif not title:
            log(key, "   ⚠️ Empty/expired — deactivating")
            store.deactivate(target["id"])
        else:
            log(key, "   ⏭️ 0 products — skipping")

    return posted


def main():
    print(f"{'='*60}")
    print(f"  ARKHASHOM CLOUD RUNNER")
    print(f"  {datetime.now().isoformat(timespec='seconds')}")
    print(f"  Channel: {TELEGRAM_CHAT_ID}  |  Tag: {AFFILIATE_TAG}")
    print(f"  Max posts/bot: {MAX_POSTS_PER_BOT}")
    print(f"{'='*60}\n")

    # Load bots.json
    with open("bots.json", "r", encoding="utf-8") as f:
        bots_config = json.load(f)

    shared = {
        "gemini_api_key": GEMINI_API_KEY,
        "telegram_chat_id": TELEGRAM_CHAT_ID,
        "affiliate_tag": AFFILIATE_TAG,
        "banner_path": BANNER_PATH,
        "banner_text": "Arkhashom Offers",
        "headless": True,
        "force_arabic": True,
        "random_delay": 8,
        "channel_url": f"https://t.me/{TELEGRAM_CHAT_ID.lstrip('@')}",
    }

    total_posted = 0
    bots = bots_config.get("bots", [])

    for spec in bots:
        key = spec["key"]
        print(f"\n{'─'*50}")
        print(f"  BOT: {spec['name']} ({key})")
        print(f"{'─'*50}")
        try:
            count = run_single_cycle(spec, shared)
            total_posted += count
        except Exception as e:
            log(key, f"❌ FATAL: {e}")
        time.sleep(2)

    print(f"\n{'='*60}")
    print(f"  TOTAL POSTED: {total_posted}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
