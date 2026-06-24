"""Pub/Sub en memoria para señales SSE por empresa (tenant)."""
from __future__ import annotations

import queue
import threading
from collections import defaultdict
from typing import Any

_lock = threading.Lock()
_subscribers: dict[int, set[queue.Queue[dict[str, Any]]]] = defaultdict(set)


def subscribe(empresa_id: int) -> queue.Queue[dict[str, Any]]:
    q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=64)
    with _lock:
        _subscribers[int(empresa_id)].add(q)
    return q


def unsubscribe(empresa_id: int, q: queue.Queue[dict[str, Any]]) -> None:
    with _lock:
        subs = _subscribers.get(int(empresa_id))
        if not subs:
            return
        subs.discard(q)
        if not subs:
            _subscribers.pop(int(empresa_id), None)


def broadcast_refresh(
    empresa_id: int,
    *,
    source_device_id: str | None = None,
    reason: str = "sync_push",
) -> int:
    """Notifica a todos los clientes SSE de la empresa que refresquen datos."""
    payload: dict[str, Any] = {
        "event": "refrescar",
        "reason": reason,
        "empresa_id": int(empresa_id),
    }
    if source_device_id:
        payload["source_device_id"] = source_device_id

    with _lock:
        targets = list(_subscribers.get(int(empresa_id), ()))

    delivered = 0
    for q in targets:
        try:
            q.put_nowait(payload)
            delivered += 1
        except queue.Full:
            pass
    return delivered


def subscriber_count(empresa_id: int | None = None) -> int:
    with _lock:
        if empresa_id is not None:
            return len(_subscribers.get(int(empresa_id), ()))
        return sum(len(s) for s in _subscribers.values())
