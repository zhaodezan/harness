import ipaddress
import socket


def validate_bind_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("本地应用只能监听 127.0.0.1 或 ::1") from None
    if not address.is_loopback:
        raise ValueError("本地应用只能监听 127.0.0.1 或 ::1")
    return host


def find_available_port(host: str, preferred_port: int = 8765) -> int:
    validate_bind_host(host)
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, preferred_port))
        return sock.getsockname()[1]
