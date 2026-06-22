#!/usr/bin/env python3
"""Backup de la base de datos Master Total (SQLite o PostgreSQL dump)."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime

from app.config import Config


def backup_sqlite(dest_dir: str) -> str:
    src = Config.DB_PATH
    if not os.path.isfile(src):
        raise FileNotFoundError(f"No existe la base SQLite: {src}")
    os.makedirs(dest_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(dest_dir, f"master_total_{stamp}.db")
    shutil.copy2(src, dest)
    return dest


def backup_postgres(dest_dir: str) -> str:
    url = Config.DATABASE_URL
    if not url:
        raise RuntimeError("DATABASE_URL no configurada")
    os.makedirs(dest_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(dest_dir, f"master_total_{stamp}.sql")
    env = os.environ.copy()
    with open(dest, "w", encoding="utf-8") as fh:
        subprocess.run(
            ["pg_dump", url],
            check=True,
            stdout=fh,
            env=env,
        )
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup de Master Total")
    parser.add_argument(
        "-o",
        "--output",
        default="backups",
        help="Directorio de salida (default: backups/)",
    )
    args = parser.parse_args()

    try:
        if Config.DATABASE_URL:
            path = backup_postgres(args.output)
        else:
            path = backup_sqlite(args.output)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
