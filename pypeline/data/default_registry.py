from pypeline.data import (
    DataRegistry,
    DatabaseConnection,
    PostgreSQLTableDataset,
    PostgreSQLColumnDataset,
)

***REMOVED*** = DatabaseConnection(
    host="ds1.example.com",
    port=54328,
    database="***REMOVED***",
    user="***REMOVED***",
    password="***REMOVED***"
)

print( ***REMOVED***.is_available() )





registry = DataRegistry()

