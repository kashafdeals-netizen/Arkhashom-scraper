"""
Events manager v4.0 - DEPRECATED
The old auto-discovery has been replaced by manual scheduled events
managed through events_store.py and the Event Manager UI.

This file is kept as a thin shim so old imports don't break,
but discover_all_events now returns an empty list.
"""

def discover_all_events(*args, **kwargs):
    """Deprecated. Returns empty list. Use events_store.EventsStore instead."""
    return []


def load_manual_events(*args, **kwargs):
    """Deprecated."""
    return []
