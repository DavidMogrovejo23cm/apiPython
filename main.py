from typing import Union, Optional, List
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
import secrets
import string
from datetime import datetime
from pydantic import BaseModel
from database import get_db, reset_database, create_tables, QRCode, RegistroEscaneo
from sqlalchemy import desc

# Importación condicional de qrcode
try:
    import qrcode
    from io import BytesIO
    import base64
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False

app = FastAPI(title="QR Attendance API", description="API simplificada para generar QRs y registrar asistencia")

# Reiniciar la base de datos al iniciar (elimina esquema anterior)
print("🚀 Iniciando aplicación...")
reset_database()

# ============= MODELOS PYDANTIC =============

class QRGenerationRequest(BaseModel):
    empleado_id: int

class QRCodeResponse(BaseModel):
    id: int
    empleado_id: int
    qr_code_base64: str
    creado_en: str
    activo: bool
    total_escaneos: int

class EscaneoResponse(BaseModel):
    id: int
    qr_id: int
    empleado_id: int
    fecha: str
    hora_entrada: str
    hora_salida: Optional[str] = None
    es_entrada: bool  # True si es entrada, False si es salida
    duracion_jornada: Optional[str] = None  # Duración en formato "8h 30m" si hay salida

class ValidationResponse(BaseModel):
    valid: bool
    message: str
    qr_data: Optional[dict] = None
    accion: Optional[str] = None  # "ENTRADA" o "SALIDA"

class AttendanceStatsResponse(BaseModel):
    total_qrs: int
    total_escaneos: int
    empleados_registrados: int
    escaneos_hoy: int

# ============= FUNCIONES AUXILIARES =============

def generate_unique_id(length=16):
    """Genera un ID único para identificar el QR"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def generate_qr_code(qr_id: str) -> str:
    """Genera código QR en base64"""
    if not QR_AVAILABLE:
        # Si no está disponible qrcode, generar un placeholder
        return f"QR_PLACEHOLDER_ID:{qr_id}"
    
    try:
        # El QR contendrá el ID del registro en la BD
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(str(qr_id))
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"Error generando QR: {e}")
        return f"QR_ERROR_ID:{qr_id}"

def qr_to_response(qr_code: QRCode, db: Session) -> QRCodeResponse:
    """Convierte un QR code de la DB a respuesta"""
    total_escaneos = db.query(RegistroEscaneo).filter(RegistroEscaneo.qr_id == qr_code.id).count()
    
    return QRCodeResponse(
        id=qr_code.id,
        empleado_id=qr_code.empleado_id,
        qr_code_base64=qr_code.qr_code_base64,
        creado_en=qr_code.creado_en.isoformat(),
        activo=qr_code.activo,
        total_escaneos=total_escaneos
    )

def escaneo_to_response(escaneo: RegistroEscaneo, db: Session) -> EscaneoResponse:
    """Convierte un registro de escaneo a respuesta"""
    # Calcular duración si hay hora de salida
    duracion_jornada = None
    if escaneo.hora_salida:
        duracion = escaneo.hora_salida - escaneo.hora_entrada
        horas = int(duracion.total_seconds() // 3600)
        minutos = int((duracion.total_seconds() % 3600) // 60)
        duracion_jornada = f"{horas}h {minutos}m"
    
    # Determinar si es entrada (cuando se crea) o salida (cuando se actualiza)
    es_entrada = escaneo.hora_salida is None
    
    return EscaneoResponse(
        id=escaneo.id,
        qr_id=escaneo.qr_id,
        empleado_id=escaneo.empleado_id,
        fecha=escaneo.fecha.date().isoformat(),
        hora_entrada=escaneo.hora_entrada.isoformat(),
        hora_salida=escaneo.hora_salida.isoformat() if escaneo.hora_salida else None,
        es_entrada=es_entrada,
        duracion_jornada=duracion_jornada
    )

# ============= ENDPOINTS =============

@app.get("/")
def read_root():
    return {
        "Hello": "QR Attendance API", 
        "version": "1.0.0",
        "features": [
            "Generación de códigos QR por empleado",
            "Registro de escaneos con fecha/hora",
            "Control de asistencia simplificado"
        ]
    }

@app.post("/qr/generate", response_model=QRCodeResponse)
def generate_qr(request: QRGenerationRequest, db: Session = Depends(get_db)):
    """Genera un nuevo código QR para un empleado"""
    
    # Verificar si ya existe un QR activo para este empleado
    existing_qr = db.query(QRCode).filter(
        QRCode.empleado_id == request.empleado_id,
        QRCode.activo == True
    ).first()
    
    if existing_qr:
        # Devolver el QR existente en lugar de crear uno nuevo
        return qr_to_response(existing_qr, db)
    
    # Crear nuevo QR en la base de datos primero para obtener el ID
    db_qr = QRCode(
        empleado_id=request.empleado_id,
        qr_code_base64="temp"  # Temporal
    )
    
    db.add(db_qr)
    db.commit()
    db.refresh(db_qr)
    
    # Generar el código QR usando el ID de la base de datos
    qr_code_base64 = generate_qr_code(db_qr.id)
    
    # Actualizar con el QR generado
    db_qr.qr_code_base64 = qr_code_base64
    db.commit()
    db.refresh(db_qr)
    
    return qr_to_response(db_qr, db)

@app.get("/qr/{qr_id}/validate", response_model=ValidationResponse)
def validate_qr(qr_id: int, db: Session = Depends(get_db)):
    """Valida un código QR y determina si será entrada o salida"""
    
    qr_code = db.query(QRCode).filter(QRCode.id == qr_id).first()
    
    if not qr_code:
        return ValidationResponse(
            valid=False,
            message="Código QR no encontrado"
        )
    
    if not qr_code.activo:
        return ValidationResponse(
            valid=False,
            message="Código QR desactivado",
            qr_data={
                "empleado_id": qr_code.empleado_id,
                "activo": False
            }
        )
    
    # Verificar si hay un registro de entrada sin salida para hoy
    hoy = datetime.utcnow().date()
    registro_hoy = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.qr_id == qr_id,
        RegistroEscaneo.fecha >= datetime.combine(hoy, datetime.min.time()),
        RegistroEscaneo.fecha < datetime.combine(hoy, datetime.max.time())
    ).first()
    
    if registro_hoy:
        if registro_hoy.hora_salida is None:
            # Ya tiene entrada, el próximo escaneo será salida
            accion = "SALIDA"
            mensaje = f"Registrará salida - Entrada: {registro_hoy.hora_entrada.strftime('%H:%M:%S')}"
        else:
            # Ya completó entrada y salida hoy
            accion = "COMPLETADO"
            mensaje = f"Ya registró entrada y salida hoy"
    else:
        # No hay registro hoy, será entrada
        accion = "ENTRADA"
        mensaje = "Registrará entrada"
    
    return ValidationResponse(
        valid=True,
        message=mensaje,
        accion=accion,
        qr_data={
            "empleado_id": qr_code.empleado_id,
            "activo": qr_code.activo,
            "creado_en": qr_code.creado_en.isoformat()
        }
    )

@app.post("/qr/{qr_id}/scan", response_model=EscaneoResponse)
def record_scan(qr_id: int, db: Session = Depends(get_db)):
    """Registra un escaneo del código QR (entrada o salida)"""
    
    # Verificar que el QR existe y está activo
    qr_code = db.query(QRCode).filter(QRCode.id == qr_id).first()
    
    if not qr_code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Código QR no encontrado"
        )
    
    if not qr_code.activo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Código QR desactivado"
        )
    
    ahora = datetime.utcnow()
    hoy = ahora.date()
    
    # Buscar registro de hoy
    registro_hoy = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.qr_id == qr_id,
        RegistroEscaneo.fecha >= datetime.combine(hoy, datetime.min.time()),
        RegistroEscaneo.fecha < datetime.combine(hoy, datetime.max.time())
    ).first()
    
    if registro_hoy:
        if registro_hoy.hora_salida is None:
            # Registrar salida
            registro_hoy.hora_salida = ahora
            db.commit()
            db.refresh(registro_hoy)
            return escaneo_to_response(registro_hoy, db)
        else:
            # Ya completó entrada y salida
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ya registró entrada y salida para hoy"
            )
    else:
        # Crear nuevo registro de entrada
        nuevo_registro = RegistroEscaneo(
            qr_id=qr_id,
            empleado_id=qr_code.empleado_id,
            fecha=ahora,
            hora_entrada=ahora,
            hora_salida=None
        )
        
        db.add(nuevo_registro)
        db.commit()
        db.refresh(nuevo_registro)
        
        return escaneo_to_response(nuevo_registro, db)

# ============= ENDPOINTS ADMINISTRATIVOS =============

@app.get("/admin/qrs", response_model=List[QRCodeResponse])
def get_all_qrs(
    empleado_id: Optional[int] = None,
    activo: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """Obtiene todos los códigos QR con filtros"""
    query = db.query(QRCode)
    
    if empleado_id:
        query = query.filter(QRCode.empleado_id == empleado_id)
    
    if activo is not None:
        query = query.filter(QRCode.activo == activo)
    
    qrs = query.offset(offset).limit(limit).all()
    return [qr_to_response(qr, db) for qr in qrs]

@app.get("/admin/escaneos", response_model=List[EscaneoResponse])
def get_all_scans(
    qr_id: Optional[int] = None,
    empleado_id: Optional[int] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    solo_sin_salida: Optional[bool] = False,  # Filtrar solo registros sin hora de salida
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """Obtiene todos los registros de escaneo con filtros"""
    query = db.query(RegistroEscaneo)
    
    if qr_id:
        query = query.filter(RegistroEscaneo.qr_id == qr_id)
    
    if empleado_id:
        query = query.filter(RegistroEscaneo.empleado_id == empleado_id)
    
    if fecha_desde:
        try:
            fecha_desde_dt = datetime.fromisoformat(fecha_desde)
            query = query.filter(RegistroEscaneo.fecha >= fecha_desde_dt)
        except ValueError:
            pass
    
    if fecha_hasta:
        try:
            fecha_hasta_dt = datetime.fromisoformat(fecha_hasta)
            query = query.filter(RegistroEscaneo.fecha <= fecha_hasta_dt)
        except ValueError:
            pass
    
    if solo_sin_salida:
        query = query.filter(RegistroEscaneo.hora_salida.is_(None))
    
    escaneos = query.order_by(desc(RegistroEscaneo.fecha)).offset(offset).limit(limit).all()
    return [escaneo_to_response(escaneo, db) for escaneo in escaneos]

@app.get("/admin/empleado/{empleado_id}/escaneos", response_model=List[EscaneoResponse])
def get_employee_scans(empleado_id: int, db: Session = Depends(get_db)):
    """Obtiene todos los escaneos de un empleado específico"""
    escaneos = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.empleado_id == empleado_id
    ).order_by(desc(RegistroEscaneo.fecha)).all()
    
    return [escaneo_to_response(escaneo, db) for escaneo in escaneos]

@app.put("/admin/qr/{qr_id}/toggle")
def toggle_qr_status(qr_id: int, db: Session = Depends(get_db)):
    """Activa/desactiva un código QR"""
    
    qr_code = db.query(QRCode).filter(QRCode.id == qr_id).first()
    
    if not qr_code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Código QR no encontrado"
        )
    
    qr_code.activo = not qr_code.activo
    db.commit()
    
    return {
        "success": True,
        "message": f"QR {'activado' if qr_code.activo else 'desactivado'}",
        "qr_id": qr_id,
        "activo": qr_code.activo
    }

@app.delete("/admin/qr/{qr_id}")
def delete_qr(qr_id: int, db: Session = Depends(get_db)):
    """Elimina un código QR y todos sus escaneos"""
    
    qr_code = db.query(QRCode).filter(QRCode.id == qr_id).first()
    
    if not qr_code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Código QR no encontrado"
        )
    
    # Eliminar escaneos asociados
    escaneos_eliminados = db.query(RegistroEscaneo).filter(RegistroEscaneo.qr_id == qr_id).delete()
    
    # Eliminar QR
    db.delete(qr_code)
    db.commit()
    
    return {
        "success": True,
        "message": f"QR eliminado junto con {escaneos_eliminados} escaneos",
        "qr_id": qr_id,
        "escaneos_eliminados": escaneos_eliminados
    }

# ============= ENDPOINTS ESPECÍFICOS PARA ENTRADA/SALIDA =============

@app.get("/admin/empleados/sin-salida")
def get_employees_without_exit(db: Session = Depends(get_db)):
    """Obtiene empleados que tienen entrada pero no salida hoy"""
    hoy = datetime.utcnow().date()
    
    registros_sin_salida = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.fecha >= datetime.combine(hoy, datetime.min.time()),
        RegistroEscaneo.fecha < datetime.combine(hoy, datetime.max.time()),
        RegistroEscaneo.hora_salida.is_(None)
    ).all()
    
    empleados_info = []
    for registro in registros_sin_salida:
        empleados_info.append({
            "empleado_id": registro.empleado_id,
            "hora_entrada": registro.hora_entrada.isoformat(),
            "tiempo_transcurrido": str(datetime.utcnow() - registro.hora_entrada).split('.')[0]
        })
    
    return {
        "total": len(empleados_info),
        "empleados": empleados_info
    }

@app.post("/admin/registro/{registro_id}/forzar-salida")
def force_exit(registro_id: int, db: Session = Depends(get_db)):
    """Fuerza una salida para un registro específico (uso administrativo)"""
    registro = db.query(RegistroEscaneo).filter(RegistroEscaneo.id == registro_id).first()
    
    if not registro:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registro no encontrado"
        )
    
    if registro.hora_salida:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este registro ya tiene hora de salida"
        )
    
    registro.hora_salida = datetime.utcnow()
    db.commit()
    
    return {
        "success": True,
        "message": "Salida forzada registrada",
        "registro": escaneo_to_response(registro, db)
    }

@app.get("/admin/reporte-diario/{fecha}")
def daily_report(fecha: str, db: Session = Depends(get_db)):
    """Genera reporte diario de asistencia"""
    try:
        fecha_obj = datetime.fromisoformat(fecha).date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato de fecha inválido. Use YYYY-MM-DD"
        )
    
    # Obtener todos los registros del día
    registros = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.fecha >= datetime.combine(fecha_obj, datetime.min.time()),
        RegistroEscaneo.fecha < datetime.combine(fecha_obj, datetime.max.time())
    ).all()
    
    # Estadísticas
    total_empleados = len(set(r.empleado_id for r in registros))
    con_entrada = len(registros)
    con_salida = len([r for r in registros if r.hora_salida])
    sin_salida = con_entrada - con_salida
    
    # Detalle por empleado
    empleados_detalle = []
    for registro in registros:
        duracion = None
        if registro.hora_salida:
            diff = registro.hora_salida - registro.hora_entrada
            horas = int(diff.total_seconds() // 3600)
            minutos = int((diff.total_seconds() % 3600) // 60)
            duracion = f"{horas}h {minutos}m"
        
        empleados_detalle.append({
            "empleado_id": registro.empleado_id,
            "hora_entrada": registro.hora_entrada.strftime("%H:%M:%S"),
            "hora_salida": registro.hora_salida.strftime("%H:%M:%S") if registro.hora_salida else None,
            "duracion_jornada": duracion,
            "completo": registro.hora_salida is not None
        })
    
    return {
        "fecha": fecha,
        "estadisticas": {
            "total_empleados": total_empleados,
            "con_entrada": con_entrada,
            "con_salida": con_salida,
            "sin_salida": sin_salida
        },
        "empleados": empleados_detalle
    }

# ============= ESTADÍSTICAS =============

@app.get("/stats", response_model=AttendanceStatsResponse)
def get_attendance_stats(db: Session = Depends(get_db)):
    """Obtiene estadísticas de asistencia"""
    
    # Contar totales
    total_qrs = db.query(QRCode).count()
    total_escaneos = db.query(RegistroEscaneo).count()
    
    # Empleados únicos con QR
    empleados_registrados = db.query(QRCode.empleado_id).distinct().count()
    
    # Escaneos de hoy
    hoy = datetime.utcnow().date()
    escaneos_hoy = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.fecha >= datetime.combine(hoy, datetime.min.time()),
        RegistroEscaneo.fecha < datetime.combine(hoy, datetime.max.time())
    ).count()
    
    return AttendanceStatsResponse(
        total_qrs=total_qrs,
        total_escaneos=total_escaneos,
        empleados_registrados=empleados_registrados,
        escaneos_hoy=escaneos_hoy
    )

@app.get("/info")
def get_system_info(db: Session = Depends(get_db)):
    """Información del sistema con estadísticas"""
    stats = get_attendance_stats(db)
    
    return {
        "app": "QR Attendance API",
        "version": "1.0.0",
        "database": "PostgreSQL (Neon)",
        "qr_available": QR_AVAILABLE,
        "attendance_stats": {
            "total_qrs": stats.total_qrs,
            "total_escaneos": stats.total_escaneos,
            "empleados_registrados": stats.empleados_registrados,
            "escaneos_hoy": stats.escaneos_hoy
        },
        "features": [
            "Generación de QR por empleado",
            "Registro de múltiples escaneos",
            "Control de asistencia sin tokens",
            "Estadísticas en tiempo real"
        ]
    }

# ============= ENDPOINTS LEGACY PARA COMPATIBILIDAD CON EL SCANNER =============

@app.post("/tokens/{qr_id}/record_scan")
def legacy_record_scan(qr_id: str, db: Session = Depends(get_db)):
    """Endpoint legacy para compatibilidad con el scanner existente"""
    try:
        # Convertir qr_id a int
        qr_id_int = int(qr_id)
        escaneo = record_scan(qr_id_int, db)
        
        return {
            "success": True,
            "message": "Escaneo registrado",
            "is_first_scan": escaneo.es_entrada,  # True si es entrada, False si es salida
            "empleado_id": escaneo.empleado_id,
            "fecha_escaneo": escaneo.hora_entrada if escaneo.es_entrada else escaneo.hora_salida,
            "accion": "ENTRADA" if escaneo.es_entrada else "SALIDA"
        }
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ID de QR inválido"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.get("/tokens/{qr_id}/validate")
def legacy_validate(qr_id: str, db: Session = Depends(get_db)):
    """Endpoint legacy para validación compatible con el scanner"""
    try:
        qr_id_int = int(qr_id)
        validation = validate_qr(qr_id_int, db)
        
        if validation.valid:
            # Obtener registros para mostrar en el scanner
            registros = db.query(RegistroEscaneo).filter(
                RegistroEscaneo.qr_id == qr_id_int
            ).order_by(desc(RegistroEscaneo.fecha)).all()
            
            # Crear lista de escaneos previos (entradas y salidas)
            previous_scans = []
            for registro in registros:
                previous_scans.append(registro.hora_entrada.isoformat())
                if registro.hora_salida:
                    previous_scans.append(registro.hora_salida.isoformat())
            
            # Obtener último registro para mostrar información
            ultimo_registro = registros[0] if registros else None
            usado_en = None
            if ultimo_registro:
                if ultimo_registro.hora_salida:
                    usado_en = ultimo_registro.hora_salida.isoformat()
                else:
                    usado_en = ultimo_registro.hora_entrada.isoformat()
            
            return {
                "valid": True,
                "message": validation.message,
                "token_data": {
                    "empleado_id": validation.qr_data["empleado_id"],
                    "estado": "ACTIVO",
                    "usado_en": usado_en,
                    "accion": validation.accion
                },
                "first_scan": validation.accion == "ENTRADA",
                "previous_scans": previous_scans
            }
        else:
            return {
                "valid": False,
                "message": validation.message,
                "token_data": validation.qr_data or {},
                "first_scan": False,
                "previous_scans": []
            }
    except ValueError:
        return {
            "valid": False,
            "message": "ID de QR inválido",
            "token_data": {},
            "first_scan": False,
            "previous_scans": []
        }
    except Exception as e:
        return {
            "valid": False,
            "message": f"Error del servidor: {str(e)}",
            "token_data": {},
            "first_scan": False,
            "previous_scans": []
        }