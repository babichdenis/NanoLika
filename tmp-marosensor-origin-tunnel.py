#!/usr/bin/env python3
"""TCP-туннель к origin marosensor.ru через резервный HTTP-прокси.

Нужен, когда хостинг блокирует IP нашего сервера: nginx ходит на
127.0.0.1:8443, а соединение до 141.8.192.217:443 открывается через
HTTP CONNECT на 127.0.0.1:18888 (туннель в Ригу).
"""
import select
import socket
import threading

LISTEN = ("127.0.0.1", 8443)
PROXY = ("127.0.0.1", 18888)
TARGET = ("141.8.192.217", 443)


def connect_via_proxy() -> socket.socket:
    sock = socket.create_connection(PROXY, timeout=15)
    request = (
        f"CONNECT {TARGET[0]}:{TARGET[1]} HTTP/1.1\r\n"
        f"Host: {TARGET[0]}:{TARGET[1]}\r\n"
        "Proxy-Connection: keep-alive\r\n\r\n"
    )
    sock.sendall(request.encode("ascii"))
    reply = b""
    while b"\r\n\r\n" not in reply:
        chunk = sock.recv(4096)
        if not chunk:
            raise OSError("proxy closed connection")
        reply += chunk
        if len(reply) > 65536:
            raise OSError("proxy reply too large")
    status = reply.split(b"\r\n", 1)[0]
    if b" 200 " not in status:
        raise OSError(f"proxy refused: {status!r}")
    sock.settimeout(None)
    return sock


def pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def handle(client: socket.socket) -> None:
    try:
        upstream = connect_via_proxy()
    except OSError:
        client.close()
        return
    left = threading.Thread(target=pump, args=(client, upstream), daemon=True)
    left.start()
    pump(upstream, client)
    left.join(timeout=5)
    client.close()
    upstream.close()


def main() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(LISTEN)
    server.listen(128)
    while True:
        client, _ = server.accept()
        client.settimeout(None)
        threading.Thread(target=handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
