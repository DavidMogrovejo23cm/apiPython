from typing import Union, Optional, List
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
import secrets
import string
from datetime import datetime, timedelta
from pydantic import BaseModel
from database import get_db, create_tables, QRToken, TokenType
from sqlalchemy import and_, or_

# Importación condicional de qrcode
try:
    import qrcode
    from io import BytesIO
    import base64
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False

app = FastAPI(title="QR Token API", description="API para generar tokens QR con gestión flexible")

# Crear tablas al iniciar
create_tables()

# ============= CONFIGURACIÓN DE EXPIRACIÓN =============
DURACION_DEFAULT = {
    "empleado": 8760,  # 1 año en horas (365 días * 24 horas)
    "jefe": 8760,      # 1 año en horas
    "temporal": 24,    # 1 día para tokens temporales
    "visitante": 4     # 4 horas para visitantes
}

# ============= MODELOS PYDANTIC MEJORADOS =============

class TokenGenerationRequest(BaseModel):
    empleado_id: int
    duracion_horas: Optional[int] = None  # Si no se especifica, usa el default según tipo
    tipo_token: str  # "empleado", "jefe", "temporal", "visitante"
    departamento: Optional[str] = None
    permisos_especiales: Optional[str] = None
    descripcion: Optional[str] = None  # Descripción del token para identificación

class TokenUpdateRequest(BaseModel):
    activo: Optional[bool] = None
    descripcion: Optional[str] = None
    extender_horas: Optional[int] = None  # Extender la expiración X horas

class TokenRefreshRequest(BaseModel):
    token: str
    nuevas_horas: int = 168  # Default: 1 semana

class QRTokenResponse(BaseModel):
    id: int
    token: str
    empleado_id: int
    tipo_token: str
    creado_en: str
    expira_en: str
    usado: bool
    usado_en: Optional[str] = None
    qrCode: Optional[str] = None
    departamento: Optional[str] = None
    permisos_especiales: Optional[str] = None
    descripcion: Optional[str] = None
    activo: bool
    dias_restantes: int  # Días hasta expiración
    estado: str  # "ACTIVO", "EXPIRADO", "DESACTIVADO", "USADO"

class TokenValidationResponse(BaseModel):
    valid: bool
    message: str
    token_data: Optional[dict] = None
    warnings: List[str] = []  # Advertencias como "expira pronto"

class AdminTokenListResponse(BaseModel):
    tokens: List[QRTokenResponse]
    total: int
    activos: int
    expirados: int
    desactivados: int

# ============= FUNCIONES AUXILIARES MEJORADAS =============

def generate_token(length=32):
    """Genera un token aleatorio seguro"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def generate_qr_code(token: str) -> Optional[str]:
    """Genera código QR en base64"""
    if not QR_AVAILABLE:
        return None
    
    try:
        img = qrcode.make(token)
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"Error generando QR: {e}")
        return None

def calcular_estado_token(token: QRToken) -> str:
    """Calcula el estado actual del token"""
    if not token.activo:
        return "DESACTIVADO"
    elif token.usado:
        return "USADO"
    elif datetime.utcnow() > token.expira_en:
        return "EXPIRADO"
    else:
        return "ACTIVO"

def calcular_dias_restantes(expira_en: datetime) -> int:
    """Calcula los días restantes hasta expiración"""
    diff = expira_en - datetime.utcnow()
    return max(0, diff.days)

def token_to_response(token: QRToken) -> QRTokenResponse:
    """Convierte un token de la DB a respuesta con información extendida"""
    estado = calcular_estado_token(token)
    dias_restantes = calcular_dias_restantes(token.expira_en)
    
    return QRTokenResponse(
        id=token.id,
        token=token.token,
        empleado_id=token.empleado_id,
        tipo_token=token.tipo_token.value,
        creado_en=token.creado_en.isoformat(),
        expira_en=token.expira_en.isoformat(),
        usado=token.usado,
        usado_en=token.usado_en.isoformat() if token.usado_en else None,
        qrCode=token.qr_code_base64 if token.qr_code_base64 else f"QR_NOT_AVAILABLE_TOKEN:{token.token}",
        departamento=token.departamento,
        permisos_especiales=token.permisos_especiales,
        descripcion=token.descripcion if hasattr(token, 'descripcion') else None,
        activo=token.activo,
        dias_restantes=dias_restantes,
        estado=estado
    )

def get_duracion_default(tipo_token: str) -> int:
    """Obtiene la duración por defecto según el tipo de token"""
    return DURACION_DEFAULT.get(tipo_token, DURACION_DEFAULT["empleado"])

# ============= ENDPOINTS PRINCIPALES MEJORADOS =============

@app.get("/")
def read_root():
    return {
        "Hello": "QR Token API - Gestión Flexible", 
        "version": "3.0.0",
        "features": [
            "Tokens de larga duración",
            "Gestión administrativa",
            "Refresh de tokens",
            "Estados detallados"
        ]
    }

@app.post("/tokens/generate", response_model=QRTokenResponse)
def generate_qr_token(request: TokenGenerationRequest, db: Session = Depends(get_db)):
    """Genera un nuevo QR token con duración flexible"""
    
    # Validar tipo de token
    tipos_validos = ["empleado", "jefe", "temporal", "visitante"]
    if request.tipo_token not in tipos_validos:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"tipo_token debe ser uno de: {', '.join(tipos_validos)}"
        )
    
    # Usar duración por defecto si no se especifica
    duracion_horas = request.duracion_horas or get_duracion_default(request.tipo_token)
    
    # Validar campos requeridos para jefes
    if request.tipo_token == "jefe" and not request.departamento:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El departamento es requerido para tokens de jefe"
        )
    
    # Generar token único
    token = generate_token()
    while db.query(QRToken).filter(QRToken.token == token).first():
        token = generate_token()
    
    # Generar QR code
    qr_code_base64 = generate_qr_code(token)
    
    # Crear el token en la base de datos
    db_token = QRToken(
        token=token,
        empleado_id=request.empleado_id,
        tipo_token=TokenType.EMPLEADO if request.tipo_token == "empleado" else TokenType.JEFE,
        expira_en=datetime.utcnow() + timedelta(hours=duracion_horas),
        qr_code_base64=qr_code_base64,
        departamento=request.departamento if request.tipo_token in ["jefe"] else None,
        permisos_especiales=request.permisos_especiales,
        # Agregar descripción si el modelo de BD lo soporta
        # descripcion=request.descripcion
    )
    
    db.add(db_token)
    db.commit()
    db.refresh(db_token)
    
    return token_to_response(db_token)

@app.get("/tokens/{token}/validate", response_model=TokenValidationResponse)
def validate_token(token: str, db: Session = Depends(get_db)):
    """Valida un token con información detallada y advertencias"""
    
    db_token = db.query(QRToken).filter(QRToken.token == token).first()
    
    if not db_token:
        return TokenValidationResponse(
            valid=False,
            message="Token no encontrado"
        )
    
    warnings = []
    
    # Verificar si está desactivado
    if not db_token.activo:
        return TokenValidationResponse(
            valid=False,
            message="Token desactivado por administrador",
            token_data={
                "empleado_id": db_token.empleado_id,
                "tipo_token": db_token.tipo_token.value,
                "estado": "DESACTIVADO"
            }
        )
    
    # Verificar si ya fue usado
    if db_token.usado:
        return TokenValidationResponse(
            valid=False,
            message="Token ya fue utilizado",
            token_data={
                "usado_en": db_token.usado_en.isoformat() if db_token.usado_en else None,
                "tipo_token": db_token.tipo_token.value,
                "empleado_id": db_token.empleado_id,
                "estado": "USADO"
            }
        )
    
    # Verificar expiración
    now = datetime.utcnow()
    if now > db_token.expira_en:
        return TokenValidationResponse(
            valid=False,
            message="Token expirado",
            token_data={
                "expira_en": db_token.expira_en.isoformat(),
                "tipo_token": db_token.tipo_token.value,
                "empleado_id": db_token.empleado_id,
                "estado": "EXPIRADO"
            }
        )
    
    # Advertencias por proximidad de expiración
    dias_restantes = calcular_dias_restantes(db_token.expira_en)
    if dias_restantes <= 7:
        warnings.append(f"Token expira en {dias_restantes} días")
    elif dias_restantes <= 30:
        warnings.append(f"Token expira en {dias_restantes} días")
    
    return TokenValidationResponse(
        valid=True,
        message="Token válido",
        warnings=warnings,
        token_data={
            "empleado_id": db_token.empleado_id,
            "tipo_token": db_token.tipo_token.value,
            "departamento": db_token.departamento,
            "permisos_especiales": db_token.permisos_especiales,
            "expira_en": db_token.expira_en.isoformat(),
            "dias_restantes": dias_restantes,
            "estado": "ACTIVO"
        }
    )

# ============= ENDPOINTS ADMINISTRATIVOS =============

@app.get("/admin/tokens", response_model=AdminTokenListResponse)
def get_all_tokens_admin(
    empleado_id: Optional[int] = None,
    tipo_token: Optional[str] = None,
    estado: Optional[str] = None,  # ACTIVO, EXPIRADO, DESACTIVADO, USADO
    departamento: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """Vista administrativa de todos los tokens con filtros avanzados"""
    query = db.query(QRToken)
    
    # Aplicar filtros
    if empleado_id:
        query = query.filter(QRToken.empleado_id == empleado_id)
    
    if tipo_token:
        if tipo_token == "empleado":
            query = query.filter(QRToken.tipo_token == TokenType.EMPLEADO)
        elif tipo_token == "jefe":
            query = query.filter(QRToken.tipo_token == TokenType.JEFE)
    
    if departamento:
        query = query.filter(QRToken.departamento == departamento)
    
    # Obtener todos los tokens para calcular estadísticas
    all_tokens = query.all()
    
    # Aplicar filtro de estado después de obtener los datos
    if estado:
        filtered_tokens = []
        for token in all_tokens:
            token_estado = calcular_estado_token(token)
            if token_estado == estado:
                filtered_tokens.append(token)
        tokens_to_show = filtered_tokens[offset:offset + limit]
    else:
        tokens_to_show = all_tokens[offset:offset + limit]
    
    # Calcular estadísticas
    activos = sum(1 for t in all_tokens if calcular_estado_token(t) == "ACTIVO")
    expirados = sum(1 for t in all_tokens if calcular_estado_token(t) == "EXPIRADO")
    desactivados = sum(1 for t in all_tokens if calcular_estado_token(t) == "DESACTIVADO")
    
    return AdminTokenListResponse(
        tokens=[token_to_response(token) for token in tokens_to_show],
        total=len(all_tokens),
        activos=activos,
        expirados=expirados,
        desactivados=desactivados
    )

@app.put("/admin/tokens/{token}/update")
def update_token_admin(
    token: str, 
    request: TokenUpdateRequest, 
    db: Session = Depends(get_db)
):
    """Actualiza un token (activar/desactivar, extender duración, etc.)"""
    
    db_token = db.query(QRToken).filter(QRToken.token == token).first()
    
    if not db_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token no encontrado"
        )
    
    # Actualizar campos
    if request.activo is not None:
        db_token.activo = request.activo
    
    if request.extender_horas:
        db_token.expira_en = db_token.expira_en + timedelta(hours=request.extender_horas)
    
    # Si el modelo soporta descripción
    # if request.descripcion is not None:
    #     db_token.descripcion = request.descripcion
    
    db.commit()
    db.refresh(db_token)
    
    return {
        "success": True,
        "message": "Token actualizado exitosamente",
        "token": token_to_response(db_token)
    }

@app.post("/admin/tokens/{token}/refresh")
def refresh_token(token: str, request: TokenRefreshRequest, db: Session = Depends(get_db)):
    """Extiende la duración de un token existente"""
    
    db_token = db.query(QRToken).filter(QRToken.token == token).first()
    
    if not db_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token no encontrado"
        )
    
    # Extender la expiración
    nueva_expiracion = datetime.utcnow() + timedelta(hours=request.nuevas_horas)
    db_token.expira_en = nueva_expiracion
    
    # Reactivar el token si estaba desactivado
    if not db_token.activo:
        db_token.activo = True
    
    # Si estaba marcado como usado, resetear (opcional, según lógica de negocio)
    # db_token.usado = False
    # db_token.usado_en = None
    
    db.commit()
    db.refresh(db_token)
    
    return {
        "success": True,
        "message": f"Token extendido por {request.nuevas_horas} horas",
        "nueva_expiracion": nueva_expiracion.isoformat(),
        "token": token_to_response(db_token)
    }

@app.post("/admin/empleados/{empleado_id}/deactivate-tokens")
def deactivate_employee_tokens(empleado_id: int, db: Session = Depends(get_db)):
    """Desactiva todos los tokens de un empleado (simula despido)"""
    
    tokens = db.query(QRToken).filter(
        and_(
            QRToken.empleado_id == empleado_id,
            QRToken.activo == True
        )
    ).all()
    
    if not tokens:
        return {
            "success": True,
            "message": "No se encontraron tokens activos para este empleado",
            "tokens_desactivados": 0
        }
    
    # Desactivar todos los tokens
    for token in tokens:
        token.activo = False
    
    db.commit()
    
    return {
        "success": True,
        "message": f"Se desactivaron {len(tokens)} tokens del empleado {empleado_id}",
        "tokens_desactivados": len(tokens),
        "empleado_id": empleado_id
    }

@app.post("/admin/cleanup/expired")
def cleanup_expired_tokens(db: Session = Depends(get_db)):
    """Limpia tokens expirados (los desactiva)"""
    
    tokens_expirados = db.query(QRToken).filter(
        and_(
            QRToken.expira_en < datetime.utcnow(),
            QRToken.activo == True
        )
    ).all()
    
    for token in tokens_expirados:
        token.activo = False
    
    db.commit()
    
    return {
        "success": True,
        "message": f"Se desactivaron {len(tokens_expirados)} tokens expirados",
        "tokens_limpiados": len(tokens_expirados)
    }

# ============= ENDPOINTS PARA EMPLEADOS (MANTENIDOS) =============

@app.post("/empleados/tokens/generate", response_model=QRTokenResponse)
def generate_empleado_token(
    empleado_id: int, 
    duracion_horas: Optional[int] = None,
    descripcion: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Genera un token de empleado con duración extendida"""
    request = TokenGenerationRequest(
        empleado_id=empleado_id,
        duracion_horas=duracion_horas,
        tipo_token="empleado",
        descripcion=descripcion
    )
    return generate_qr_token(request, db)

@app.get("/empleados/{empleado_id}/tokens", response_model=List[QRTokenResponse])
def get_empleado_tokens(empleado_id: int, incluir_inactivos: bool = False, db: Session = Depends(get_db)):
    """Obtiene tokens de un empleado con opción de incluir inactivos"""
    query = db.query(QRToken).filter(QRToken.empleado_id == empleado_id)
    
    if not incluir_inactivos:
        query = query.filter(QRToken.activo == True)
    
    tokens = query.all()
    return [token_to_response(token) for token in tokens]

# ============= INFORMACIÓN DEL SISTEMA =============

@app.get("/info")
def get_system_info(db: Session = Depends(get_db)):
    """Información del sistema con estadísticas detalladas"""
    all_tokens = db.query(QRToken).all()
    
    # Calcular estadísticas por estado
    activos = sum(1 for t in all_tokens if calcular_estado_token(t) == "ACTIVO")
    expirados = sum(1 for t in all_tokens if calcular_estado_token(t) == "EXPIRADO")
    desactivados = sum(1 for t in all_tokens if calcular_estado_token(t) == "DESACTIVADO")
    usados = sum(1 for t in all_tokens if calcular_estado_token(t) == "USADO")
    
    # Estadísticas por tipo
    empleado_tokens = sum(1 for t in all_tokens if t.tipo_token == TokenType.EMPLEADO)
    jefe_tokens = sum(1 for t in all_tokens if t.tipo_token == TokenType.JEFE)
    
    # Próximos a expirar (30 días)
    proximos_expirar = sum(1 for t in all_tokens 
                          if calcular_estado_token(t) == "ACTIVO" 
                          and calcular_dias_restantes(t.expira_en) <= 30)
    
    return {
        "app": "QR Token Generator - Gestión Flexible",
        "version": "3.0.0",
        "database": "PostgreSQL (Neon)",
        "estadisticas": {
            "total_tokens": len(all_tokens),
            "por_estado": {
                "activos": activos,
                "expirados": expirados,
                "desactivados": desactivados,
                "usados": usados
            },
            "por_tipo": {
                "empleados": empleado_tokens,
                "jefes": jefe_tokens
            },
            "proximos_expirar_30_dias": proximos_expirar
        },
        "configuracion": {
            "duracion_default": DURACION_DEFAULT,
            "qr_disponible": QR_AVAILABLE
        }
    }

# ============= ENDPOINTS LEGACY (MANTENIDOS PARA COMPATIBILIDAD) =============

@app.get("/generate-qr-token")
def generate_qr_token_legacy(empleado_id: int = 1, db: Session = Depends(get_db)):
    """Genera un token de empleado (LEGACY) - ahora con duración extendida"""
    return generate_empleado_token(empleado_id, None, "Token legacy", db)

@app.get("/validate-token/{token}")
def validate_token_legacy(token: str, db: Session = Depends(get_db)):
    """Valida un token (LEGACY)"""
    return validate_token(token, db)

@app.post("/use-token/{token}")
def use_token_legacy(token: str, db: Session = Depends(get_db)):
    """Usa un token (LEGACY)"""
    return use_token(token, db)

@app.post("/tokens/{token}/use")
def use_token(token: str, db: Session = Depends(get_db)):
    """Marca un token como usado"""
    
    db_token = db.query(QRToken).filter(QRToken.token == token).first()
    
    if not db_token:
        return {"success": False, "message": "Token no encontrado"}
    
    if not db_token.activo:
        return {"success": False, "message": "Token desactivado"}
    
    if db_token.usado:
        return {"success": False, "message": "Token ya fue usado"}
    
    if datetime.utcnow() > db_token.expira_en:
        return {"success": False, "message": "Token expirado"}
    
    # Marcar como usado
    db_token.usado = True
    db_token.usado_en = datetime.utcnow()
    db.commit()
    
    return {
        "success": True,
        "message": "Token usado exitosamente",
        "empleado_id": db_token.empleado_id,
        "tipo_token": db_token.tipo_token.value,
        "departamento": db_token.departamento,
        "usado_en": db_token.usado_en.isoformat(),
        "estado": "USADO"
    }