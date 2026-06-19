# MASTER TOTAL — Guía rápida

## 3 pasos para ejecutar

### 1. Abrir terminal en la carpeta del proyecto
```powershell
cd "C:\Users\cesar\Desktop\deudas pro"
```

### 2. Instalar Flask
```powershell
pip install -r requirements.txt
```

### 3. Iniciar el sistema
```powershell
python app.py
```
Abrí **http://127.0.0.1:5000** en el navegador.

---

## Controles (solo teclado)

| Tecla | Acción |
|-------|--------|
| ↑ ↓ | Navegar lista de enemigos |
| Enter | Seleccionar / ver detalle |
| Delete | Eliminar enemigo seleccionado |
| Tab | Cambiar panel Detalle ↔ Carga |
| 1 / 2 | Ir a Detalle / Carga |
| F5 | Refrescar datos |
| Ctrl+Enter | Guardar remito (en panel Carga) |
| Enter en Meses | Guardar deuda |

## Archivos principales

- `schema.sql` — Tablas SQLite
- `app.py` — Backend + lógica de negocio + API
- `templates/terminal.html` — Dashboard estilo terminal
