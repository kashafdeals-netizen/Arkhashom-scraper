"""
Promotions Store v1.0
Tracks Amazon.eg promotion pages (/promotion/psp/<ID>) the bot has
discovered or that the user seeded manually.

Each promotion record:
{
    "id": "A29WVUNL1DOK0W",                 # the PSP id (primary key)
    "url": "https://www.amazon.eg/promotion/psp/A29WVUNL1DOK0W",
    "title": "وفر 40 جنيه عند شراء 200 ...",  # filled when first posted
    "product_count": 18,
    "source": "discovered" | "seed",
    "first_seen": "2026-06-13T...",
    "last_posted": "2026-06-13T..." | None,
    "total_posts": 0,
    "active": true                            # false once it 404s / expires
}
"""
import json
import os
import re
from datetime import datetime, timedelta

STORE_FILE = "promotions_store.json"
PSP_RE = re.compile(r"/promotion/psp/([A-Z0-9]{6,})")


def extract_psp_id(text):
    """Pull the PSP id from a URL or raw string, else None."""
    if not text:
        return None
    m = PSP_RE.search(str(text))
    if m:
        return m.group(1)
    # bare id?
    s = str(text).strip()
    if re.fullmatch(r"[A-Z0-9]{6,}", s):
        return s
    return None


def psp_url(psp_id, tag=None):
    base = f"https://www.amazon.eg/promotion/psp/{psp_id}"
    return f"{base}?tag={tag}" if tag else base


class PromotionsStore:
    def __init__(self, file_path=STORE_FILE):
        self.file_path = file_path
        self.promos = self._load()

    def _load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else data.get("promotions", [])
            except Exception as e:
                print(f"[promotions_store] Load error: {e}")
        return []

    def _save(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.promos, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[promotions_store] Save error: {e}")

    def get(self, psp_id):
        for p in self.promos:
            if p["id"] == psp_id:
                return p
        return None

    def has(self, psp_id):
        return self.get(psp_id) is not None

    def add(self, psp_id, source="discovered"):
        """Register a newly seen promotion. Returns (record, is_new)."""
        psp_id = extract_psp_id(psp_id) or psp_id
        existing = self.get(psp_id)
        if existing:
            return existing, False
        rec = {
            "id": psp_id,
            "url": psp_url(psp_id),
            "title": None,
            "product_count": 0,
            "source": source,
            "first_seen": datetime.now().isoformat(timespec="seconds"),
            "last_posted": None,
            "total_posts": 0,
            "active": True,
        }
        self.promos.append(rec)
        self._save()
        return rec, True

    def update(self, psp_id, **kwargs):
        rec = self.get(psp_id)
        if rec:
            rec.update(kwargs)
            self._save()
        return rec

    def mark_posted(self, psp_id, title=None, product_count=None):
        rec = self.get(psp_id)
        if not rec:
            return None
        rec["last_posted"] = datetime.now().isoformat(timespec="seconds")
        rec["total_posts"] = rec.get("total_posts", 0) + 1
        if title:
            rec["title"] = title[:160]
        if product_count is not None:
            rec["product_count"] = product_count
        self._save()
        return rec

    def deactivate(self, psp_id):
        rec = self.get(psp_id)
        if rec:
            rec["active"] = False
            self._save()

    # ── Selection helpers ─────────────────────────────────────────────────
    def never_posted(self):
        """Active promotions that have not been posted yet (newest first)."""
        out = [p for p in self.promos if p.get("active") and not p.get("last_posted")]
        out.sort(key=lambda p: p.get("first_seen", ""), reverse=True)
        return out

    def due_for_reshare(self, cooldown_hours=24):
        """Active, already-posted promotions whose cooldown has elapsed,
        least-recently-posted first (for the guaranteed-post fallback)."""
        now = datetime.now()
        out = []
        for p in self.promos:
            if not p.get("active") or not p.get("last_posted"):
                continue
            try:
                last = datetime.fromisoformat(p["last_posted"])
            except Exception:
                last = now - timedelta(days=999)
            if now - last >= timedelta(hours=cooldown_hours):
                out.append(p)
        out.sort(key=lambda p: p.get("last_posted", ""))
        return out

    def counts(self):
        active = [p for p in self.promos if p.get("active")]
        posted = [p for p in active if p.get("last_posted")]
        return {
            "total": len(self.promos),
            "active": len(active),
            "posted": len(posted),
            "pending": len(active) - len(posted),
        }


if __name__ == "__main__":
    s = PromotionsStore()
    print(s.counts())
