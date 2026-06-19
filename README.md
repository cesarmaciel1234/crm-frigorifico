# CRM Frigorífico — Master Total

**Programa principal:** Dashboard ejecutivo (deudas, remitos, clientes, KPIs, balance).

![Dashboard](https://img.shields.io/badge/Stack-Flask%20%2B%20SQLite-blue)

## Correr en tu PC (el programa de la captura)

```powershell
cd "C:\Users\cesar\Desktop\deudas pro"
pip install -r requirements.txt
python seed_demo.py          # datos de prueba (opcional)
python run.py                # desarrollo → http://127.0.0.1:5005
```

Producción local: `python start_produccion.py`

## Qué es cada URL

| URL | Qué muestra |
|-----|-------------|
| **http://127.0.0.1:5005** | Dashboard ejecutivo (programa principal) |
| **http://127.0.0.1:5005/pos** | POS offline (módulo auxiliar) |
| **github.io/crm-frigorifico** | Solo landing + POS estático (no Flask) |
| **Render (HTTPS)** | Dashboard completo en iPhone como PWA |

## iPhone — instalar el panel completo

1. Deploy en [Render.com](https://render.com) con el repo (usa `render.yaml`)
2. Safari → URL `https://tu-app.onrender.com`
3. Compartir → **Agregar a inicio**

## Tests

```powershell
python -m pytest tests -v
```

## Repo

https://github.com/cesarmaciel1234/crm-frigorifico
