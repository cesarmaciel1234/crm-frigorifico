"""Servicio para limpiar la base de datos e importar un backup completo en formato JSON."""
from app.database import get_db, _init_tenant_db

def import_all_data(json_data: dict) -> None:
    """
    Restaura todos los datos a partir del diccionario json_data.
    Limpia completamente el esquema del tenant y recrea las tablas antes de importar.
    """
    with get_db() as conn:
        # Desactivamos FK temporariamente
        conn.execute("PRAGMA foreign_keys = OFF")
        
        # Obtenemos todas las tablas del tenant actual (SQLite local o schema Postgres)
        # Solo eliminamos tablas de la conexión actual
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        
        for table in tables:
            table_name = table[0]
            # No borrar metadatos internos de SQLite ni las tablas de login global si estamos en DB master
            if not table_name.startswith("sqlite_") and table_name not in ("empresas", "usuarios"):
                conn.execute(f"DROP TABLE IF EXISTS {table_name}")
                
        # Ahora, recreamos el esquema usando la inicialización nativa
        _init_tenant_db(conn)
        
        # 1. Configuración de empresa
        if "empresa" in json_data:
            empresa = json_data["empresa"]
            if "nombre" in empresa:
                conn.execute(
                    "UPDATE configuracion_empresa SET nombre = ?, telefono = ?, email = ?, cuit = ?, direccion = ?",
                    (
                        empresa.get("nombre", ""),
                        empresa.get("telefono", ""),
                        empresa.get("email", ""),
                        empresa.get("cuit", ""),
                        empresa.get("direccion", "")
                    )
                )

        # Helper para inserción masiva
        def insert_rows(table_name, rows, columns):
            if not rows: return
            placeholders = ",".join(["?"] * len(columns))
            cols_str = ",".join(columns)
            sql = f"INSERT INTO {table_name} ({cols_str}) VALUES ({placeholders})"
            for r in rows:
                values = tuple(r.get(c) for c in columns)
                conn.execute(sql, values)

        # 2. Clientes
        insert_rows("clientes", json_data.get("clientes", []), ["id", "nombre", "whatsapp", "activo"])
        
        # 3. Bancos
        insert_rows("bancos", json_data.get("bancos", []), ["id", "alias", "banco", "cbu", "activo"])
        
        # 4. Remitos
        insert_rows("remitos", json_data.get("remitos", []), ["id", "cliente_id", "fecha", "monto", "pagado", "saldo", "estado", "observaciones", "vencimiento", "kilos_vendidos", "factura_nro"])
        
        # 5. Operaciones Financieras
        insert_rows("operaciones_financieras", json_data.get("operaciones", []), [
            "id", "tipo", "concepto", "banco_id", "recibido", "cuotas", 
            "fecha_ingreso", "primer_vencimiento", "interes", "monto_cuota",
            "capital_restante", "total_devolver", "estado", "observaciones"
        ])
        
        # 6. Pagos de Cuotas
        insert_rows("pagos_cuotas", json_data.get("pagos_cuotas", []), [
            "id", "operacion_id", "nro_cuota", "fecha_pago", "monto_pago", "estado"
        ])
        
        # 7. Ventas Mostrador
        insert_rows("ventas_mostrador", json_data.get("ventas_mostrador", []), [
            "id", "fecha", "monto", "kilos", "banco_id", "observaciones"
        ])
        
        # 8. Perdidas Acumuladas
        insert_rows("perdidas_acumuladas", json_data.get("perdidas", []), [
            "id", "mes", "monto", "descripcion"
        ])
        
        # 9. Lotes Bulk (Compras)
        insert_rows("bulk_lots", json_data.get("bulk", []), [
            "id", "proveedor", "fecha_ingreso", "cantidad_kg", "precio_compra_kg", "costo_total", "vendido_kg", "ingreso_total", "estado", "observaciones"
        ])
        
        # 10. Auditoría
        insert_rows("auditoria", json_data.get("auditoria_reciente", []), [
            "id", "usuario", "accion", "entidad", "entidad_id", "detalle", "fecha"
        ])

        # Rehabilitamos FK
        conn.execute("PRAGMA foreign_keys = ON")
