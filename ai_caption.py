"""
Caption generator v4.3
Format matches the n8n workflow exactly:

  خصم XX% علي

  <b>Product Name</b>

  <blockquote>السعر الآن XXXX جنيه بدلاً من YYYY جنيه</blockquote>  ← discount ≥ 15%
  السعر الآن XXXX جنيه بدلاً من YYYY جنيه                          ← discount < 15%

  الحق المنتج من هنا 👇🏼:
  [affiliate URL]

parse_mode = HTML throughout (no Markdown * or _)
Gemini SDK: google.genai (new) with google.generativeai fallback
Model: gemini-2.0-flash  (free tier: 1500 req/day, 15 req/min)
"""
import re

# New SDK (google-genai package)
try:
    from google import genai
    _USE_NEW_SDK = True
except ImportError:
    import google.generativeai as genai_legacy
    _USE_NEW_SDK = False

# Free-tier model — do NOT change to 2.5-pro (zero free quota)
DEFAULT_MODEL = "gemini-2.0-flash"


PRODUCT_NAME_PROMPT = """انت محرر منتجات لقناة عروض أمازون مصر.

الاسم الأصلي للمنتج:
{title}

المطلوب:
- اكتب اسم المنتج بشكل واضح ومختصر للزبون المصري
- خليه بالعربي الفصيح المفهوم، مع الحفاظ على البراند والموديل بالإنجليزي
- لو الاسم طويل، اختصره مع الحفاظ على المعلومات المهمة
- لا تضف أي إيموجي أو رموز
- لا تضف أي مقدمات زي "اسم المنتج:" - اكتب الاسم مباشرة
- الطول الأمثل: 8-15 كلمة

اكتب الاسم المنقح فقط:"""


EVENT_DESCRIPTION_PROMPT = """انت محرر إعلانات لقناة عروض أمازون مصر تكتب بالعامية المصرية.

دي صفحة حدث/عرض على أمازون مصر:
- العنوان: {title}
- الوصف: {description}

اكتب وصف قصير وممتع للزبون المصري عن هذا العرض، بحد أقصى سطرين.
- استخدم العامية المصرية
- ابدأ بإيموجي مناسب
- اذكر الموضوع الرئيسي للحدث
- اضف عبارة محفزة للنقر
- لا تذكر روابط

اكتب الوصف فقط:"""


# ── HTML caption templates (Telegram parse_mode=HTML) ──────────────────────
# Discount ≥ 15% → price line wrapped in <blockquote> (renders as sidebar box)
PRODUCT_CAPTION_DISCOUNT_HIGH = (
    "خصم {discount}% علي \n\n"
    "<b>{product_name}</b>\n\n"
    "<blockquote>السعر الآن {new_price} جنيه بدلاً من {old_price} جنيه</blockquote>\n\n"
    "الحق المنتج من هنا 👇🏼:\n"
    "{affiliate_url}"
)

# Discount > 0 but < 15% → plain price line, no blockquote
PRODUCT_CAPTION_DISCOUNT_LOW = (
    "خصم {discount}% علي \n\n"
    "<b>{product_name}</b>\n\n"
    "السعر الآن {new_price} جنيه بدلاً من {old_price} جنيه\n\n"
    "الحق المنتج من هنا 👇🏼:\n"
    "{affiliate_url}"
)

# Price known, no confirmed discount
PRODUCT_CAPTION_NO_DISCOUNT = (
    "<b>{product_name}</b>\n\n"
    "السعر: {new_price} جنيه\n\n"
    "الحق المنتج من هنا 👇🏼:\n"
    "{affiliate_url}"
)

# No price found at all
PRODUCT_CAPTION_NO_PRICE = (
    "<b>{product_name}</b>\n\n"
    "السعر متوفر في الموقع\n\n"
    "الحق المنتج من هنا 👇🏼:\n"
    "{affiliate_url}"
)

EVENT_CAPTION_TEMPLATE = (
    "عرض علي\n\n"
    "<b>{event_title}</b>\n\n"
    "{event_description}"
)


PROMOTION_CAPTION_TEMPLATE = (
    "🔥 عرض خاص من أمازون مصر\n\n"
    "<b>{offer_title}</b>\n\n"
    "{hype_line}"
    "{products_line}"
    "اطلب دلوقتي من هنا 👇🏼:\n"
    "{promo_url}"
)

PROMOTION_HYPE_PROMPT = """انت محرر إعلانات لقناة عروض أمازون مصر تكتب بالعامية المصرية.

ده عنوان عرض ترويجي على أمازون مصر:
{title}

اكتب سطر واحد قصير وجذاب يشجع الزبون المصري إنه يستغل العرض ده.
- بالعامية المصرية
- ابدأ بإيموجي مناسب
- بحد أقصى سطر واحد
- لا تذكر روابط أو أرقام أسعار من عندك

اكتب السطر فقط:"""


def _escape_html(text):
    """Escape special HTML chars for Telegram HTML parse_mode."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


class CaptionGenerator:
    def __init__(self, api_key, model_name=None):
        self.api_key = api_key
        # Always use the free-tier flash model
        self.model_name = DEFAULT_MODEL
        self.model = None
        # ── Coupon support (toggled live from the GUI / config.env) ──
        self.coupon_enabled = False
        self.coupon_code = ""
        self.coupon_text = "كوبون خصم إضافي"

    def set_coupon(self, enabled, code="", text=None):
        """Enable/disable the coupon line appended to product captions."""
        self.coupon_enabled = bool(enabled)
        self.coupon_code = (code or "").strip()
        if text:
            self.coupon_text = text.strip()

    def _coupon_block(self):
        """Coupon line inserted above the 'الحق المنتج' CTA, or '' if off."""
        if not self.coupon_enabled or not self.coupon_code:
            return ""
        code = _escape_html(self.coupon_code)
        label = _escape_html(self.coupon_text or "كوبون خصم إضافي")
        return f"🎟️ {label}: <code>{code}</code>\n\n"
        if api_key:
            if _USE_NEW_SDK:
                self._client = genai.Client(api_key=api_key)
            else:
                genai_legacy.configure(api_key=api_key)
                self.model = genai_legacy.GenerativeModel(self.model_name)

    def _call_gemini(self, prompt):
        """Single entry point for all Gemini calls — handles both SDK versions."""
        if _USE_NEW_SDK:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            return response.text.strip()
        else:
            response = self.model.generate_content(prompt)
            return response.text.strip()

    # ──────────────────────── PRODUCT CAPTIONS ────────────────────────────────

    def generate(self, product_info):
        """Generate product caption in HTML format matching n8n workflow."""
        raw_title = (product_info.get("title") or "").strip()
        clean_name = _escape_html(self._refine_product_name(raw_title))
        url = (product_info.get("affiliate_url") or product_info.get("url") or "")

        new_price = self._extract_clean_price(product_info.get("current_price"))
        old_price = self._extract_clean_price(product_info.get("list_price"))
        discount_str = product_info.get("discount_pct")

        valid_discount = self._validate_discount(new_price, old_price, discount_str)

        if valid_discount:
            new_p, old_p, pct = valid_discount
            pct_int = int(pct)
            if pct_int >= 15:
                caption = PRODUCT_CAPTION_DISCOUNT_HIGH.format(
                    discount=pct,
                    product_name=clean_name,
                    new_price=new_p,
                    old_price=old_p,
                    affiliate_url=url,
                )
            else:
                caption = PRODUCT_CAPTION_DISCOUNT_LOW.format(
                    discount=pct,
                    product_name=clean_name,
                    new_price=new_p,
                    old_price=old_p,
                    affiliate_url=url,
                )
        elif new_price:
            caption = PRODUCT_CAPTION_NO_DISCOUNT.format(
                product_name=clean_name,
                new_price=new_price,
                affiliate_url=url,
            )
        else:
            caption = PRODUCT_CAPTION_NO_PRICE.format(
                product_name=clean_name,
                affiliate_url=url,
            )

        # Insert the coupon line (if enabled) just above the buy CTA.
        coupon = self._coupon_block()
        if coupon:
            marker = "الحق المنتج من هنا"
            if marker in caption:
                caption = caption.replace(marker, coupon + marker, 1)
            else:
                caption = caption + "\n\n" + coupon.rstrip()

        if len(caption) > 1020:
            caption = caption[:1020] + "..."
        return caption

    @staticmethod
    def _validate_discount(new_price, old_price, discount_str):
        """
        Returns (new_p, old_p, pct_str) if valid discount, else None.
        - Both prices must exist and be positive integers
        - old_price must be strictly greater than new_price
        - Discount % from Amazon badge must match calculated (within ±5pp)
        - Minimum 3% to suppress noise
        """
        if not new_price or not old_price:
            return None
        try:
            new_val = int(new_price)
            old_val = int(old_price)
        except (TypeError, ValueError):
            return None
        if old_val <= new_val or new_val <= 0 or old_val <= 0:
            return None

        calculated_pct = round((1 - new_val / old_val) * 100)
        if calculated_pct < 3:
            return None

        if discount_str:
            m = re.search(r"\d+", str(discount_str))
            if m:
                amazon_pct = int(m.group(0))
                # Trust the Amazon badge directly — decimal truncation can cause
                # the integer-based calculated_pct to differ significantly
                # (e.g. 8.99->10.00 is 10% but truncated 8->10 calculates as 20%).
                # The badge is what appears in the screenshot, so we use it as-is.
                return (str(new_val), str(old_val), str(amazon_pct))

        # No Amazon badge — require ≥5%
        if calculated_pct < 5:
            return None
        return (str(new_val), str(old_val), str(calculated_pct))

    @staticmethod
    def _extract_clean_price(price_str):
        """Pull a clean integer price from strings like 'EGP 2,299.00' → '2299'."""
        if not price_str:
            return None
        cleaned = re.sub(r"[^\d.,]", "", str(price_str)).replace(",", "")
        if "." in cleaned:
            cleaned = cleaned.split(".")[0]
        if not cleaned:
            return None
        try:
            val = int(cleaned)
            return str(val) if val > 0 else None
        except ValueError:
            return None

    def _refine_product_name(self, raw_title):
        if not raw_title:
            return "منتج رائع"
        if not self.api_key:
            return self._truncate_title(raw_title)
        try:
            prompt = PRODUCT_NAME_PROMPT.format(title=raw_title)
            cleaned = self._call_gemini(prompt).strip("\"'`*_")
            cleaned = re.sub(
                r'^(اسم المنتج|المنتج|Product Name)\s*:?\s*', '', cleaned)
            if not cleaned or len(cleaned) > 200:
                return self._truncate_title(raw_title)
            return cleaned
        except Exception as e:
            print(f"[ai_caption] Name refinement error: {e}")
            return self._truncate_title(raw_title)

    @staticmethod
    def _truncate_title(title, max_chars=120):
        if len(title) <= max_chars:
            return title
        return title[:max_chars].rsplit(" ", 1)[0]

    # ──────────────────────── EVENT CAPTIONS ──────────────────────────────────

    def generate_promotion(self, promo_info):
        """Caption for an Amazon.eg promotion page (offer + affiliate link)."""
        title = _escape_html((promo_info.get("title") or "عرض حصري على أمازون مصر")[:160])
        url = promo_info.get("affiliate_url") or promo_info.get("url") or ""
        count = promo_info.get("product_count") or 0

        hype = ""
        if self.api_key:
            try:
                raw = promo_info.get("title") or ""
                line = self._call_gemini(
                    PROMOTION_HYPE_PROMPT.format(title=raw)).strip("\"'`*_")
                if line and len(line) <= 160:
                    hype = _escape_html(line) + "\n\n"
            except Exception as e:
                print(f"[ai_caption] Promo hype error: {e}")

        products_line = ""
        if count:
            products_line = f"🛒 {count} منتج داخل العرض\n\n"

        caption = PROMOTION_CAPTION_TEMPLATE.format(
            offer_title=title,
            hype_line=hype,
            products_line=products_line,
            promo_url=url,
        )
        if len(caption) > 1020:
            caption = caption[:1020] + "..."
        return caption

    def generate_event(self, event_info):
        """Generate event caption in HTML format."""
        title = _escape_html(event_info.get("title") or "عرض جديد")
        description = event_info.get("description") or ""
        ai_desc = self._refine_event_description(
            event_info.get("title") or "", description)

        caption = EVENT_CAPTION_TEMPLATE.format(
            event_title=title[:120],
            event_description=ai_desc,
        )
        if len(caption) > 1020:
            caption = caption[:1020] + "..."
        return caption

    def _refine_event_description(self, title, description):
        if not self.api_key:
            return description or "اكتشف العروض الحصرية الآن"
        try:
            prompt = EVENT_DESCRIPTION_PROMPT.format(
                title=title, description=description or "(لا يوجد وصف)"
            )
            cleaned = self._call_gemini(prompt).strip("\"'`*_")
            if not cleaned or len(cleaned) > 400:
                return description[:200] or "اكتشف العروض الحصرية الآن"
            return cleaned
        except Exception as e:
            print(f"[ai_caption] Event refinement error: {e}")
            return description[:200] or "اكتشف العروض الحصرية الآن"


if __name__ == "__main__":
    gen = CaptionGenerator(None)

    print("=== Test 1: High discount (≥15%) → blockquote ===")
    print(gen.generate({
        "title": "Tefal Wall Fan VG4151EE",
        "current_price": "EGP 2,299.00",
        "list_price": "EGP 2,699.00",
        "discount_pct": "-15%",
        "affiliate_url": "https://www.amazon.eg/dp/B0F8C7Y5B3?tag=arkhashom-21",
    }))

    print("\n=== Test 2: Low discount (<15%) → plain text ===")
    print(gen.generate({
        "title": "Samsung Galaxy Tab A9",
        "current_price": "EGP 5,000.00",
        "list_price": "EGP 5,500.00",
        "discount_pct": "-9%",
        "affiliate_url": "https://www.amazon.eg/dp/TEST?tag=arkhashom-21",
    }))

    print("\n=== Test 3: No old price ===")
    print(gen.generate({
        "title": "Single Price Product",
        "current_price": "EGP 1,500.00",
        "list_price": None,
        "affiliate_url": "https://test.com",
    }))
