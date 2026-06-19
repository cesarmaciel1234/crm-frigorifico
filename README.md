# CRM Frigorífico — Master Total

Panel financiero y POS offline para distribuidoras de carne.

## URLs

| Entorno | URL | Qué es |
|---------|-----|--------|
| **GitHub Pages (PWA iPhone)** | https://cesarmaciel1234.github.io/crm-frigorifico/ | POS offline, sin servidor |
| **CRM completo (Flask)** | Render / local | Dashboard, deudas, remitos, clientes |

> GitHub Pages **no ejecuta Flask**. Ahí corre solo el **POS offline** (IndexedDB + PWA).
> El panel CRM completo va en **Render** (`render.yaml`) o `python start_produccion.py` en tu PC.

## Local

```powershell
pip install -r requirements.txt
python run.py              # desarrollo
python start_produccion.py # producción
python -m pytest tests -v  # tests
```

## iPhone — instalar PWA

1. Abrí en **Safari**: https://cesarmaciel1234.github.io/crm-frigorifico/
2. Compartir → **Agregar a inicio**
3. Usá con o sin internet (ventas se guardan en el teléfono)

## Sincronizar ventas al CRM

En `docs/config.js` configurá la URL de tu servidor Render:

```javascript
apiBase: 'https://tu-app.onrender.com',
```

## Repositorio

https://github.com/cesarmaciel1234/crm-frigorifico
