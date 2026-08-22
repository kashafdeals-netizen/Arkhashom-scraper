"""
Amazon.eg scraper v5.0  —  Supermarket / Grocery deals edition
================================================================
Key upgrades over v4.1 (focus: SPEED + DEAL HUNTING):

1. FAST DISCOVERY via `requests` + BeautifulSoup (no browser).
   Listing pages (search / deals / bestsellers) expose price + strike
   price + discount right in the HTML, so we read the discount badge
   off every card, RANK candidates by % off, and surface the best
   deals first — including "hidden gems" beyond bestseller pages.

2. SINGLE Selenium page-load per product.  get_product_info() caches
   the loaded page so capture_product_screenshot() reuses it instead
   of navigating again (the old code loaded each product page TWICE).

3. HARD page-load timeout (no more 5-minute hangs) + eager load
   strategy.

4. No webdriver-manager re-download — Selenium's built-in manager
   resolves the driver once.

5. Category map is now supermarket/grocery + deal focused: coffee,
   tea, juice, chocolate, snacks, nuts, pantry staples, etc.
"""
import os
import re
import time
import random
import requests
from io import BytesIO
from urllib.parse import quote_plus
from PIL import Image, ImageDraw, ImageFont

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except Exception:
    _HAS_BS4 = False

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

AMZ = "https://www.amazon.eg/-/ar"


def _search(keyword):
    return f"{AMZ}/s?k={quote_plus(keyword)}"


CATEGORY_SOURCES = {
    # Broad deal sources
    "deals": [f"{AMZ}/deals", f"{AMZ}/gp/bestsellers/electronics"],
    # ── COMPUTERS ──
    "laptops": [_search("laptop"), _search("لابتوب"), _search("gaming laptop")],
    "pc_desktops": [_search("desktop pc"), _search("كمبيوتر مكتبي"), _search("gaming pc")],
    "monitors": [_search("monitor"), _search("شاشة كمبيوتر"), _search("gaming monitor")],
    # ── PC HARDWARE ──
    "gpu": [_search("graphics card gpu"), _search("كارت شاشة"), _search("rtx geforce")],
    "cpu": [_search("processor cpu"), _search("معالج"), _search("ryzen intel core")],
    "motherboard": [_search("motherboard"), _search("مذربورد لوحة ام")],
    "ram": [_search("ram memory ddr"), _search("رامات ذاكرة")],
    "ssd_storage": [_search("ssd nvme"), _search("هارد ديسك ssd"), _search("hard drive")],
    "power_supply": [_search("power supply psu"), _search("باور سبلاي")],
    "pc_case_cooling": [_search("pc case"), _search("كيسة كمبيوتر"), _search("cpu cooler fan")],
    # ── MOBILES & TABLETS ──
    "mobiles": [_search("mobile phone smartphone"), _search("موبايل هاتف"), _search("samsung iphone")],
    "tablets": [_search("tablet"), _search("تابلت"), _search("ipad")],
    "smartwatches": [_search("smart watch"), _search("ساعة ذكية"), _search("smartwatch")],
    # ── AUDIO & CAMERAS ──
    "cameras": [_search("camera"), _search("كاميرا"), _search("dslr mirrorless")],
    "headphones": [_search("headphones earbuds"), _search("سماعات"), _search("airpods")],
    "tv": [_search("smart tv"), _search("تليفزيون شاشة"), _search("led tv")],
    # ── GAMING ──
    "gaming": [_search("gaming console"), _search("بلايستيشن العاب"), _search("playstation xbox")],
    "gaming_accessories": [_search("gaming keyboard mouse"), _search("لوحة مفاتيح ماوس جيمنج")],
    # ── PERIPHERALS & ACCESSORIES ──
    "keyboard_mouse": [_search("keyboard mouse"), _search("كيبورد ماوس")],
    "printers": [_search("printer"), _search("طابعة"), _search("printer scanner")],
    "networking": [_search("wifi router"), _search("راوتر"), _search("range extender")],
    "storage_drives": [_search("external hard drive"), _search("هارد خارجي"), _search("flash memory usb")],
    "accessories": [_search("phone charger cable"), _search("شاحن كابل"), _search("power bank")],
    "smart_home": [_search("smart home device"), _search("جهاز منزل ذكي"), _search("alexa echo")],
    # Legacy aliases
    "electronics": [f"{AMZ}/gp/bestsellers/electronics", _search("electronics")],
    "computers": [f"{AMZ}/gp/bestsellers/computers", _search("computer")],
}

PRICE_BLOCK_SELECTORS = [
    "#corePriceDisplay_desktop_feature_div",
    "#corePrice_feature_div",
    "#apex_desktop",
    "#priceblock_dealprice",
]

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")


def _price_to_int(text):
    if not text:
        return None
    cleaned = re.sub(r"[^\d.,]", "", str(text)).replace(",", "")
    if "." in cleaned:
        cleaned = cleaned.split(".")[0]
    if not cleaned:
        return None
    try:
        v = int(cleaned)
        return v if v > 0 else None
    except ValueError:
        return None


class AmazonScraper:
    def __init__(self, headless=False, affiliate_tag="arkhashom-21",
                 banner_path="arkhashom_banner.png",
                 banner_text="Arkhashom Offers",
                 force_arabic=True):
        self.headless = headless
        self.affiliate_tag = affiliate_tag
        self.banner_path = banner_path
        self.banner_text = banner_text
        self.force_arabic = force_arabic
        self.driver = None
        self._language_set = False
        self._current_url = None
        self._deal_hints = {}   # asin -> {discount, current_price, list_price, reviews}
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": _UA,
            "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": ("text/html,application/xhtml+xml,application/xml;"
                       "q=0.9,image/webp,*/*;q=0.8"),
        })
        self.session.cookies.set("lc-acbeg", "ar_AE", domain=".amazon.eg")
        self.session.cookies.set("i18n-prefs", "EGP", domain=".amazon.eg")

    def _start_driver(self):
        if self.driver:
            return
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.page_load_strategy = "eager"
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--window-size=1366,1000")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        if self.force_arabic:
            options.add_argument("--lang=ar-EG")
            options.add_experimental_option(
                "prefs", {"intl.accept_languages": "ar-EG,ar,en-US,en"})
        options.add_argument(f"user-agent={_UA}")
        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(30)
        self.driver.set_script_timeout(20)
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        if self.force_arabic and not self._language_set:
            self._set_arabic_language()

    def _safe_get(self, url):
        try:
            self.driver.get(url)
            self._current_url = url
            return True
        except TimeoutException:
            try:
                self.driver.execute_script("window.stop();")
            except Exception:
                pass
            self._current_url = url
            print(f"[scraper] Page-load timeout (partial DOM): {url}")
            return True
        except Exception as e:
            print(f"[scraper] Navigation error: {e}")
            self._current_url = None
            return False

    def _set_arabic_language(self):
        try:
            self.driver.get("https://www.amazon.eg/")
            try:
                self.driver.delete_cookie("lc-acbeg")
            except Exception:
                pass
            for c in [
                {"name": "lc-acbeg", "value": "ar_AE", "domain": ".amazon.eg"},
                {"name": "i18n-prefs", "value": "EGP", "domain": ".amazon.eg"},
            ]:
                try:
                    self.driver.add_cookie(c)
                except Exception:
                    pass
            self._language_set = True
            print("[scraper] Arabic language enforced")
        except Exception as e:
            print(f"[scraper] Could not set Arabic cookies: {e}")

    def close(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        self._current_url = None

    def deal_discount_for_url(self, url):
        """Discovery-card discount hint for a product URL (0 if unknown).
        Lets the orchestrator rank fallback candidates without re-opening."""
        m = re.search(r"/dp/([A-Z0-9]{10})", url or "")
        if not m:
            return 0
        h = self._deal_hints.get(m.group(1)) or {}
        try:
            return int(h.get("discount", 0) or 0)
        except Exception:
            return 0

    def discover_product_urls(self, categories, max_per_category=20):
        """
        Browser-free deal discovery, ranked by DISCOUNT then POPULARITY
        (review count = "most sold"). Card prices + discount are cached in
        self._deal_hints so the posting step never loses a found deal even
        if the product page's own price extraction misses the strike price.
        """
        ranked = []          # (discount, reviews, url)
        seen = set()
        for cat in categories:
            cat = (cat or "").strip().lower()
            sources = CATEGORY_SOURCES.get(cat)
            if not sources:
                print(f"[scraper] Unknown category '{cat}', skipping")
                continue
            print(f"[scraper] Discovering deals: {cat}")
            cat_found = []
            for src in sources:
                for card in self._fetch_cards(src):
                    asin = card["asin"]
                    if asin in seen:
                        continue
                    seen.add(asin)
                    # Remember everything the card told us about this deal
                    self._deal_hints[asin] = {
                        "discount": card["disc"],
                        "current_price": card["cur"],
                        "list_price": card["lst"],
                        "reviews": card["reviews"],
                    }
                    cat_found.append(
                        (card["disc"], card["reviews"],
                         f"https://www.amazon.eg/dp/{asin}"))
            # Best deals first; among equal discounts, most-reviewed first
            cat_found.sort(key=lambda x: (x[0], x[1]), reverse=True)
            cat_found = cat_found[:max_per_category]
            n_deals = sum(1 for d, _, _ in cat_found if d > 0)
            print(f"[scraper]   {len(cat_found)} candidates "
                  f"({n_deals} with visible discount) in {cat}")
            ranked.extend(cat_found)
        ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        result = [u for _, _, u in ranked]
        print(f"[scraper] Total ranked candidates: {len(result)}")
        return result

    def _fetch_cards(self, url, retries=2):
        html = None
        for attempt in range(retries):
            try:
                r = self.session.get(url, timeout=20)
                if r.status_code == 200 and "data-asin" in r.text:
                    html = r.text
                    break
            except Exception as e:
                print(f"[scraper]   requests fetch failed ({attempt+1}): {e}")
            time.sleep(1.2)
        if html is None:
            html = self._fetch_cards_selenium(url)
        if not html:
            return []
        cards = self._parse_cards_bs4(html) if _HAS_BS4 else self._parse_cards_regex(html)
        # JS-rendered pages (e.g. /deals) return nothing from static HTML —
        # render once in the browser and re-parse.
        if not cards:
            rendered = self._fetch_cards_selenium(url)
            if rendered:
                cards = self._parse_cards_bs4(rendered) if _HAS_BS4 \
                    else self._parse_cards_regex(rendered)
        return cards

    def _fetch_cards_selenium(self, url):
        try:
            self._start_driver()
            if not self._safe_get(url):
                return None
            for _ in range(2):
                self.driver.execute_script("window.scrollBy(0, 1200);")
                time.sleep(0.6)
            return self.driver.page_source
        except Exception as e:
            print(f"[scraper]   selenium listing fallback failed: {e}")
            return None

    def _parse_cards_bs4(self, html):
        soup = BeautifulSoup(html, "lxml")
        out = []
        cards = soup.select("div[data-asin]")
        if not cards:
            cards = soup.select("div[data-component-type='s-search-result']")
        for card in cards:
            asin = (card.get("data-asin") or "").strip()
            if not re.fullmatch(r"[A-Z0-9]{10}", asin):
                continue
            cur = lst = None
            price_el = card.select_one(".a-price:not(.a-text-price) .a-offscreen")
            if price_el:
                cur = _price_to_int(price_el.get_text())
            strike_el = card.select_one(
                ".a-price.a-text-price .a-offscreen, "
                "span.a-price[data-a-strike='true'] .a-offscreen")
            if strike_el:
                lst = _price_to_int(strike_el.get_text())
            out.append({"asin": asin, "disc": self._card_discount(card, cur, lst),
                        "cur": cur, "lst": lst,
                        "reviews": self._card_reviews(card)})
        if not out:
            seen = set()
            for a in soup.select("a[href*='/dp/']"):
                m = re.search(r"/dp/([A-Z0-9]{10})", a.get("href") or "")
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    out.append({"asin": m.group(1), "disc": 0, "cur": None,
                                "lst": None, "reviews": 0})
        return out

    @staticmethod
    def _card_reviews(card):
        """Best-effort review COUNT from a search card (popularity = most sold)."""
        best = 0
        try:
            labels = [e.get("aria-label", "") for e in card.select("[aria-label]")]
            for lab in labels:
                # The review-count label looks like "تقييمات 178" or "178 ratings".
                # Skip the star-rating label ("4.4 من 5 نجوم ... التصنيف").
                if re.search(r"تقييم|rating|review", lab, re.I) and "نجوم" not in lab \
                        and "من 5" not in lab and "out of" not in lab.lower():
                    for num in re.findall(r"[\d,]{1,9}", lab):
                        best = max(best, int(num.replace(",", "")))
            if best == 0:
                el = card.select_one("a .a-size-base.s-underline-text, "
                                     ".a-size-base.s-underline-text")
                if el:
                    m = re.search(r"[\d,]+", el.get_text())
                    if m:
                        best = int(m.group(0).replace(",", ""))
        except Exception:
            pass
        return best

    def _parse_cards_regex(self, html):
        out = []
        for m in re.finditer(
                r'data-asin="([A-Z0-9]{10})"([\s\S]{0,2500}?)(?=data-asin=|$)',
                html):
            asin, blob = m.group(1), m.group(2)
            vals = [_price_to_int(p) for p in re.findall(r'a-price-whole">([\d,]+)', blob)]
            vals = [v for v in vals if v]
            disc = 0; cur = lst = None
            if len(vals) >= 2:
                cur, lst = min(vals), max(vals)
                if lst > cur:
                    disc = round((1 - cur / lst) * 100)
            elif vals:
                cur = vals[0]
            rv = re.search(r'([\d,]+)\s*(?:rating|review|تقييم)', blob, re.I)
            reviews = int(rv.group(1).replace(",", "")) if rv else 0
            out.append({"asin": asin, "disc": disc, "cur": cur,
                        "lst": lst, "reviews": reviews})
        return out

    @staticmethod
    def _card_discount(card, cur, lst):
        # Trust the strike-vs-current price first (most reliable signal).
        disc = 0
        if cur and lst and lst > cur:
            disc = round((1 - cur / lst) * 100)
        # Only an EXPLICIT savings badge counts — never free-text "%",
        # which on Amazon cards is usually a credit-card promo or a spec.
        try:
            badge = card.select_one(
                ".savingsPercentage, span[class*='savingsPercentage'], "
                "span[class*='s-coupon'] , .a-badge-text")
            if badge:
                bm = re.search(r'(\d+)\s*%', badge.get_text(" ", strip=True))
                if bm:
                    b = int(bm.group(1))
                    # Sanity: ignore absurd badges unless prices agree
                    if 0 < b <= 95:
                        # Amazon's own displayed badge wins — use it verbatim,
                        # never the strike-vs-price recalculation.
                        disc = b
        except Exception:
            pass
        return disc

    def get_product_info(self, url):
        self._start_driver()
        info = {
            "url": url, "asin": None, "title": None,
            "current_price": None, "list_price": None, "discount_pct": None,
            "rating": None, "review_count": None, "bullets": [],
            "image_url": None, "brand": None, "affiliate_url": None,
        }
        try:
            m = re.search(r"/dp/([A-Z0-9]{10})", url)
            if m:
                info["asin"] = m.group(1)
                info["affiliate_url"] = (
                    f"https://www.amazon.eg/dp/{info['asin']}"
                    f"?tag={self.affiliate_tag}")
            self._safe_get(url)
            try:
                WebDriverWait(self.driver, 12).until(
                    EC.presence_of_element_located((By.ID, "productTitle")))
            except TimeoutException:
                pass
            try:
                info["title"] = self.driver.find_element(
                    By.ID, "productTitle").text.strip()
            except Exception:
                pass
            try:
                img = self.driver.find_element(By.ID, "landingImage")
                info["image_url"] = (img.get_attribute("data-old-hires") or
                                     img.get_attribute("src"))
            except Exception:
                pass
            cur, lst, disc = self._extract_prices_strict()
            info["current_price"] = cur
            info["list_price"] = lst
            info["discount_pct"] = disc
            try:
                info["rating"] = self.driver.find_element(
                    By.ID, "acrPopover").get_attribute("title")
            except Exception:
                pass
            try:
                info["review_count"] = self.driver.find_element(
                    By.ID, "acrCustomerReviewText").text.strip()
            except Exception:
                pass
            try:
                els = self.driver.find_elements(
                    By.CSS_SELECTOR, "#feature-bullets ul li span.a-list-item")
                info["bullets"] = [e.text.strip() for e in els if e.text.strip()][:5]
            except Exception:
                pass
            try:
                info["brand"] = self.driver.find_element(
                    By.ID, "bylineInfo").text.strip()
            except Exception:
                pass
        except Exception as e:
            print(f"[scraper] Error scraping product: {e}")

        # ── Fallback to the discovery card's data so a found deal is never
        #    lost when the product page's own price extraction misses it ──
        hint = self._deal_hints.get(info.get("asin") or "")
        if hint:
            if not _price_to_int(info.get("current_price")) and hint.get("current_price"):
                info["current_price"] = f"EGP {hint['current_price']}"
            if not _price_to_int(info.get("list_price")) and hint.get("list_price"):
                info["list_price"] = f"EGP {hint['list_price']}"
            page_pct = 0
            m = re.search(r"\d+", str(info.get("discount_pct") or ""))
            if m:
                page_pct = int(m.group(0))
            # Only use discovery-card hint discount when page extraction found nothing.
            # Overriding a page-extracted % would make the caption text mismatch the screenshot.
            if page_pct == 0 and hint.get("discount", 0) > 0:
                info["discount_pct"] = f"{hint['discount']}%"
            info["reviews_count_num"] = hint.get("reviews", 0)
        return info

    def _extract_prices_strict(self):
        cur, lst, disc = self._extract_prices_selenium()
        if _price_to_int(cur):
            return cur, lst, disc
        cur, lst, disc = self._extract_prices_from_html()
        if not _price_to_int(cur):
            print("[scraper] WARNING: Could not extract price from page")
        return cur, lst, disc

    def _extract_prices_selenium(self):
        current_price_text = list_price_text = discount_text = None
        block = None
        for sel in PRICE_BLOCK_SELECTORS:
            try:
                block = self.driver.find_element(By.CSS_SELECTOR, sel)
                if block:
                    break
            except Exception:
                continue
        if block is None:
            block = self.driver
        for sel in [".priceToPay .a-offscreen",
                    "span.a-price.priceToPay .a-offscreen",
                    ".reinventPricePriceToPayMargin .a-offscreen",
                    "#priceblock_ourprice", "#priceblock_dealprice",
                    ".a-price .a-offscreen"]:
            try:
                el = block.find_element(By.CSS_SELECTOR, sel)
                txt = (el.get_attribute("textContent") or el.text or "").strip()
                if txt and _price_to_int(txt):
                    current_price_text = txt
                    break
            except Exception:
                continue
        for sel in [".basisPrice .a-offscreen",
                    "span.a-price.a-text-price[data-a-strike='true'] .a-offscreen",
                    "span.a-price.a-text-price .a-offscreen"]:
            try:
                el = block.find_element(By.CSS_SELECTOR, sel)
                txt = (el.get_attribute("textContent") or el.text or "").strip()
                if txt and _price_to_int(txt):
                    list_price_text = txt
                    break
            except Exception:
                continue
        for sel in [".savingsPercentage", "span[class*='savingsPercentage']",
                    ".reinventPricePriceToPayMargin .savingsPercentage",
                    ".a-badge-text", "span[class*='priceBadge']",
                    ".a-color-price .a-text-bold",
                    "[data-a-color='price'] .a-offscreen",
                    ".basisPrice .a-text-strike + span", "td.a-color-price"]:
            try:
                el = block.find_element(By.CSS_SELECTOR, sel)
                txt = (el.text or el.get_attribute("textContent") or "").strip()
                if txt and "%" in txt:
                    discount_text = txt
                    break
            except Exception:
                continue
        if not discount_text:
            try:
                page_src = self.driver.page_source
                for pat in [r'savingsPercentage[^>]*>\s*-?\s*(\d+)\s*%',
                            r'"savingPercent"\s*:\s*"?(\d+)"?', r'-( \d+)%']:
                    bm = re.search(pat, page_src[:80000], re.IGNORECASE)
                    if bm:
                        discount_text = f"-{bm.group(1).strip()}%"
                        break
            except Exception:
                pass
        return self._validate_prices(current_price_text, list_price_text, discount_text)

    def _extract_prices_from_html(self):
        try:
            html = self.driver.page_source
        except Exception:
            return None, None, None

        def parse_num(s):
            cleaned = re.sub(r"[^\d.]", "", str(s))
            return float(cleaned) if cleaned else 0

        price_block = ""
        for pid in ["corePriceDisplay_desktop_feature_div", "apex_desktop",
                    "centerCol", "ppd", "corePrice_feature_div"]:
            m = re.search(r'id="' + pid + r'"[^>]*>([\s\S]{0,15000})', html)
            if m:
                price_block += m.group(1)
        if not price_block:
            price_block = html[:60000]
        current_price_text = None
        wm = re.search(r'class="a-price-whole">([\d,]+)<', price_block)
        if wm:
            fm = re.search(r'class="a-price-fraction">([\d]+)<', price_block)
            raw = wm.group(1).replace(",", "")
            raw += ("." + fm.group(1)) if fm else ""
            if parse_num(raw) > 0:
                current_price_text = "EGP " + raw
        if not current_price_text:
            for pat in [r'"priceAmount":([\d.]+)', r'"buyingPrice":([\d.]+)']:
                m = re.search(pat, html)
                if m and parse_num(m.group(1)) > 0:
                    current_price_text = "EGP " + m.group(1)
                    break
        if not _price_to_int(current_price_text):
            return None, None, None
        cur_val = parse_num(current_price_text)
        min_p, max_p = cur_val * 1.02, cur_val * 5
        list_price_text = None
        for pat in [r'[Ww]as[:\s]*EGP[\s]*([\d,]+\.?\d*)',
                    r'[Ww]as[:\s]+([\d,]+\.?\d*)',
                    r'class="a-text-strike"[^>]*>[\s]*(?:EGP[\s]*)?([\d,]+\.?\d*)',
                    r'"wasPrice"[^>:\s]*[:\s]*([\d,]+\.?\d*)',
                    r'"listPrice"[^>:\s]*[:\s]*"?([\d,]+\.?\d*)',
                    r'\u0643\u0627\u0646[^\d]{0,40}([\d,]+\.?\d*)',
                    r'\u0633\u0639\u0631 \u0627\u0644\u0642\u0627\u0626\u0645\u0629[^\d]{0,50}([\d,]+\.?\d*)']:
            m = re.search(pat, price_block)
            if m:
                c = parse_num(m.group(1).replace(",", ""))
                if min_p <= c <= max_p:
                    list_price_text = "EGP " + str(int(c))
                    break
        if not list_price_text:
            for mw in list(re.finditer(r'class="a-price-whole">([\d,]+)<', price_block))[1:]:
                c = parse_num(mw.group(1).replace(",", ""))
                if min_p <= c <= max_p:
                    list_price_text = "EGP " + str(int(c))
                    break
        discount_text = None
        for disc_pat in [r'savingsPercentage[^>]*>\s*-?\s*(\d+)\s*%',
                         r'"savingPercent"\s*:\s*"?(\d+)"?',
                         r'class="[^"]*badge[^"]*"[^>]*>[^<]*(\d+)\s*%', r'-(\d+)%']:
            bm = re.search(disc_pat, price_block, re.IGNORECASE)
            if bm:
                discount_text = f"-{bm.group(1)}%"
                break
        return self._validate_prices(current_price_text, list_price_text, discount_text)

    def _validate_prices(self, current_price_text, list_price_text, discount_text):
        cur_int = _price_to_int(current_price_text)
        old_int = _price_to_int(list_price_text)
        if not cur_int:
            return None, None, None
        if cur_int and old_int and old_int <= cur_int:
            list_price_text = None
            old_int = None
        if discount_text:
            m = re.search(r"\d+", discount_text)
            if m:
                # Use Amazon's displayed discount badge verbatim — never recompute it.
                discount_text = f"{int(m.group(0))}%"
        if cur_int and old_int and not discount_text:
            calculated_pct = round((1 - cur_int / old_int) * 100)
            if calculated_pct < 5:
                list_price_text = None
            else:
                discount_text = f"{calculated_pct}%"
        return current_price_text, list_price_text, discount_text

    def harvest_promotions_from_product(self, product_url):
        """Open a product page and return the set of PSP promotion ids
        advertised on it (the 'عروض ترويجية / Promotions' links)."""
        import re as _re
        ids = set()
        try:
            self._start_driver()
            self._safe_get(product_url)
            time.sleep(1.6)
            try:
                self.driver.execute_script("window.scrollBy(0, 900);")
                time.sleep(0.5)
            except Exception:
                pass
            html = self.driver.page_source or ""
            for m in _re.findall(r"/promotion/psp/([A-Z0-9]{6,})", html):
                ids.add(m)
        except Exception as e:
            print(f"[scraper] Promo harvest error: {e}")
        return ids

    def get_promotion_info(self, promo_url):
        """Promotion page details: offer title + products inside.
        (Same renderer as events — promotion pages share the layout.)"""
        return self.get_event_info(promo_url)

    def get_event_info(self, event_url):
        self._start_driver()
        info = {"url": event_url, "title": None,
                "description": None, "product_urls": []}
        try:
            self._safe_get(event_url)
            time.sleep(1.5)
            for sel in ["h1", ".event-page-title", ".a-section h1", "title"]:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, sel)
                    t = (el.text or el.get_attribute("textContent") or "").strip()
                    if t and len(t) > 3:
                        info["title"] = t[:120]
                        break
                except Exception:
                    continue
            try:
                meta = self.driver.find_element(
                    By.CSS_SELECTOR, "meta[name='description']")
                info["description"] = (meta.get_attribute("content") or "")[:200]
            except Exception:
                pass
            for _ in range(2):
                self.driver.execute_script("window.scrollBy(0, 1200);")
                time.sleep(0.6)
            urls_found = set()
            for a in self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/dp/']"):
                href = a.get_attribute("href") or ""
                m = re.search(r"/dp/([A-Z0-9]{10})", href)
                if m:
                    urls_found.add(f"https://www.amazon.eg/dp/{m.group(1)}")
            info["product_urls"] = list(urls_found)[:30]
            print(f"[scraper] Event '{info['title']}': "
                  f"{len(info['product_urls'])} products inside")
        except Exception as e:
            print(f"[scraper] Error scraping event: {e}")
        return info

    def capture_branded_screenshot(self, url, save_path="product_screenshot.png",
                                   wait_selector="productTitle"):
        self._start_driver()
        try:
            if self._current_url != url:
                self._safe_get(url)
            try:
                WebDriverWait(self.driver, 8).until(
                    EC.presence_of_element_located((By.ID, wait_selector)))
            except Exception:
                pass
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.6)
            png = self.driver.get_screenshot_as_png()
            final = self._add_branded_footer(Image.open(BytesIO(png)))
            final.save(save_path, "PNG", optimize=True)
            return True
        except Exception as e:
            print(f"[scraper] Screenshot error: {e}")
            return False

    def capture_product_screenshot(self, url, save_path="product_screenshot.png"):
        self._start_driver()
        try:
            if self._current_url != url:
                self._safe_get(url)
                try:
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.ID, "productTitle")))
                except Exception:
                    pass
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.5)
            try:
                title_el = self.driver.find_element(By.ID, "productTitle")
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'start'});", title_el)
                self.driver.execute_script("window.scrollBy(0, -160);")
                time.sleep(0.6)
            except Exception:
                pass
            # Guard: make sure the product image is actually visible on the page.
            # If it's a captcha / login redirect / empty page, skip it.
            try:
                img_el = self.driver.find_element(By.ID, "landingImage")
                if not img_el.is_displayed():
                    print("[scraper] Product image not visible — page may be a redirect/captcha, skipping")
                    return False
            except Exception:
                print("[scraper] Product image element (landingImage) not found — skipping screenshot")
                return False
            png = self.driver.get_screenshot_as_png()
            final = self._add_branded_footer(Image.open(BytesIO(png)))
            final.save(save_path, "PNG", optimize=True)
            return True
        except Exception as e:
            print(f"[scraper] Screenshot error: {e}")
            return False


    def _add_branded_footer(self, screenshot):
        sw, sh = screenshot.size
        banner_height = 100
        banner = None
        if self.banner_path and os.path.exists(self.banner_path):
            try:
                banner = Image.open(self.banner_path).convert("RGB")
                bw, bh = banner.size
                new_h = int(bh * sw / bw)
                banner = banner.resize((sw, new_h), Image.LANCZOS)
                banner_height = new_h
            except Exception:
                banner = None
        if banner is None:
            banner = self._generate_banner(sw, banner_height)
        out = Image.new("RGB", (sw, sh + banner_height), color=(0, 0, 0))
        if screenshot.mode != "RGB":
            screenshot = screenshot.convert("RGB")
        out.paste(screenshot, (0, 0))
        out.paste(banner, (0, sh))
        return out

    def _generate_banner(self, width, height):
        banner = Image.new("RGB", (width, height), color=(15, 15, 15))
        draw = ImageDraw.Draw(banner)
        font = None
        for fp in ["C:\\Windows\\Fonts\\arialbd.ttf", "C:\\Windows\\Fonts\\arial.ttf"]:
            if os.path.exists(fp):
                try:
                    font = ImageFont.truetype(fp, 42)
                    break
                except Exception:
                    pass
        if font is None:
            font = ImageFont.load_default()
        text = self.banner_text
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            tw, th = 600, 36
        draw.text(((width - tw) // 2, (height - th) // 2), text,
                  fill=(255, 153, 0), font=font)
        draw.rectangle([0, 0, 8, height], fill=(255, 153, 0))
        draw.rectangle([width - 8, 0, width, height], fill=(255, 153, 0))
        return banner

    def download_image(self, image_url, save_path):
        if not image_url:
            return False
        try:
            r = self.session.get(image_url, timeout=30)
            r.raise_for_status()
            img = Image.open(BytesIO(r.content))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(save_path, "JPEG", quality=90)
            return True
        except Exception as e:
            print(f"[scraper] Image download error: {e}")
            return False
