import os.path
import sys
import logging
from typing import Union

from app.constant import LOG_LEVEL

log_path = f'{os.getcwd()}/log.txt'

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler(log_path), logging.StreamHandler()])
LOG = logging.getLogger(__name__)
logging.getLogger('huey').setLevel(logging.INFO)


def get_stream_logger(name: str, level: Union) -> logging.Logger:
    """生成日志记录器（输出到stderr）"""
    logger = logging.Logger(name, level)
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] <%(process)d|%(thread)d|%(threadName)s> %(filename)s:%(lineno)d %(message)s')
    err_handler = logging.StreamHandler(stream=sys.stderr)
    err_handler.setFormatter(formatter)
    err_handler.setLevel(level)
    logger.addHandler(err_handler)

    out_handler = logging.StreamHandler(sys.stdout)
    out_handler.setFormatter(formatter)
    out_handler.setLevel(level)
    logger.addHandler(out_handler)

    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)  # 记录调试及以上级别的日志
    logger.addHandler(file_handler)
    return logger


std_logger = get_stream_logger('standard', LOG_LEVEL)
