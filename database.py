from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

# URL de conexión a Neon
DATABASE_URL = 'postgresql://neondb_owner:npg_21fFSKavmgOE@ep-gentle-term-ae4qpxn7-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require'

# Crear engine
engine = create_engine(DATABASE_URL)

# Crear SessionLocal
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base para los modelos
Base = declarative_base()

# Modelo para los códigos QR
class QRCode(Base):
    __tablename__ = "qr_codes"
    
    id = Column(Integer, primary_key=True, index=True)
    empleado_id = Column(Integer, nullable=False, index=True)
    qr_code_base64 = Column(Text, nullable=False)
    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)
    activo = Column(Boolean, default=True, nullable=False)
    
    # Relación con registros de escaneo
    escaneos = relationship("RegistroEscaneo", back_populates="qr_code")

# Modelo para los registros de escaneo
class RegistroEscaneo(Base):
    __tablename__ = "registros_escaneo"
    
    id = Column(Integer, primary_key=True, index=True)
    qr_id = Column(Integer, ForeignKey("qr_codes.id"), nullable=False)
    empleado_id = Column(Integer, nullable=False)
    fecha = Column(DateTime, default=datetime.utcnow, nullable=False)  # Solo la fecha del registro
    hora_entrada = Column(DateTime, nullable=False)  # Hora de entrada
    hora_salida = Column(DateTime, nullable=True)    # Hora de salida (puede ser null)
    
    # Relación con QR code
    qr_code = relationship("QRCode", back_populates="escaneos")

# Función para reiniciar la base de datos
def reset_database():
    """Elimina todas las tablas y las vuelve a crear"""
    print("🔄 Reiniciando base de datos...")
    
    # Eliminar todas las tablas
    Base.metadata.drop_all(bind=engine)
    print("✅ Tablas eliminadas")
    
    # Crear las tablas nuevamente
    Base.metadata.create_all(bind=engine)
    print("✅ Tablas creadas")
    print("🎉 Base de datos reiniciada correctamente")

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