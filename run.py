from app import create_app
from app.config import Config

app = create_app()

if __name__ == "__main__":
    print("=" * 55)
    print("  MASTER TOTAL — DESARROLLO v3.6")
    print("  Distribuidora y Carnicería Minorista")
    print(f"  Consola: http://{Config.HOST}:{Config.PORT}")
    print("  Producción: python start_produccion.py")
    print("=" * 55)
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
