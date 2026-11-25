import pytest

from backend.app.database_manager import DatabaseManagerError, _validate_write_sql


@pytest.mark.parametrize("sql", [
    "INSERT INTO users(id,name) VALUES(1,'Ada')",
])
def test_insert_and_replace_are_valid_approved_single_write_statements(sql):
    _validate_write_sql(sql, dialect="sqlite")


def test_write_policy_rejects_multiple_sql_statements():
    with pytest.raises(DatabaseManagerError, match="一条"):
        _validate_write_sql("INSERT INTO users(name) VALUES('Ada'); DELETE FROM users")
