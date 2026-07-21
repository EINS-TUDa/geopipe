from typing import Optional
from sqlalchemy import create_engine, text, URL
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from dotenv import find_dotenv, load_dotenv
import os

class PostgresConnection:
    """
    Manages a connection to a database server.

    This class can be shared across multiple datasets that access
    different schemas or tables in the same database.
    """

    def __init__(
        self,
        host: str, # = "localhost",
        port: int, # = 5432,
        database: str, # = None,
        user: str, # = None,
        password: str, # = None,
    ):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self._engine: Optional[Engine] = None

    @classmethod
    def from_env(cls, prefix: str ) -> "PostgresConnection":
        """
        Build a connection from {PREFIX}_HOST/_PORT/_DATABASE/_USER/_PASSWORD.
        """
        load_dotenv()
        def req(name: str) -> str:
            val = os.environ.get(f"{prefix}_{name}")
            if not val:
                raise RuntimeError(f"Missing env var {prefix}_{name} (see .env.template)")
            return val
        return cls(
            host=req("HOST"),
            port=int(req("PORT")),
            database=req("DATABASE"),
            user=req("USER"),
            password=req("PASSWORD"),
        )

    def get_engine(self) -> Engine:
        """
        Gets or creates the SQLAlchemy Engine.
        The engine is created lazily on first access and then reused.

        Returns:
            SQLAlchemy Engine instance
        """
        url = URL.create(
            "postgresql",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )
        self._engine = create_engine(url)
        return self._engine

    def is_available(self) -> bool:
        try:
            engine = self.get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except (OperationalError, SQLAlchemyError):
            return False

    def __repr__(self) -> str:
        return f"DatabaseConnection(host='{self.host}', database='{self.database}')"
