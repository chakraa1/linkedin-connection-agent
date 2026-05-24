"""
Connection Scheduler — SQLite state machine for LinkedIn profile outreach lifecycle.

States:
  discovered → analyzed → message_drafted → approved → sent
                                           → rejected  (human rejected)
                                                     → failed  (send error)

Deduplication: profile_url is unique — same person cannot enter the pipeline twice.
"""
import uuid
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from sqlalchemy import Column, DateTime, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session

DB_PATH = Path("outputs/scheduler.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

console = Console()


class Base(DeclarativeBase):
    pass


class ConnectionProfile(Base):
    __tablename__ = "connection_profiles"

    id = Column(String, primary_key=True)
    profile_url = Column(String, unique=True)
    profile_name = Column(String)
    profile_headline = Column(String)
    icp_key = Column(String)
    profile_data = Column(Text)
    recent_posts = Column(Text)
    pdf_path = Column(String)
    message = Column(Text)
    message_feedback = Column(Text)
    status = Column(String, default="discovered")
    linkedin_invitation_id = Column(String)
    error_message = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ConnectionScheduler:
    def __init__(self):
        self._engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
        Base.metadata.create_all(self._engine)

    def save_discovered(
        self,
        profile_url: str,
        profile_name: str,
        profile_headline: str = "",
        icp_key: str = "icp1",
    ) -> str:
        with Session(self._engine) as session:
            existing = session.query(ConnectionProfile).filter_by(profile_url=profile_url).first()
            if existing:
                return existing.id
            profile_id = str(uuid.uuid4())
            session.add(
                ConnectionProfile(
                    id=profile_id,
                    profile_url=profile_url,
                    profile_name=profile_name,
                    profile_headline=profile_headline,
                    icp_key=icp_key,
                    status="discovered",
                )
            )
            session.commit()
        return profile_id

    def save_analyzed(
        self,
        profile_id: str,
        profile_data: str,
        recent_posts: str,
        pdf_path: str = "",
    ) -> None:
        with Session(self._engine) as session:
            record = session.get(ConnectionProfile, profile_id)
            if record:
                record.profile_data = profile_data
                record.recent_posts = recent_posts
                record.pdf_path = pdf_path
                record.status = "analyzed"
                session.commit()

    def save_message(self, profile_id: str, message: str) -> None:
        with Session(self._engine) as session:
            record = session.get(ConnectionProfile, profile_id)
            if record:
                record.message = message
                record.status = "message_drafted"
                session.commit()

    def approve_message(self, profile_id: str, feedback: str = "") -> None:
        with Session(self._engine) as session:
            record = session.get(ConnectionProfile, profile_id)
            if record:
                record.status = "approved"
                record.message_feedback = feedback
                session.commit()

    def reject_message(self, profile_id: str, feedback: str = "") -> None:
        with Session(self._engine) as session:
            record = session.get(ConnectionProfile, profile_id)
            if record:
                record.status = "rejected"
                record.message_feedback = feedback
                session.commit()

    def mark_sent(self, profile_id: str, invitation_id: str = "") -> None:
        with Session(self._engine) as session:
            record = session.get(ConnectionProfile, profile_id)
            if record:
                record.status = "sent"
                record.linkedin_invitation_id = invitation_id
                session.commit()

    def mark_failed(self, profile_id: str, error: str) -> None:
        with Session(self._engine) as session:
            record = session.get(ConnectionProfile, profile_id)
            if record:
                record.status = "failed"
                record.error_message = error
                session.commit()

    def list_by_status(self, status: str) -> list:
        with Session(self._engine) as session:
            records = (
                session.query(ConnectionProfile)
                .filter_by(status=status)
                .order_by(ConnectionProfile.created_at)
                .all()
            )
            session.expunge_all()
            return records

    def get(self, profile_id: str):
        with Session(self._engine) as session:
            record = session.get(ConnectionProfile, profile_id)
            if record:
                session.expunge(record)
            return record

    def list_all(self) -> None:
        with Session(self._engine) as session:
            records = (
                session.query(ConnectionProfile)
                .order_by(ConnectionProfile.created_at.desc())
                .all()
            )

        table = Table(title="Connection Profiles", show_lines=True)
        table.add_column("ID", style="cyan", max_width=10)
        table.add_column("Name", style="white")
        table.add_column("Headline", style="dim", max_width=35)
        table.add_column("Status", style="green")
        table.add_column("ICP", style="yellow")
        table.add_column("Created", style="dim")

        for r in records:
            table.add_row(
                r.id[:8],
                r.profile_name or "—",
                (r.profile_headline or "")[:35],
                r.status,
                r.icp_key or "—",
                r.created_at.strftime("%Y-%m-%d") if r.created_at else "—",
            )
        console.print(table)
