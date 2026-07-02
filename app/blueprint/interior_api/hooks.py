from flask import abort

from app.models.mysql import db
from app.utils.logger import std_logger


def before_request():
    """请求前钩子函数

    Raises:
        werkzeug.exceptions.ServiceUnavailable
        werkzeug.exceptions.Unauthorized
    """
    # 建立MySQL连接
    try:
        db.connect(reuse_if_open=True)
    except Exception as e:
        std_logger.critical(repr(e))
        abort(503)


def teardown_request(e: Exception = None):
    """请求后钩子函数"""
    # 关闭MySQL连接
    db.close()
