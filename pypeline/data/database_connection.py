from typing import Optional
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError, OperationalError

class DatabaseConnection:
    """
    Manages a connection to a database server.

    This class can be shared across multiple datasets that access
    different schemas or tables in the same database.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = None,
        user: str = None,
        password: str = None,
    ):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self._engine: Optional[Engine] = None

    def get_engine(self) -> Engine:
        """
        Gets or creates the SQLAlchemy Engine.
        The engine is created lazily on first access and then reused.

        Returns:
            SQLAlchemy Engine instance
        """
        if self._engine is None:
            connection_string = (
                f"postgresql://{self.user}:{self.password}@"
                f"{self.host}:{self.port}/{self.database}"
            )
            self._engine = create_engine(connection_string)
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

    def connection_string_full(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"

