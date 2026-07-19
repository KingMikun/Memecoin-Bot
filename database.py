"""
Schema is intentionally small. Four tables, each earns its place.
"""
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, Boolean, ForeignKey, inspect, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
import logging

from config import DATABASE_URL

logger = logging.getLogger(__name__)

def _normalize_database_url(url: str) -> str:
    """
    Railway injects DATABASE_URL as postgresql:// (sometimes the older
    postgres:// form). SQLAlchemy defaults bare postgresql:// to the psycopg2
    dialect, which depends on the system's libpq shared library — not
    reliably present on Railway's slim Python runtime, causing
    'libpq.so.5: cannot open shared object file' at import time even when
    psycopg2-binary installed fine at build time. Forcing the pg8000 dialect
    (pure Python, no native library at all) sidesteps that failure mode
    entirely.
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://") and "+pg8000" not in url:
        url = url.replace("postgresql://", "postgresql+pg8000://", 1)
    return url


DATABASE_URL = _normalize_database_url(DATABASE_URL)

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
    token_amount = Column(Float, default=0.0)  # raw quantity transacted
    amount_usd = Column(Float, default=0.0)    # token_amount * price at ingest time; 0 if price unavailable
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


_SQL_TYPE_MAP = {
    Integer: "INTEGER",
    String: "VARCHAR",
    Float: "FLOAT",
    DateTime: "TIMESTAMP",
    Boolean: "BOOLEAN",
}


def _column_sql_type(column: Column) -> str:
    for py_type, sql_type in _SQL_TYPE_MAP.items():
        if isinstance(column.type, py_type):
            return sql_type
    return "VARCHAR"  # safe fallback, shouldn't hit this with our current models


def _auto_migrate():
    """
    create_all() only creates brand-new tables — it silently does nothing to
    a table that already exists but is missing columns a newer model added
    (exactly what happened when token_amount/entry_mcap were added to Trade
    after the table already existed in production). This diffs the live DB
    against the model on every startup and adds whatever's missing, so a
    forgotten manual migration can't break trade ingestion or /wallethistory
    ever again.
    """
    inspector = inspect(engine)
    for table_name, table in Base.metadata.tables.items():
        if table_name not in inspector.get_table_names():
            continue  # brand new table, create_all() already handled it
        existing_cols = {col["name"] for col in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name in existing_cols:
                continue
            sql_type = _column_sql_type(column)
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column.name} {sql_type}"))
                logger.info(f"[auto_migrate] Added missing column {table_name}.{column.name} ({sql_type})")
            except Exception:
                logger.exception(f"[auto_migrate] Failed to add {table_name}.{column.name} — check DB permissions")


def init_db():
    Base.metadata.create_all(bind=engine)
    _auto_migrate()


def get_session():
    return SessionLocal()
