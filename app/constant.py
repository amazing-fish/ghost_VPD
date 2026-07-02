import os
import sys
from os import getenv

REMOTE_MODE = True

WORK_DIR = sys._MEIPASS if hasattr(sys, 'frozen') and hasattr(sys, '_MEIPASS') \
    else os.path.join(os.getcwd().split('vpd-service')[0], 'vpd-service')

MAX_HUEY_WORKS = 18

XIAOLUBAN_TOKEN = 'w00912103_NBqyD4Ih9OkAGLP2bxXtj1c5dna7giCZ'

ST_DOMAIN = 'http://ads-smart-test.rnd.yinwang.com'

VPD_WAIT_MAP_TIME = 120
VPD_BEFORE_WAIT_TIME = 10
VPD_CALCULATED_WAIT_TIME = 180
VPD_MAX_CRUISE_TIME = 1200

MYSQL_DB = 'vpd_service'
MYSQL_HOST = getenv('MYSQL_HOST') or ''
MYSQL_USER = getenv('MYSQL_USER') or ''
MYSQL_PASSWORD = getenv('MYSQL_PASSWORD') or ''

REDIS_HOST = getenv('REDIS_HOST') or 'localhost'
REDIS_PORT = int(getenv('REDIS_PORT') or 6379)
REDIS_DB = int(getenv('REDIS_DB') or 0)
REDIS_MAX_CONNECTIONS = int(getenv('REDIS_MAX_CONNECTIONS') or 200)

LOG_LEVEL = 'INFO'

APP_LOG_MAP = {
    'Android': {
        '#01': ['/sdcard/Android/data/app.huawei.motor/files/xcar/log/uavp/',
                'Android/data/app.huawei.motor/files/xcar/log/cloud/'],
        '#02': ['/sdcard/Android/data/com.avatar.buyer.client/files/logs/'],
        '#03': ['/sdcard/Android/data/com.voyah.fassistant/files/log/uavp/'],
        '#04': ['/sdcard/Android/data/com.cloudy.component/files/logs/'],
        '#05': ['/sdcard/Android/data/deepal.com.cn.app/files/logs/'],
        '#06': ['/sdcard/Android/data/com.byd.aeri.caranywhere/files/log/uavp/'],
        '#08': ['/sdcard/Android/data/com.dfmc.m/files/log/uavp/'],
        '#09': ['/sdcard/Android/data/com.jetour.zongheng/files/log/uavp/'],
    },
}
