"""
Events Store v4.0
Manages user-defined events with scheduling.

Each event:
{
    "id": "evt_abc123",
    "title": "FIFA Fan Store",
    "url": "https://amzn.to/4nMQOOX",   # Used EXACTLY as-is in posts
    "start": "2026-05-23T09:00:00",     # ISO format
    "end":   "2026-05-30T23:59:00",
    "posts_per_day": 4,
    "scrape_products_inside": false,    # Q3 = C
    "post_count_today": 0,
    "last_posted_date": "2026-05-23",
    "total_posts": 0,
    "created_at": "..."
}
"""
import json
import os
import uuid
from datetime import datetime, date


EVENTS_FILE = "events_store.json"


class EventsStore:
    def __init__(self, file_path=EVENTS_FILE):
        self.file_path = file_path
        self.events = self._load()

    def _load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
                    return data.get("events", [])
            except Exception as e:
                print(f"[events_store] Load error: {e}")
        return []

    def _save(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.events, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[events_store] Save error: {e}")

    # --------------------- CRUD ---------------------

    def add_event(self, title, url, start_iso, end_iso,
                  posts_per_day=4, scrape_products_inside=False):
        evt = {
            "id": f"evt_{uuid.uuid4().hex[:8]}",
            "title": title.strip(),
            "url": url.strip(),
            "start": start_iso,
            "end": end_iso,
            "posts_per_day": int(posts_per_day),
            "scrape_products_inside": bool(scrape_products_inside),
            "post_count_today": 0,
            "last_posted_date": None,
            "total_posts": 0,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.events.append(evt)
        self._save()
        return evt

    def update_event(self, event_id, **kwargs):
        for evt in self.events:
            if evt["id"] == event_id:
                for k, v in kwargs.items():
                    if k in ("title", "url", "start", "end",
                             "posts_per_day", "scrape_products_inside"):
                        evt[k] = v
                self._save()
                return evt
        return None

    def delete_event(self, event_id):
        before = len(self.events)
        self.events = [e for e in self.events if e["id"] != event_id]
        if len(self.events) != before:
            self._save()
            return True
        return False

    def get_event(self, event_id):
        for e in self.events:
            if e["id"] == event_id:
                return e
        return None

    def all_events(self):
        return list(self.events)

    # --------------------- SCHEDULING ---------------------

    def get_status(self, event):
        """Return: 'active', 'scheduled', 'expired'"""
        now = datetime.now()
        try:
            start = datetime.fromisoformat(event["start"])
            end = datetime.fromisoformat(event["end"])
        except Exception:
            return "expired"
        if now < start:
            return "scheduled"
        if now > end:
            return "expired"
        return "active"

    def active_events(self):
        return [e for e in self.events if self.get_status(e) == "active"]

    def scheduled_events(self):
        return [e for e in self.events if self.get_status(e) == "scheduled"]

    def expired_events(self):
        return [e for e in self.events if self.get_status(e) == "expired"]

    # --------------------- POST TRACKING ---------------------

    def reset_daily_counter_if_needed(self, event):
        """Reset post_count_today if last_posted_date is not today."""
        today_str = date.today().isoformat()
        if event.get("last_posted_date") != today_str:
            event["post_count_today"] = 0
            event["last_posted_date"] = today_str
            self._save()

    def can_post(self, event):
        """Check if event still has post slots today."""
        if self.get_status(event) != "active":
            return False
        self.reset_daily_counter_if_needed(event)
        return event.get("post_count_today", 0) < event.get("posts_per_day", 0)

    def mark_posted(self, event_id):
        """Increment post counter for an event."""
        for evt in self.events:
            if evt["id"] == event_id:
                self.reset_daily_counter_if_needed(evt)
                evt["post_count_today"] = evt.get("post_count_today", 0) + 1
                evt["total_posts"] = evt.get("total_posts", 0) + 1
                evt["last_posted_date"] = date.today().isoformat()
                self._save()
                return True
        return False

    def pick_next_event_to_post(self):
        """
        Q5 = C: rotate through active events with capacity.
        Returns the active event with fewest posts today (least recent).
        """
        candidates = []
        for e in self.active_events():
            self.reset_daily_counter_if_needed(e)
            if e.get("post_count_today", 0) < e.get("posts_per_day", 0):
                candidates.append(e)
        if not candidates:
            return None
        # Rotate: pick the one with lowest post_count_today
        candidates.sort(key=lambda x: (x.get("post_count_today", 0),
                                       x.get("created_at", "")))
        return candidates[0]


if __name__ == "__main__":
    store = EventsStore()
    print(f"Total events: {len(store.events)}")
    print(f"Active: {len(store.active_events())}")
    print(f"Scheduled: {len(store.scheduled_events())}")
    print(f"Expired: {len(store.expired_events())}")
