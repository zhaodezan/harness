from backend.app.launcher import validate_bind_host


def test_launcher_refuses_non_loopback_bind_hosts():
    try:
        validate_bind_host("0.0.0.0")
    except ValueError as error:
        assert "127.0.0.1" in str(error)
    else:
        raise AssertionError("non-loopback bind host was accepted")


def test_launcher_accepts_loopback_hosts():
    assert validate_bind_host("127.0.0.1") == "127.0.0.1"
    assert validate_bind_host("::1") == "::1"
