"""Check retired TCP addresses using the production listener's socket policy."""
import socket


def assertTcpPortReleased(port):
    # POSIX keeps active-close connections in TIME_WAIT after all descriptors
    # close. Both production listeners allow that reuse, but not a live listener.
    # Windows must keep their exclusive-address policy instead of SO_REUSEADDR.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        option = exclusive if isinstance(exclusive, int) else socket.SO_REUSEADDR
        listener.setsockopt(socket.SOL_SOCKET, option, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(1)
        listener.settimeout(3)
        with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
            with listener.accept()[0] as accepted:
                accepted.settimeout(3)
                client.sendall(b"R")
                assert accepted.recv(1) == b"R"
