"""Dispara el envío programado del informe empresarial (Render Cron)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    base = (os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("MT_PUBLIC_URL") or "").rstrip("/")
    secret = os.environ.get("REPORT_CRON_SECRET", "")
    if not base:
        print("RENDER_EXTERNAL_URL o MT_PUBLIC_URL requerido", file=sys.stderr)
        return 1
    if not secret:
        print("REPORT_CRON_SECRET requerido", file=sys.stderr)
        return 1

    url = f"{base}/api/reportes/cron/semanal"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "X-Report-Cron-Secret": secret,
            "User-Agent": "MasterTotal-Cron/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(body)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {}
            if resp.status >= 300 or (isinstance(data, dict) and data.get("ok") is False):
                return 1
            return 0
    except urllib.error.HTTPError as e:
        print(e.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"Error de red: {e.reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
