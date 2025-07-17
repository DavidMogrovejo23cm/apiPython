from typing import Union, Optional, List
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
import secrets
import string
from datetime import datetime, timedelta
from pydantic import BaseModel
from database import get_db, create_tables, QRToken, TokenType
from sqlalchemy import and_

# Importación condicional de qrcode
try:
    import qrcode
    from io import BytesIO
    import base64
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False

app = FastAPI(title="QR Token API", description="API para generar tokens QR para empleados y jefes")

# Crear tablas al iniciar
create_tables()

# ============= MODELOS PYDANTIC =============

class TokenGenerationRequest(BaseModel):
    empleado_id: int
    duracion_horas: int = 1
    tipo_token: str  # "empleado" o "jefe"
    departamento: Optional[str] = None  # Solo para jefes
    permisos_especiales: Optional[str] = None  # Solo para jefes

class TokenUsageRequest(BaseModel):
    token: str
    empleado_id: int

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
    activo: bool

class TokenValidationResponse(BaseModel):
    valid: bool
    message: str
    token_data: Optional[dict] = None

# ============= FUNCIONES AUXILIARES =============

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

def token_to_response(token: QRToken) -> QRTokenResponse:
    """Convierte un token de la DB a respuesta"""
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
        activo=token.activo
    )

# ============= ENDPOINTS PRINCIPALES =============

@app.get("/")
def read_root():
    return {"Hello": "QR Token API", "version": "2.0.0"}

@app.post("/tokens/generate", response_model=QRTokenResponse)
def generate_qr_token(request: TokenGenerationRequest, db: Session = Depends(get_db)):
    """Genera un nuevo QR token para empleado o jefe"""
    
    # Validar tipo de token
    if request.tipo_token not in ["empleado", "jefe"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tipo_token debe ser 'empleado' o 'jefe'"
        )
    
    # Validar campos requeridos para jefes
    if request.tipo_token == "jefe":
        if not request.departamento:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El departamento es requerido para tokens de jefe"
            )
    
    # Generar token único
    token = generate_token()
    
    # Verificar que el token sea único
    while db.query(QRToken).filter(QRToken.token == token).first():
        token = generate_token()
    
    # Generar QR code
    qr_code_base64 = generate_qr_code(token)
    
    # Crear el token en la base de datos
    db_token = QRToken(
        token=token,
        empleado_id=request.empleado_id,
        tipo_token=TokenType.EMPLEADO if request.tipo_token == "empleado" else TokenType.JEFE,
        expira_en=datetime.utcnow() + timedelta(hours=request.duracion_horas),
        qr_code_base64=qr_code_base64,
        departamento=request.departamento if request.tipo_token == "jefe" else None,
        permisos_especiales=request.permisos_especiales if request.tipo_token == "jefe" else None
    )
    
    db.add(db_token)
    db.commit()
    db.refresh(db_token)
    
    return token_to_response(db_token)

@app.get("/tokens/{token}/validate", response_model=TokenValidationResponse)
def validate_token(token: str, db: Session = Depends(get_db)):
    """Valida si un token existe y no ha expirado"""
    
    db_token = db.query(QRToken).filter(
        and_(
            QRToken.token == token,
            QRToken.activo == True
        )
    ).first()
    
    if not db_token:
        return TokenValidationResponse(
            valid=False,
            message="Token no encontrado o inactivo"
        )
    
    if db_token.usado:
        return TokenValidationResponse(
            valid=False,
            message="Token ya fue usado",
            token_data={
                "usado_en": db_token.usado_en.isoformat() if db_token.usado_en else None,
                "tipo_token": db_token.tipo_token.value
            }
        )
    
    if datetime.utcnow() > db_token.expira_en:
        return TokenValidationResponse(
            valid=False,
            message="Token expirado"
        )
    
    return TokenValidationResponse(
        valid=True,
        message="Token válido",
        token_data={
            "empleado_id": db_token.empleado_id,
            "tipo_token": db_token.tipo_token.value,
            "departamento": db_token.departamento,
            "permisos_especiales": db_token.permisos_especiales,
            "expira_en": db_token.expira_en.isoformat()
        }
    )

@app.post("/tokens/{token}/use")
def use_token(token: str, db: Session = Depends(get_db)):
    """Marca un token como usado"""
    
    db_token = db.query(QRToken).filter(
        and_(
            QRToken.token == token,
            QRToken.activo == True
        )
    ).first()
    
    if not db_token:
        return {"success": False, "message": "Token no encontrado o inactivo"}
    
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
        "usado_en": db_token.usado_en.isoformat()
    }

# ============= ENDPOINTS PARA EMPLEADOS =============

@app.post("/empleados/tokens/generate", response_model=QRTokenResponse)
def generate_empleado_token(empleado_id: int, duracion_horas: int = 1, db: Session = Depends(get_db)):
    """Genera un token específico para empleado"""
    request = TokenGenerationRequest(
        empleado_id=empleado_id,
        duracion_horas=duracion_horas,
        tipo_token="empleado"
    )
    return generate_qr_token(request, db)

@app.get("/empleados/{empleado_id}/tokens", response_model=List[QRTokenResponse])
def get_empleado_tokens(empleado_id: int, db: Session = Depends(get_db)):
    """Obtiene todos los tokens de un empleado específico"""
    tokens = db.query(QRToken).filter(
        and_(
            QRToken.empleado_id == empleado_id,
            QRToken.tipo_token == TokenType.EMPLEADO,
            QRToken.activo == True
        )
    ).all()
    
    return [token_to_response(token) for token in tokens]

# ============= ENDPOINTS PARA JEFES =============

@app.post("/jefes/tokens/generate", response_model=QRTokenResponse)
def generate_jefe_token(
    empleado_id: int,
    departamento: str,
    duracion_horas: int = 1,
    permisos_especiales: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Genera un token específico para jefe"""
    request = TokenGenerationRequest(
        empleado_id=empleado_id,
        duracion_horas=duracion_horas,
        tipo_token="jefe",
        departamento=departamento,
        permisos_especiales=permisos_especiales
    )
    return generate_qr_token(request, db)

@app.get("/jefes/{empleado_id}/tokens", response_model=List[QRTokenResponse])
def get_jefe_tokens(empleado_id: int, db: Session = Depends(get_db)):
    """Obtiene todos los tokens de un jefe específico"""
    tokens = db.query(QRToken).filter(
        and_(
            QRToken.empleado_id == empleado_id,
            QRToken.tipo_token == TokenType.JEFE,
            QRToken.activo == True
        )
    ).all()
    
    return [token_to_response(token) for token in tokens]

@app.get("/jefes/departamento/{departamento}/tokens", response_model=List[QRTokenResponse])
def get_tokens_by_departamento(departamento: str, db: Session = Depends(get_db)):
    """Obtiene todos los tokens de jefes de un departamento específico"""
    tokens = db.query(QRToken).filter(
        and_(
            QRToken.departamento == departamento,
            QRToken.tipo_token == TokenType.JEFE,
            QRToken.activo == True
        )
    ).all()
    
    return [token_to_response(token) for token in tokens]

# ============= ENDPOINTS GENERALES =============

@app.get("/tokens", response_model=List[QRTokenResponse])
def get_all_tokens(
    tipo_token: Optional[str] = None,
    activo: Optional[bool] = True,
    db: Session = Depends(get_db)
):
    """Obtiene todos los tokens con filtros opcionales"""
    query = db.query(QRToken)
    
    if activo is not None:
        query = query.filter(QRToken.activo == activo)
    
    if tipo_token:
        if tipo_token == "empleado":
            query = query.filter(QRToken.tipo_token == TokenType.EMPLEADO)
        elif tipo_token == "jefe":
            query = query.filter(QRToken.tipo_token == TokenType.JEFE)
    
    tokens = query.all()
    return [token_to_response(token) for token in tokens]

@app.delete("/tokens/{token}")
def delete_token(token: str, db: Session = Depends(get_db)):
    """Elimina (desactiva) un token específico"""
    db_token = db.query(QRToken).filter(QRToken.token == token).first()
    
    if not db_token:
        return {"success": False, "message": "Token no encontrado"}
    
    db_token.activo = False
    db.commit()
    
    return {"success": True, "message": "Token desactivado exitosamente"}

@app.delete("/tokens")
def delete_all_tokens(db: Session = Depends(get_db)):
    """Desactiva todos los tokens"""
    db.query(QRToken).update({QRToken.activo: False})
    db.commit()
    
    return {"success": True, "message": "Todos los tokens desactivados"}

@app.get("/info")
def get_system_info(db: Session = Depends(get_db)):
    """Información del sistema"""
    total_tokens = db.query(QRToken).filter(QRToken.activo == True).count()
    active_tokens = db.query(QRToken).filter(
        and_(QRToken.activo == True, QRToken.usado == False)
    ).count()
    used_tokens = db.query(QRToken).filter(
        and_(QRToken.activo == True, QRToken.usado == True)
    ).count()
    
    empleado_tokens = db.query(QRToken).filter(
        and_(QRToken.activo == True, QRToken.tipo_token == TokenType.EMPLEADO)
    ).count()
    
    jefe_tokens = db.query(QRToken).filter(
        and_(QRToken.activo == True, QRToken.tipo_token == TokenType.JEFE)
    ).count()
    
    return {
        "app": "QR Token Generator",
        "version": "2.0.0",
        "database": "PostgreSQL (Neon)",
        "total_tokens": total_tokens,
        "active_tokens": active_tokens,
        "used_tokens": used_tokens,
        "empleado_tokens": empleado_tokens,
        "jefe_tokens": jefe_tokens,
        "qr_available": QR_AVAILABLE
    }

# ============= ENDPOINTS LEGACY =============

@app.get("/generate-qr-token")
def generate_qr_token_legacy(empleado_id: int = 1, db: Session = Depends(get_db)):
    """Genera un token de empleado (LEGACY)"""
    return generate_empleado_token(empleado_id, 1, db)

@app.get("/validate-token/{token}")
def validate_token_legacy(token: str, db: Session = Depends(get_db)):
    """Valida un token (LEGACY)"""
    return validate_token(token, db)

@app.post("/use-token/{token}")
def use_token_legacy(token: str, db: Session = Depends(get_db)):
    """Usa un token (LEGACY)"""
    return use_token(token, db)