import socket


class PortUtil:
    def __init__(self, start_port=8000, end_port=27283):
        self.start_port = start_port
        self.end_port = end_port
        self.used_ports = set()

    @classmethod
    def is_port_available(cls, prot: int):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", prot))  # 尝试绑定端口
                return True
            except OSError:
                return False

    def get_port(self):
        for port in range(self.start_port, self.end_port + 1):
            if port not in self.used_ports and self.is_port_available(port):
                self.used_ports.add(port)
                return port
        raise Exception("No available ports")

    def release_port(self, port):
        if port in self.used_ports:
            self.used_ports.remove(port)


port_util = PortUtil()
