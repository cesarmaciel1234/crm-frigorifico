"""
Arranque de producción — Master Total
Sin auto-reload ni debugger. Usa Waitress (servidor WSGI estable en Windows).

Uso:
  python start_produccion.py

Variables obligatorias en producción:
  SECRET_KEY          clave de sesión Flask
  MT_API_KEY          clave de acceso a la API / panel

Variables opcionales:
  MT_HOST=0.0.0.0     escuchar en la red local
  MT_PORT=5005        puerto (default: 5005)
  DATABASE_URL        PostgreSQL (recomendado en Render)
"""
import sys

from app.config import Config


def main():
    if not Config.SECRET_KEY:
        print("AVISO: SECRET_KEY no configurada. Generando una temporal. Las sesiones se perderán al reiniciar.", file=sys.stderr)
        from app.security import generate_secret
        Config.SECRET_KEY = generate_secret()
    if not Config.MT_API_KEY:
        print("AVISO: MT_API_KEY no definida — configure antes de exponer a internet", file=sys.stderr)

    from app import create_app

    app = create_app()
    host = Config.HOST
    port = Config.PORT
    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}"

    print("=" * 55)
    print("  MASTER TOTAL — PRODUCCION v3.5")
    print("  Distribuidora y Carniceria Minorista")
    print(f"  URL local: {url}")
    if host == "0.0.0.0":
        print("  Red local: escuchando en todas las interfaces")
    print("  Modo: produccion (debug OFF, sin auto-reload)")
    print("=" * 55)

    try:
        from waitress import serve
    except ImportError:
        print("ERROR: falta waitress. Ejecuta: pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)

    serve(app, host=host, port=port, threads=4)


if __name__ == "__main__":
    main()
