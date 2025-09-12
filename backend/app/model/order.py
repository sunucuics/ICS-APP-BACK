import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.sql import func
from app.database import Base  # SQLAlchemy Base declarative base

class Order(Base):
    __tablename__ = "orders"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # UUID primary key
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    tracking_number = Column(String, nullable=True)      # Aras tracking number (InvoiceKey)
    status = Column(String, default="Shipped")           # Order status (e.g. "Shipped", "Delivered")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
