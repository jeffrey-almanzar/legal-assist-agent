from app import db


def test_defaults_to_local_database(monkeypatch):
    for name in ('DB_HOST', 'DATABASE_URL'):
        monkeypatch.delenv(name, raising=False)
    assert db.connection_options() == {'dsn': db.DEFAULT_DATABASE_URL}


def test_uses_database_url(monkeypatch):
    monkeypatch.delenv('DB_HOST', raising=False)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://u:p@db:5432/x')
    assert db.connection_options() == {'dsn': 'postgresql://u:p@db:5432/x'}


def test_separate_variables_take_precedence(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://ignored')
    monkeypatch.setenv('DB_HOST', 'legal.abc123.us-east-1.rds.amazonaws.com')
    monkeypatch.setenv('DB_PORT', '5432')
    monkeypatch.setenv('DB_USER', 'legal')
    monkeypatch.setenv('DB_PASSWORD', 'p@ss/w:rd')  # characters that would break a URL
    monkeypatch.setenv('DB_NAME', 'legal')
    assert db.connection_options() == {
        'host': 'legal.abc123.us-east-1.rds.amazonaws.com', 'port': 5432,
        'user': 'legal', 'password': 'p@ss/w:rd', 'database': 'legal',
    }
