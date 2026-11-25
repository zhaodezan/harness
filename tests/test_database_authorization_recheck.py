from backend.app.database_manager import DatabaseManager
from backend.app.db import database_connection, initialize_database


class Secrets:
    def get(self, key): return None
    def set(self, key, value): pass
    def delete(self, key): pass


def test_database_tool_authorization_is_rechecked_against_current_agent_grants(tmp_path):
    database = tmp_path / "app.sqlite3"
    initialize_database(database)
    with database_connection(database) as connection:
        connection.execute(
            "INSERT INTO database_connections(id,name,db_type) VALUES('db-1','DB','sqlite')"
        )
        connection.execute(
            "INSERT INTO agents(id,name,selected_database_connections_json) VALUES('agent-1','Agent','[\"db-1\"]')"
        )
    manager = DatabaseManager(database, Secrets())
    plugin_id = "database:db-1:query"
    assert manager.is_tool_authorized("agent-1", plugin_id) is True
    with database_connection(database) as connection:
        connection.execute("UPDATE agents SET selected_database_connections_json='[]' WHERE id='agent-1'")
    assert manager.is_tool_authorized("agent-1", plugin_id) is False
    assert manager.is_tool_authorized("agent-1", plugin_id, draft_connection_ids=["db-1"]) is True
    with database_connection(database) as connection:
        connection.execute("DELETE FROM database_connections WHERE id='db-1'")
    assert manager.is_tool_authorized("agent-1", plugin_id, draft_connection_ids=["db-1"]) is False
