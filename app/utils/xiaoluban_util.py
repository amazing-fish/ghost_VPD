import requests

from app.constant import XIAOLUBAN_TOKEN


class XiaoLuBan:
    """小鲁班"""

    url = 'http://xiaoluban.rnd.huawei.com:80/'
    auth = XIAOLUBAN_TOKEN

    @classmethod
    def send_msg(cls, content: str, receiver: str, sender: str = None):
        """小鲁班发送消息"""
        data = {
            'content': content,
            'receiver': receiver,
            'auth': cls.auth
        }
        url = cls.url
        if sender:
            data['sender'] = sender
        resp = requests.post(url=url, json=data)
        return resp.ok