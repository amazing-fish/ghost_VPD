import traceback

import requests

from app.constant import ST_DOMAIN
from app.models.mysql.tables import VpdTestUpgradeTask
from app.service.tool_services import AppUpgradeTool, Device
from app.tasks.huey_base import huey
from app.utils.logger import std_logger


@huey.task()
def batch_upgrade_packages(task_id, device_list: list, upgrade_strategy: dict):
    status = 2
    result_list = []
    try:
        tool = AppUpgradeTool()

        test_devices = [Device(device_sn=aa.get("device_sn"), device_type=aa.get("device_type"))
                        for aa in device_list]
        result = tool.batch_upgrade(test_devices, upgrade_strategy, dry_run=False)


        for device_item in result.get("results", []):
            device_sn = device_item.device_sn
            device_result = f'device_sn={device_sn}'

            for app_item in device_item.apps:
                app_result = f'，app_code={app_item.app}，version={app_item.version}，result={app_item.status}，log={app_item.log}'
                device_result += app_result

            result_list.append(device_result)
    except Exception as e:
        std_logger.error(f'[batch_upgrade_packages] error={traceback.format_exc()}')
        status = 3
        result_list.append(str(e))

    update_upgrade_task_status(task_id, status, result_list)


def update_upgrade_task_status(task_id: int, status: int, result_list: list):
    filters = {
        "id": task_id
    }

    data = {
        "status": status,
        "result_list": result_list
    }

    try:
        VpdTestUpgradeTask.gateway().update(filters=filters, data=data)
    except Exception as e:
        std_logger.error(f'[update_upgrade_task_status] task_id={task_id}, status={status}, result_list={result_list},'
                         f' error={traceback.format_exc()}')
