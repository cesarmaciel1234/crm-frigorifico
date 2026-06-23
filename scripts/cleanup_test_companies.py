import sys
import os

# Añadir el directorio padre al path para poder importar 'app'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.database import get_db, is_postgres
from app.config import Config

app = create_app()

with app.app_context():
    print("Iniciando limpieza de empresas de prueba (manteniendo solo ID=1 Rumaul)...")
    
    with get_db(empresa_id=0) as conn:
        try:
            # 1. Asegurarnos de borrar el usuario 'maciel' esté donde esté (por si se coló en Rumaul)
            cur = conn.execute("DELETE FROM usuarios WHERE username = 'maciel' OR username = 'MACIEL'")
            print(f"- Usuario específico 'maciel' eliminado: {cur.rowcount}")

            # 2. Borrar usuarios de empresas de prueba (o sin empresa asignada)
            cur = conn.execute("DELETE FROM usuarios WHERE (empresa_id > 1 OR empresa_id IS NULL) AND username != 'admin'")
            print(f"- Otros usuarios de prueba eliminados: {cur.rowcount}")
            
            # 3. Borrar empresas de prueba
            cur = conn.execute("DELETE FROM empresas WHERE id > 1 OR slug != 'rumaul'")
            print(f"- Empresas de prueba eliminadas: {cur.rowcount}")
            
            if is_postgres():
                cur = conn.cursor()
                cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'empresa_%'")
                schemas = cur.fetchall()
                count = 0
                for row in schemas:
                    s_name = row[0]
                    if s_name != 'empresa_1':
                        print(f"  Borrando esquema: {s_name}...")
                        cur.execute(f"DROP SCHEMA IF EXISTS {s_name} CASCADE")
                        count += 1
                conn.commit()
                print(f"- Esquemas de Postgres eliminados: {count}")
                
                # Sincronizar secuencia para que las próximas empresas arranquen desde el ID correcto
                cur.execute("SELECT setval('empresas_id_seq', (SELECT coalesce(MAX(id), 1) FROM empresas))")
                conn.commit()
                print("- Secuencia de empresas sincronizada correctamente.")
            else:
                base_dir = os.path.dirname(Config.DB_PATH)
                import glob
                count = 0
                for file in glob.glob(os.path.join(base_dir, "master_total_empresa_*.db")):
                    if not file.endswith("empresa_1.db"):
                        print(f"  Borrando archivo DB: {file}...")
                        try:
                            os.remove(file)
                            count += 1
                        except Exception as e:
                            print(f"  Error borrando {file}: {e}")
                print(f"- Archivos SQLite de prueba eliminados: {count}")
                
            print("\nLIMPIEZA COMPLETADA EXITOSAMENTE. Rumaul (id=1) está blindado y seguro.")
            
        except Exception as e:
            print(f"\nERROR DURANTE LA LIMPIEZA: {e}")
            conn.rollback()
            sys.exit(1)
