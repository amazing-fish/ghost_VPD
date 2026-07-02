import traceback
from datetime import datetime

from huey import crontab

from app.models.mysql import db
from app.models.mysql.tables import VpdTestTask
from app.models.redis import redis_cli
from app.service.vpd.base import vpd_create_tools, vpd_task_run
from app.tasks.huey_base import huey
from app.utils.config_util import config_util
from app.utils.exception_util import ManualStopEvent, StopEvent
from app.utils.logger import std_logger
from app.utils.network_util import network_util
from app.utils.xiaoluban_util import XiaoLuBan


@huey.periodic_task(crontab(minute='*'))  # 每分钟执行一次
@db.connection_context()
def schedule_run_vpd_task():
    """定时运行vpd任务"""
    time_now = datetime.now()
    tasks = VpdTestTask.gateway().select(filters={'executor_ip__in': network_util.ips, 'status': -1,
                                                  'start_time__lte': time_now})

    for task in tasks:
        run_vpd_task(task.id)


@huey.task()
@db.connection_context()
def run_vpd_task(task_id: int):
    """运行vpd"""

    redis_key = f'vpd_task_{task_id}'

    if redis_cli.get(redis_key) == 'running':
        std_logger.info('[run_vpd_task] is runnning, will not run with more threads.')
        return

    redis_cli.set(redis_key, 'running', ex=60 * 45)

    vpd_task = VpdTestTask.gateway().get_by_id(task_id)
    VpdTestTask.gateway().update_status(vpd_task.id, 0)

    massage = f'【Smart-Test任务状态提醒】无人VPD任务：{vpd_task.task_name}，任务已完成!'

    try:
        vpd_task_run(vpd_task)
        VpdTestTask.gateway().update_status(vpd_task.id, 1)

    except StopEvent:
        VpdTestTask.gateway().update_status(vpd_task.id, 1)

    except ManualStopEvent:
        massage = f'【Smart-Test任务状态提醒】无人VPD任务：{vpd_task.task_name}，任务已停止!'
        VpdTestTask.gateway().update_status(vpd_task.id, 4)

    except Exception as e:
        std_logger.info(traceback.format_exc())
        massage = f'【Smart-Test任务状态异常提醒】无人VPD任务：{vpd_task.task_name}，原因: {str(e)}!'
        VpdTestTask.gateway().update_status(vpd_task.id, 2, str(e))

    finally:

        if vpd_task.notice_type == 1:  # 小鲁班通知
            receiver = config_util.config.get('group_id') or ''
            if receiver:
                XiaoLuBan.send_msg(massage, receiver)

        redis_cli.set(redis_key, 'finish', ex=60 * 45)


def start_huey_consumer(periodic: bool = False, workers: int = 1):
    huey.create_consumer(workers=workers, periodic=periodic, ).run()

