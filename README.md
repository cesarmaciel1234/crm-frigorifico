# CRM Frigorífico — Master Total

Dashboard ejecutivo Flask para distribuidoras de carne.

## Programa principal (tu captura)

```powershell
pip install -r requirements.txt
python seed_demo.py    # opcional
python run.py          # http://127.0.0.1:5005
```

## Publicar en internet (iPhone PWA)

1. Entrá a [render.com](https://render.com) → **New Web Service**
2. Conectá el repo `cesarmaciel1234/crm-frigorifico`
3. Render lee `render.yaml` automáticamente → **Deploy**
4. URL del programa: **https://crm-frigorifico.onrender.com**
5. iPhone: Safari → esa URL → Compartir → **Agregar a inicio**

## GitHub Pages

`https://cesarmaciel1234.github.io/crm-frigorifico/` redirige a Render (no es el programa, solo enlace).

## Módulos incluidos

| Ruta | Qué es |
|------|--------|
| `/` | Dashboard ejecutivo |
| `/pos` | POS offline (integrado, sincroniza al mismo servidor) |

## Seguridad (producción)

Copiá `.env.example` a `.env` y definí:

| Variable | Uso |
|----------|-----|
| `SECRET_KEY` | Sesiones Flask (obligatoria en producción) |
| `MT_API_KEY` | Clave de acceso al panel y API |
| `AUDIT_DELETE_PASSWORD` | Clave para borrar registros de auditoría |
| `DATABASE_URL` | PostgreSQL (Render lo genera automáticamente) |

En Render, `render.yaml` provisiona PostgreSQL y genera las claves. Recuperá `MT_API_KEY` desde el dashboard de Render → Environment.

Acceso: visitá `/login` e ingresá la clave. La sesión se mantiene 7 días.

## Tests

```powershell
python -m pytest tests -v
```
