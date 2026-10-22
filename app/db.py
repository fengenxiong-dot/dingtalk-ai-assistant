from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, create_engine, event, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Gate(Base):
    __tablename__ = "write_gate"
    id: Mapped[int] = mapped_column(primary_key=True)
    tick: Mapped[int] = mapped_column(default=0)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("corp_id", "staff_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    corp_id: Mapped[str] = mapped_column(String(128))
    staff_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    text_limit: Mapped[int] = mapped_column(default=50)
    image_limit: Mapped[int] = mapped_column(default=5)
    active_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="新对话")
    tags: Mapped[str] = mapped_column(String(300), default="")
    archived: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_through: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Receipt(Base):
    __tablename__ = "receipts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    prompt: Mapped[str] = mapped_column(Text)
    source_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    channel: Mapped[str] = mapped_column(String(20), default="local")
    result: Mapped[str] = mapped_column(Text, default="")
    result_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(String(500), default="")
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    credits: Mapped[int] = mapped_column(default=0)
    reserve_fen: Mapped[int] = mapped_column(default=0)
    day: Mapped[str] = mapped_column(String(10), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, default="")
    path: Mapped[str] = mapped_column(Text, default="")
    download_code: Mapped[str] = mapped_column(Text, default="")
    expired: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    error: Mapped[str] = mapped_column(String(500), default="")


class Database:
    def __init__(self, settings):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if settings.database_url.startswith("sqlite:///"):
            Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}, pool_pre_ping=True)
        if settings.database_url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def init(self):
        Base.metadata.create_all(self.engine)
        with self.Session.begin() as session:
            if session.get(Gate, 1) is None:
                session.add(Gate(id=1, tick=0))

    @contextmanager
    def write(self):
        with self.Session.begin() as session:
            # A single short database lock serializes quota reservation and deduplication.
            # No network I/O is allowed while this transaction is open.
            session.execute(update(Gate).where(Gate.id == 1).values(tick=Gate.tick + 1))
            yield session
