"""
Arranque de producción — Master Total
Sin auto-reload ni debugger. Usa Waitress (servidor WSGI estable en Windows).

Uso:
  python start_produccion.py

Variables obligatorias en producción:
  SECRET_KEY          clave de sesión Flask
  MT_API_KEY          clave de acceso a la API / panel
  MASTER_PASSWORD     clave para acciones destructivas (o AUDIT_DELETE_PASSWORD)

Variables opcionales:
  MT_HOST=0.0.0.0     escuchar en la red local
  MT_PORT=5005        puerto (default: 5005)
  DATABASE_URL        PostgreSQL (recomendado en Render)
  ADMIN_INITIAL_PASSWORD  contraseña del usuario admin inicial
  REDIS_URL           rate limiting distribuido (opcional)
"""
import sys

from app.config import Config


def main():
    errors = Config.validate_production()
    if errors:
        print("ERROR: configuración de producción inválida:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    if not Config.SECRET_KEY:
        from app.security import generate_secret
        Config.SECRET_KEY = generate_secret()
        print(f"AVISO: SECRET_KEY no configurada. Generada temporal: {Config.SECRET_KEY}", file=sys.stderr)
        
    if not Config.MT_API_KEY:
        from app.security import generate_secret
        Config.MT_API_KEY = generate_secret()
        print(f"AVISO: MT_API_KEY no definida. Generada temporal: {Config.MT_API_KEY}", file=sys.stderr)
        
    if not Config.master_password():
        from app.security import generate_secret
        gen_pwd = generate_secret()
        Config.MASTER_PASSWORD = gen_pwd
        Config.AUDIT_DELETE_PASSWORD = gen_pwd
        print(f"AVISO: MASTER_PASSWORD / AUDIT_DELETE_PASSWORD no definida. Generada temporal: {gen_pwd}", file=sys.stderr)

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
