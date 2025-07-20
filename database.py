from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import enum

# URL de conexión a Neon
DATABASE_URL = 'postgresql://neondb_owner:npg_21fFSKavmgOE@ep-gentle-term-ae4qpxn7-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require'

# Crear engine
engine = create_engine(DATABASE_URL)

# Crear SessionLocal
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base para los modelos
Base = declarative_base()

# Enum para tipos de token
class TokenType(enum.Enum):
    EMPLEADO = "empleado"
    JEFE = "jefe"

# Modelo para los QR Tokens
class QRToken(Base):
    __tablename__ = "qr_tokens"
    
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(255), unique=True, index=True, nullable=False)
    empleado_id = Column(Integer, nullable=False, index=True)
    tipo_token = Column(Enum(TokenType), nullable=False, index=True)
    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)
    expira_en = Column(DateTime, nullable=False)
    usado = Column(Boolean, default=False, nullable=False)
    usado_en = Column(DateTime, nullable=True)
    qr_code_base64 = Column(Text, nullable=True)
    activo = Column(Boolean, default=True, nullable=False)
    
    # Campos adicionales para jefes
    departamento = Column(String(100), nullable=True)
    permisos_especiales = Column(Text, nullable=True)

# Crear las tablas
def create_tables():
    Base.metadata.create_all(bind=engine)

# Dependency para obtener la sesión de la base de datos
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()