"""Quita bloque backup duplicado de app.js (ya está en crm-backup.js)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "app/static/js/app.js"
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

# Eliminar líneas 2033-2540 aprox (1-based): descargarArchivoJson hasta antes de window.abrirFormularioRegistro
start = None
end = None
for i, ln in enumerate(lines):
    if start is None and ln.strip().startswith("function descargarArchivoJson("):
        start = i
    if start is not None and ln.strip().startswith("window.abrirFormularioRegistro"):
        end = i
        break

if start is None or end is None:
    raise SystemExit(f"markers not found start={start} end={end}")

new_lines = lines[:start] + lines[end:]
path.write_text("".join(new_lines), encoding="utf-8")
print(f"removed lines {start+1}-{end} ({end-start} lines)")
