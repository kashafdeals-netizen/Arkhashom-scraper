"""
Tracks which products have already been posted to avoid repeats.
Uses a simple JSON file as storage.
"""
import json
import os
from datetime import datetime


class PostedHistory:
    def __init__(self, file_path="posted_history.json"):
        self.file_path = file_path
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"posted": {}, "stats": {"total": 0}}

    def _save(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[history] Save error: {e}")

    def has_posted(self, asin_or_url):
        """Check if a product was already posted."""
        key = self._extract_key(asin_or_url)
        return key in self.data["posted"]

    def mark_posted(self, asin_or_url, title=None, category=None):
        """Record that a product was posted."""
        key = self._extract_key(asin_or_url)
        self.data["posted"][key] = {
            "title": title or "",
            "category": category or "",
            "posted_at": datetime.now().isoformat(),
        }
        self.data["stats"]["total"] = self.data["stats"].get("total", 0) + 1
        self._save()

    def filter_unposted(self, urls):
        """Return only URLs that haven't been posted yet."""
        return [u for u in urls if not self.has_posted(u)]

    def total_posted(self):
        return self.data["stats"].get("total", 0)

    def reset(self):
        """Clear history (use with caution)."""
        self.data = {"posted": {}, "stats": {"total": 0}}
        self._save()

    @staticmethod
    def _extract_key(text):
        """Extract ASIN from URL, or return text as-is."""
        import re
        m = re.search(r"/dp/([A-Z0-9]{10})", text)
        if m:
            return m.group(1)
        return text
