from itertools import islice, cycle
from time import sleep

from app.models.mysql.tables import VpdTestTask, VpdTestRoute
from app.service.vpd.vpd import VPD, AppLoginCar
from app.service.vpd.vpd_android import VPDAndroid
from app.service.vpd.vpd_harmony import VPDHarmony
from app.utils.config_util import config_util
from app.utils.logger import std_logger

VPD_MAP = {
    'Android': VPDAndroid,
    'Harmony': VPDHarmony
}


def vpd_create_tools(vpd_task: VpdTestTask) -> VPD:
    """专题选择工具"""
    device_type = vpd_task.device_info.get('device_type')
    if device_type not in VPD_MAP:
        raise KeyError(f'暂不支持的设备类型: {device_type}')
    return VPD_MAP[device_type](vpd_task)


def vpd_task_run(vpd_task: VpdTestTask):
    if vpd_task.case_type == 13:  # 位置图片楼层用例

        for item in vpd_task.device_list:
            device_type = item.get('device_type')
            if device_type not in VPD_MAP:
                raise KeyError(f'暂不支持的设备类型: {device_type}')

        route_q = VpdTestRoute.gateway().select(filters={'route_name__in': vpd_task.route_list})
        route_list = sum([i.route_info or [] for i in route_q], [])

        execute_nums = min(vpd_task.case_execute_nums, 200)  # 最大执行次数
        final_route_list = list(islice(cycle(route_list), execute_nums))
        final_device_list = list(islice(cycle(vpd_task.device_list), execute_nums))
        account_info = config_util.account_info
        task_key = f'location_case_{vpd_task.task_name}'

        for cur_epoch, (device_item, route_info) in enumerate(zip(final_device_list, final_route_list), start=1):
            std_logger.info(f'[vpd_task_run] execute epoch={cur_epoch}, device_info={device_item},'
                            f'route_info={route_info}')

            device_type, vin_code = map(device_item.get, ['device_type', 'vin_code'])
            main_vpd = VPD_MAP[device_type](vpd_task, skip_init_driver=True)
            main_vpd.switch_device_info(device_item, route_list=[route_info], task_key=task_key)
            main_vpd.unlock_screen()

            username, password = account_info.get('username'), account_info.get('password')
            app_login_car = AppLoginCar(main_vpd, username, password, vin_code)
            app_login_car.run()

            main_vpd.run()
            sleep(300)  # 下电5分钟

            for base_device in vpd_task.device_list:
                device_type, app_type, vin_code = map(base_device.get, ['device_type', 'app_type', 'vin_code'])
                std_logger.info(f'[vpd_task_run] execute epoch={cur_epoch}, base_device={base_device}')
                if device_type != 'Harmony':
                    continue

                sub_vpd = VPD_MAP[device_type](vpd_task, skip_init_driver=True)
                sub_vpd.switch_device_info(base_device, route_list=[route_info], task_key=task_key)
                sub_vpd.unlock_screen()
                sub_vpd.screenrecord_manage(status='start')

                username, password = account_info.get('username'), account_info.get('password')
                app_login_car = AppLoginCar(sub_vpd, username, password, vin_code)
                app_login_car.run()

                sub_vpd.vpd_case.vpd_location_case_2(cur_epoch)
                sub_vpd.screenrecord_manage(status='stop')
    else:

        vpd = vpd_create_tools(vpd_task)
        vpd.run()
