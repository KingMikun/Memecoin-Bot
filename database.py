"""
Schema is intentionally small. Four tables, each earns its place.
"""
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, Boolean, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

from config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Wallet(Base):
    """A tracked wallet, optionally labeled by the user."""
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True)
    address = Column(String, index=True, nullable=False)
    chain = Column(String, nullable=False)
    label = Column(String, default="")
    added_by = Column(String, default="")  # telegram user id
    added_at = Column(DateTime, default=datetime.utcnow)
    win_count = Column(Integer, default=0)
    loss_count = Column(Integer, default=0)

    trades = relationship("Trade", back_populates="wallet")

    @property
    def win_rate(self):
        total = self.win_count + self.loss_count
        return round(self.win_count / total * 100, 1) if total else None


class Trade(Base):
    """Every buy/sell a tracked wallet makes, as it comes in off the webhook."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"))
    chain = Column(String, nullable=False)
    token_address = Column(String, nullable=False)
    token_symbol = Column(String, default="")
    action = Column(String)  # "buy" or "sell"
    amount_usd = Column(Float, default=0.0)
    entry_mcap = Column(Float, nullable=True)
    tx_hash = Column(String, default="")
    timestamp = Column(DateTime, default=datetime.utcnow)

    wallet = relationship("Wallet", back_populates="trades")


class Alert(Base):
    """A confluence alert that was actually pushed to Telegram — dedupe log."""
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)
    chain = Column(String, nullable=False)
    token_address = Column(String, nullable=False)
    score = Column(Float)
    wallet_count = Column(Integer)
    sent_at = Column(DateTime, default=datetime.utcnow)
    passed_security = Column(Boolean, default=False)


class Subscriber(Base):
    """Telegram users/chats who should receive alerts (supports multi-user later)."""
    __tablename__ = "subscribers"

    id = Column(Integer, primary_key=True)
    chat_id = Column(String, unique=True, nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_session():
    return SessionLocal()
