"""Database tables. Works on SQLite (local) and PostgreSQL (cloud) unchanged."""
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .db import Base, now


class ModelMixin:
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=now)

    def to_dict(self) -> dict:
        out = {}
        for col in self.__table__.columns:
            value = getattr(self, col.name)
            if isinstance(value, (datetime, date)):
                value = value.isoformat()
            out[col.name] = value
        return out


class Staff(ModelMixin, Base):
    """Admin panel users AND the WhatsApp staff allowlist (by phone)."""
    __tablename__ = "staff"
    name = Column(String(100), nullable=False)
    username = Column(String(50), unique=True)
    password_hash = Column(String(200))
    phone = Column(String(20), unique=True)  # normalised, e.g. 923001234567
    role = Column(String(20), default="sales")  # owner | sales | service | parts
    active = Column(Boolean, default=True)


class Customer(ModelMixin, Base):
    __tablename__ = "customers"
    name = Column(String(100))
    phone = Column(String(20), unique=True, index=True)
    email = Column(String(120))
    city = Column(String(60))
    language = Column(String(20))
    memory = Column(Text)  # AI-written summary of past conversations
    notes = Column(Text)
    vehicles = relationship("CustomerVehicle", back_populates="customer")


class CarModel(ModelMixin, Base):
    """Catalog of variants the showroom sells (also upcoming launches)."""
    __tablename__ = "car_models"
    make = Column(String(40), nullable=False)
    model = Column(String(40), nullable=False)
    variant = Column(String(60), nullable=False)
    body_type = Column(String(20))  # sedan | hatchback | suv | crossover | pickup
    fuel = Column(String(20))  # petrol | hybrid | diesel | electric
    transmission = Column(String(20))
    engine_cc = Column(Integer)
    seats = Column(Integer)
    fuel_avg_kmpl = Column(Float)
    price = Column(Integer)  # ex-showroom price
    features = Column(Text)
    is_upcoming = Column(Boolean, default=False)
    launch_date = Column(Date)
    active = Column(Boolean, default=True)

    @property
    def full_name(self) -> str:
        return f"{self.make} {self.model} {self.variant}"


class Vehicle(ModelMixin, Base):
    """A physical car in stock (new or used)."""
    __tablename__ = "vehicles"
    car_model_id = Column(Integer, ForeignKey("car_models.id"), nullable=False)
    condition = Column(String(10), default="new")  # new | used
    year = Column(Integer)
    color = Column(String(30))
    price = Column(Integer)  # selling price for this unit
    mileage_km = Column(Integer, default=0)
    chassis_no = Column(String(40), unique=True)
    reg_no = Column(String(20))
    status = Column(String(15), default="available")  # available | booked | sold
    arrived_at = Column(Date, default=lambda: now().date())
    notes = Column(Text)
    car_model = relationship("CarModel")


class Part(ModelMixin, Base):
    __tablename__ = "parts"
    part_no = Column(String(40), unique=True)
    name = Column(String(120), nullable=False)
    category = Column(String(40))
    compatible_models = Column(Text)  # e.g. "Honda Civic 2022-2025; Honda City 2021-2025"
    price = Column(Integer)
    stock = Column(Integer, default=0)
    min_stock = Column(Integer, default=2)
    is_genuine = Column(Boolean, default=True)


class ServiceType(ModelMixin, Base):
    __tablename__ = "service_types"
    name = Column(String(80), nullable=False)
    description = Column(Text)
    price_min = Column(Integer)
    price_max = Column(Integer)
    duration_hours = Column(Float)


class CustomerVehicle(ModelMixin, Base):
    """A car owned by a customer - used for service history and reminders."""
    __tablename__ = "customer_vehicles"
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    reg_no = Column(String(20))
    model_name = Column(String(80))
    year = Column(Integer)
    last_service_date = Column(Date)
    last_service_km = Column(Integer)
    next_service_due = Column(Date)
    reminder_sent_for = Column(Date)  # due-date we already reminded about
    customer = relationship("Customer", back_populates="vehicles")


class ServiceAppointment(ModelMixin, Base):
    __tablename__ = "service_appointments"
    job_no = Column(String(20), unique=True)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    reg_no = Column(String(20))
    car_model = Column(String(80))
    service_type = Column(String(80))
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String(15), default="booked")  # booked | in_progress | ready | delivered | cancelled
    complaint = Column(Text)
    bill_amount = Column(Integer)
    source = Column(String(15), default="bot")
    reminder_sent = Column(Boolean, default=False)
    customer = relationship("Customer")


class TestDrive(ModelMixin, Base):
    __tablename__ = "test_drives"
    customer_id = Column(Integer, ForeignKey("customers.id"))
    car_model_id = Column(Integer, ForeignKey("car_models.id"))
    scheduled_at = Column(DateTime, nullable=False)
    location = Column(String(15), default="showroom")  # showroom | home
    address = Column(Text)
    status = Column(String(15), default="booked")  # booked | done | cancelled | no_show
    reminder_sent = Column(Boolean, default=False)
    customer = relationship("Customer")
    car_model = relationship("CarModel")


class Lead(ModelMixin, Base):
    __tablename__ = "leads"
    customer_id = Column(Integer, ForeignKey("customers.id"))
    interest = Column(Text)  # what they want, e.g. "Civic Oriel, white"
    car_model_id = Column(Integer, ForeignKey("car_models.id"))
    budget = Column(Integer)
    timeline = Column(String(40))  # e.g. "this month"
    wants_financing = Column(Boolean, default=False)
    has_trade_in = Column(Boolean, default=False)
    source = Column(String(15))  # web | whatsapp | voice | walk-in
    status = Column(String(15), default="new")  # new | contacted | test_drive | negotiation | won | lost
    score = Column(Integer, default=0)
    score_label = Column(String(10), default="cold")  # hot | warm | cold
    notes = Column(Text)
    updated_at = Column(DateTime, default=now, onupdate=now)
    customer = relationship("Customer")
    car_model = relationship("CarModel")


class Offer(ModelMixin, Base):
    __tablename__ = "offers"
    title = Column(String(120), nullable=False)
    description = Column(Text)
    valid_from = Column(Date)
    valid_to = Column(Date)
    active = Column(Boolean, default=True)


class Conversation(ModelMixin, Base):
    __tablename__ = "conversations"
    customer_id = Column(Integer, ForeignKey("customers.id"))
    channel = Column(String(15))  # web | whatsapp | voice
    external_id = Column(String(80), index=True)  # web session id / phone / call sid
    status = Column(String(10), default="bot")  # bot | human | closed
    department = Column(String(15), default="general")
    handoff_reason = Column(Text)
    summary = Column(Text)
    unread = Column(Integer, default=0)
    last_message_at = Column(DateTime, default=now)
    customer = relationship("Customer")
    messages = relationship("Message", back_populates="conversation", order_by="Message.id")


class Message(ModelMixin, Base):
    __tablename__ = "messages"
    conversation_id = Column(Integer, ForeignKey("conversations.id"), index=True)
    role = Column(String(10))  # customer | bot | staff | system
    text = Column(Text)
    meta = Column(JSON)
    conversation = relationship("Conversation", back_populates="messages")


class PendingAction(ModelMixin, Base):
    """A staff WhatsApp command waiting for confirmation."""
    __tablename__ = "pending_actions"
    staff_id = Column(Integer, ForeignKey("staff.id"))
    action = Column(JSON)  # {"type": ..., "candidates": [...], "selected": ...}
    status = Column(String(12), default="waiting")  # waiting | done | cancelled | expired


class AuditLog(ModelMixin, Base):
    __tablename__ = "audit_log"
    actor = Column(String(80))  # "staff:Ahmed (whatsapp)", "admin:admin", "bot"
    action = Column(String(40))
    entity = Column(String(40))
    entity_id = Column(Integer)
    old = Column(JSON)
    new = Column(JSON)
    undone = Column(Boolean, default=False)


class Notification(ModelMixin, Base):
    """Outbox for automatic messages (reminders, alerts)."""
    __tablename__ = "notifications"
    customer_id = Column(Integer, ForeignKey("customers.id"))
    channel = Column(String(15))
    to = Column(String(40))
    kind = Column(String(30))  # service_due | appointment_reminder | job_ready | test_drive_reminder | staff_alert
    text = Column(Text)
    status = Column(String(10), default="queued")  # sent | logged | failed
    error = Column(Text)
