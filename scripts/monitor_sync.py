#!/usr/bin/env python3
"""
Monitor de sincronización (consola) — métricas del servidor.

Nota: el outbox real del POS (`pending_sync`) vive en IndexedDB/Dexie del navegador.
Este script consulta la base del servidor: changelog reciente + nodos de backup.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

console = Console()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def flask_online(host: str, port: int) -> bool:
    ping_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{ping_host}:{port}/login"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def get_server_sync_stats(empresa_id: int = 1, window_minutes: int = 5) -> dict:
    from app.database import get_db, table_exists

    stats = {
        "changelog_total": 0,
        "changelog_recent": 0,
        "nodos": 0,
        "empresa_id": empresa_id,
    }
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

    with get_db(empresa_id) as conn:
        if table_exists(conn, "sync_changelog"):
            row = conn.execute("SELECT COUNT(*) AS c FROM sync_changelog").fetchone()
            stats["changelog_total"] = int(row["c"] if row else 0)

            recent_rows = conn.execute(
                """
                SELECT updated_at_utc, created_at
                FROM sync_changelog
                ORDER BY id DESC
                LIMIT 200
                """
            ).fetchall()
            recent = 0
            for r in recent_rows:
                ts = _parse_ts(r["updated_at_utc"]) or _parse_ts(r["created_at"])
                if ts and ts >= cutoff:
                    recent += 1
            stats["changelog_recent"] = recent

        if table_exists(conn, "sync_nodos"):
            row = conn.execute("SELECT COUNT(*) AS c FROM sync_nodos").fetchone()
            stats["nodos"] = int(row["c"] if row else 0)

    return stats


def get_outbox_status(empresa_id: int = 1) -> int:
    """
    Proxy de actividad de sync en servidor (changelog reciente).
    El outbox del cliente (pending_sync) solo existe en el navegador.
    """
    return get_server_sync_stats(empresa_id)["changelog_recent"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor de sync POS (servidor)")
    parser.add_argument("--empresa", type=int, default=1, help="empresa_id (default: 1)")
    parser.add_argument("--interval", type=float, default=2.0, help="segundos entre refrescos")
    args = parser.parse_args()

    from app.config import Config

    try:
        while True:
            online = flask_online(Config.HOST, Config.PORT)
            stats = get_server_sync_stats(args.empresa)
            items = stats["changelog_recent"]

            table = Table(title="[bold yellow]Estado de Sincronización POS[/bold yellow]")
            table.add_column("Empresa", justify="center", style="blue")
            table.add_column("Changelog reciente", justify="center", style="cyan")
            table.add_column("Changelog total", justify="center", style="dim")
            table.add_column("Nodos backup", justify="center", style="dim")
            table.add_column("Servidor Flask", justify="center", style="magenta")

            if not online:
                conn_label = "[red]Offline[/red]"
                sync_label = "[dim]—[/dim]"
            elif items == 0:
                conn_label = "[green]Online[/green]"
                sync_label = "[green]Sincronizado[/green]"
            else:
                conn_label = "[green]Online[/green]"
                sync_label = f"[yellow]Actividad sync ({items})[/yellow]"

            table.add_row(
                str(args.empresa),
                str(items),
                str(stats["changelog_total"]),
                str(stats["nodos"]),
                conn_label,
            )

            console.clear()
            console.print(table)
            console.print(f"Estado sync: {sync_label}")
            console.print(
                "[dim]Outbox cliente (pending_sync) → Dexie en el navegador, no en SQLite del servidor.[/dim]",
                justify="center",
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        console.print("[bold]Monitoreo detenido.[/bold]")


if __name__ == "__main__":
    main()
