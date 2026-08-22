"""
Telegram poster v4.2
- Uses ButtonsStore for fully customizable button layout
- parse_mode is HTML (matching n8n workflow, supports <b>, <blockquote>, etc.)
- Product URL goes in the CAPTION (not as a button)
- Events use the SAME 3 buttons as products
- Auto-retry without parse_mode on Telegram parse errors
"""
import json
import requests
from buttons_store import ButtonsStore


class TelegramPoster:
    def __init__(self, bot_token, chat_id, channel_url=None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.channel_url = channel_url or self._chat_id_to_url(chat_id)
        self.buttons_store = ButtonsStore()

    @staticmethod
    def _chat_id_to_url(chat_id):
        if chat_id and chat_id.startswith("@"):
            return f"https://t.me/{chat_id[1:]}"
        return "https://t.me/"

    def test_connection(self):
        try:
            r = requests.get(f"{self.base_url}/getMe", timeout=10)
            data = r.json()
            if data.get("ok"):
                return True, data["result"]
            return False, data.get("description", "Unknown error")
        except Exception as e:
            return False, str(e)

    def _build_keyboard(self):
        """Reload from disk each time so GUI changes take effect immediately."""
        self.buttons_store = ButtonsStore(self.buttons_store.file_path)
        return {"inline_keyboard": self.buttons_store.build_keyboard()}

    def post_product(self, photo_path, caption, product_url=None):
        """Post product photo with HTML caption + configured promotional buttons."""
        return self._post_photo(photo_path, caption)

    def post_event(self, photo_path, caption, event_url=None, button_text=None):
        """Post event photo with HTML caption + same buttons as products."""
        return self._post_photo(photo_path, caption)

    def _post_photo(self, photo_path, caption):
        url = f"{self.base_url}/sendPhoto"
        keyboard = self._build_keyboard()
        try:
            with open(photo_path, "rb") as f:
                data = {
                    "chat_id": self.chat_id,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(keyboard, ensure_ascii=False),
                }
                r = requests.post(url, data=data, files={"photo": f}, timeout=60)
                resp = r.json()

            if resp.get("ok"):
                return True, resp["result"]["message_id"]

            # Retry without parse_mode if Telegram rejects HTML
            err_desc = resp.get("description", "").lower()
            if "parse" in err_desc or "html" in err_desc or "entities" in err_desc:
                data_plain = {
                    "chat_id": self.chat_id,
                    "caption": caption,
                    "reply_markup": json.dumps(keyboard, ensure_ascii=False),
                }
                with open(photo_path, "rb") as f2:
                    r2 = requests.post(url, data=data_plain,
                                       files={"photo": f2}, timeout=60)
                    resp2 = r2.json()
                    if resp2.get("ok"):
                        return True, resp2["result"]["message_id"]
                    return False, resp2.get("description", "Unknown error")

            return False, resp.get("description", "Unknown error")

        except Exception as e:
            return False, str(e)

    def post_text(self, text):
        try:
            r = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=15,
            )
            return r.json().get("ok", False)
        except Exception:
            return False
