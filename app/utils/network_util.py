import socket


class NetworkUtil:

    def __init__(self):
        self.ips = self.get_all_ips()


    @classmethod
    def get_all_ips(cls):
        hostname = socket.gethostname()
        ips = socket.gethostbyname_ex(hostname)[2]
        return ips


network_util = NetworkUtil()
