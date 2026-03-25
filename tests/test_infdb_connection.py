import os
import pytest
from pypeline.data.database_connection import DatabaseConnection

def ***REMOVED***_available_t():
    """Checks external infDB endpoint is reachable with configured credentials."""
    if os.getenv("RUN_INFDB_TESTS", "0") != "1":
        pytest.skip("Set RUN_INFDB_TESTS=1 to run external infDB connectivity test")
    conn = DatabaseConnection(
        host="ds1.example.com",
        port=54328,
        database="***REMOVED***",
        user="***REMOVED***",
        password="***REMOVED***",
    )
    assert conn.is_available(), "infDB on ds1.example.com:54328 is not reachable; check connectivity or credentials"
