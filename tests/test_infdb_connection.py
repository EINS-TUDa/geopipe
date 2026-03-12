from pypeline.data.database_connection import DatabaseConnection


def test_***REMOVED***_is_available():
    conn = DatabaseConnection(
        host="ds1.example.com",
        port=54328,
        database="***REMOVED***",
        user="***REMOVED***",
        password="***REMOVED***",
    )
    assert conn.is_available(), "infDB on ds1.example.com:54328 is not reachable; check connectivity or credentials"
