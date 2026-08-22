"""
Buttons Store v4.2
Persists configurable Telegram inline buttons (text + URL) for post layouts.

Default buttons match the n8n workflow exactly:
  Row 1: 🏆 عروض كأس العالم
  Row 2: ⚡ أقوى العروض  |  👑 اشترك في برايم

Layouts:
- "1+2"   : one button on top, two on bottom (3 buttons total) — DEFAULT
- "1+1"   : two buttons stacked (2 buttons total)
- "1+2+1" : one top, two middle, one bottom (4 buttons total)

Storage: buttons_store.json
"""
import json
import os


BUTTONS_FILE = os.path.join("data", "buttons_store_shared.json")

DEFAULT_BUTTONS = {
    "layout": "1+2",
    "top": {
        "text": "🏆 عروض كأس العالم",
        "url": "https://amzn.to/43prGEx",
    },
    "bottom_left": {
        "text": "⚡ أقوى العروض",
        "url": "https://amzn.to/4tQQHmQ",
    },
    "bottom_right": {
        "text": "👑 اشترك في برايم",
        "url": "https://amzn.to/4a4taaW",
    },
    "extra_bottom": {
        "text": "🛒 اطلب الآن",
        "url": "https://amzn.to/4nMQOOX",
    },
}


class ButtonsStore:
    def __init__(self, file_path=BUTTONS_FILE):
        self.file_path = file_path
        self.config = self._load()

    def _load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    merged = dict(DEFAULT_BUTTONS)
                    merged.update(data)
                    for k in ("top", "bottom_left", "bottom_right", "extra_bottom"):
                        if k in data and isinstance(data[k], dict):
                            merged[k] = {**DEFAULT_BUTTONS[k], **data[k]}
                    return merged
            except Exception as e:
                print(f"[buttons_store] Load error: {e}")
        return dict(DEFAULT_BUTTONS)

    def save(self):
        try:
            _dir = os.path.dirname(self.file_path)
            if _dir:
                os.makedirs(_dir, exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[buttons_store] Save error: {e}")

    def update(self, layout=None, top=None, bottom_left=None,
               bottom_right=None, extra_bottom=None):
        if layout is not None:
            self.config["layout"] = layout
        if top is not None:
            self.config["top"] = top
        if bottom_left is not None:
            self.config["bottom_left"] = bottom_left
        if bottom_right is not None:
            self.config["bottom_right"] = bottom_right
        if extra_bottom is not None:
            self.config["extra_bottom"] = extra_bottom
        self.save()

    def reset_to_defaults(self):
        self.config = dict(DEFAULT_BUTTONS)
        self.save()

    def build_keyboard(self):
        """Build Telegram inline_keyboard list-of-rows from config."""
        layout = self.config.get("layout", "1+2")
        top = self.config.get("top", DEFAULT_BUTTONS["top"])
        bl  = self.config.get("bottom_left", DEFAULT_BUTTONS["bottom_left"])
        br  = self.config.get("bottom_right", DEFAULT_BUTTONS["bottom_right"])
        eb  = self.config.get("extra_bottom", DEFAULT_BUTTONS["extra_bottom"])

        keyboard = []
        if layout == "1+1":
            keyboard.append([{"text": top["text"], "url": top["url"]}])
            keyboard.append([{"text": bl["text"],  "url": bl["url"]}])
        elif layout == "1+2+1":
            keyboard.append([{"text": top["text"], "url": top["url"]}])
            keyboard.append([
                {"text": bl["text"], "url": bl["url"]},
                {"text": br["text"], "url": br["url"]},
            ])
            keyboard.append([{"text": eb["text"], "url": eb["url"]}])
        else:  # "1+2" — default
            keyboard.append([{"text": top["text"], "url": top["url"]}])
            keyboard.append([
                {"text": bl["text"], "url": bl["url"]},
                {"text": br["text"], "url": br["url"]},
            ])
        return keyboard


if __name__ == "__main__":
    bs = ButtonsStore()
    print(json.dumps(bs.build_keyboard(), ensure_ascii=False, indent=2))
