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
| `MASTER_PASSWORD` | Clave para borrar datos y acciones destructivas |
| `AUDIT_DELETE_PASSWORD` | Alias retrocompatible de `MASTER_PASSWORD` |
| `ADMIN_INITIAL_PASSWORD` | Contraseña del usuario `admin` al primer arranque |
| `DATABASE_URL` | PostgreSQL (Render lo genera automáticamente) |
| `REDIS_URL` | Rate limiting distribuido (opcional) |

En Render, `render.yaml` provisiona PostgreSQL y genera las claves. Recuperá `MT_API_KEY` desde el dashboard de Render → Environment.

Acceso: visitá `/login` con usuario/contraseña o clave API. La sesión se mantiene 7 días.

La API está disponible en `/api/...` y `/api/v1/...` (mismos endpoints, versión estable).

## Backup

```powershell
python scripts/backup_db.py -o backups
```

En PostgreSQL requiere `pg_dump` instalado. Programá este comando diariamente (cron o Task Scheduler).

## Tests

```powershell
python -m pytest tests -v
```
