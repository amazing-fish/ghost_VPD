import os.path
import random
import re
import traceback
import subprocess
from abc import ABC
from datetime import datetime
from itertools import islice, cycle
from time import sleep, time

from app.config.avatar_config import AVATAR_CONFIG_MAP
from app.config.byd_config import BYD_CONFIG_MAP
from app.config.cloudy_config import CLOUDY_CONFIG_MAP
from app.config.deepal_config import DEEPAL_CONFIG_MAP
from app.config.jetour_config import JETOUR_CONFIG_MAP
from app.config.chery_config import CHERY_CONFIG_MAP
from app.config.mhero_config import MHERO_CONFIG_MAP
from app.config.motor_config import MOTOR_CONFIG_MAP
from app.config.motor_demo_config import MOTOR_DEMO_CONFIG_MAP
from app.config.qiankun_demo import QIANKUN_DEMO_CONFIG_MAP
from app.config.voyah_config import VOYAH_CONFIG_MAP
from app.config.weaknet_config import WEAKNET_ELEMENT
from app.constant import VPD_BEFORE_WAIT_TIME, VPD_CALCULATED_WAIT_TIME, VPD_WAIT_MAP_TIME, WORK_DIR, \
    VPD_MAX_CRUISE_TIME, APP_LOG_MAP
from app.drivers.base import create_driver
from app.drivers.driver import Driver
from app.models.mysql import db

from app.models.mysql.tables import VpdTestTask, VpdTestRoute, VpdTestEventStats, VpdTestStats, VpdTestCaseStats
from app.models.redis import cache
from app.utils.adb_util import adb_start_call, adb_stop_call, adb_get_call_state, adb_answer_call, \
    adb_get_battery_level
from app.utils.config_util import config_util
from app.utils.exception_util import AppTypeKeyError, DeviceTypeKeyError, AppLaunchException, NoMapException, \
    UnavailableException, ExitedException, PlanningFailureException, ParkingTimeoutException, ParkingToggleException, \
    ParkingInTimeoutException, ParkingOutTimeoutException, AebEvent, CruiseBlockEvent, \
    RiskTimeoutException, RiskToggleException, AebBlockEvent, ManualStopEvent, ManualTakeoverEvent, StopEvent, \
    VehicleBlockEvent, StartCruiseError, CruiseTriggerEvent, CruiseWalkEvent, CruiseRecordEvent, ChargeError, \
    ChargeErrorBackEvent, SentinelException
from app.utils.logger import std_logger
from app.utils.hdc_util import hdc_get_battery_level

APP_CONFIG_MAP = {
    '#01': (MOTOR_CONFIG_MAP, '鸿蒙智行'),
    '#02': (AVATAR_CONFIG_MAP, '阿维塔'),
    '#03': (VOYAH_CONFIG_MAP, '岚图'),
    '#04': (CLOUDY_CONFIG_MAP, '广汽传祺'),
    '#05': (DEEPAL_CONFIG_MAP, '深蓝'),
    '#06': (BYD_CONFIG_MAP, '比亚迪'),
    '#07': (MOTOR_DEMO_CONFIG_MAP, '鸿蒙demo'),
    '#08': (MHERO_CONFIG_MAP, '乾崑'),
    '#09': (JETOUR_CONFIG_MAP, '捷途'),
    '#10': (CHERY_CONFIG_MAP, 'VPD'),
    '#11': (QIANKUN_DEMO_CONFIG_MAP, '乾崑demo'),
}

VEHICLE_FACTORY_MAP = {
    1: '阿维塔',
    2: '问界',
    3: '智界',
    4: '享界',
    5: '尊界',
    6: '尚界',
    7: '猛士',
}

class VPD(ABC):
    SCENE_MAP = {
        '正在前往召唤点': 0,
        '正在泊入车位': 1,
        '正在泊出车位': 2,
        '路况复杂，正在尝试通行': 3,
        '临时停靠中': 4,
        '正在靠边停车': 5,
        '目标车位不可用，为您寻找可泊车位': 7,
        '正在前往目标车位': 8,
    }

    def __init__(self, vpd_task: VpdTestTask, skip_init_driver: bool = False):
        self._vpd_task = vpd_task
        self._app_version = ''
        self._vin_code = ''
        self._case_type = ''
        self._vin_pattern = re.compile(r'[\w]{17}')
        (self._device_name, self._device_sn, self._device_type, self._app_type, self._config_vin_code,
         self._config_vehicle_type, self._config_app_env, self._vehicle_factory) = self.__get_device_info()
        self._app_status = 0  # app状态 1 启动 0 未启动
        self._n_retry = 0

        self._output_path, self._safe_code, self._output_app_log_path = map(
            config_util.config.get, ['output_path', 'safe_code', 'output_app_log_path'])
        self._report_data = {}
        self._power_on_data = []
        self._event_patten = re.compile(r'classify:\s*(.*?);\s*describe:\s*(.*)')
        self._app_log_path = APP_LOG_MAP.get(self._device_type, {}).get(self._app_type, None)

        if not skip_init_driver:
            self.driver: Driver = create_driver(self._device_sn, self._device_type)
            self._img_path, self._log_path, self._video_path = self.__get_task_data_path()
            self.app_config, self.app_name = self.__get_app_config()

        self.route_list = self.__get_route_list()
        self.exception_map = {
            '暂不可用': UnavailableException,
            '泊车代驾异常退出': ExitedException,
            '规划失败': PlanningFailureException
        }
        self.manual_event = ManualEvent()
        self.vpd_case = VPDCase(self, vpd_task)

    def __set_report_data(self, function: str, data: dict):
        self._report_data.setdefault(function, {
            'vin_code': self._vin_code,
            'app_name': self.app_name,
            'app_version': self._app_version,
            'device_type': self._device_type,
            'n_park_or_summon': 0,
            'n_success_park_or_summon': 0,
            'n_calculate_road': 0,
            'n_success_calculate_road': 0,
            'n_cruise': 0,
            'n_cruise_quit': 0
        })

        for k, v in data.items():

            if k in self._report_data[function] and isinstance(v, int):
                self._report_data[function][k] += v

    def __get_device_info(self):
        """设备信息"""
        device_name, device_sn, device_type, app_type, vin_code, vehicle_type, app_env, vehicle_factory = map(
            self._vpd_task.device_info.get, ['device_name', 'device_sn', 'device_type', 'app_type', 'vin_code',
                                             'vehicle_type', 'app_env', 'vehicle_factory'])

        return device_name, device_sn, device_type, app_type, vin_code, vehicle_type, app_env, vehicle_factory

    def switch_device_info(self, dev_info: dict, route_list, task_key: str = None):
        """切换当前操作的设备和APP配置"""
        device_sn = dev_info.get('device_sn')
        device_type = dev_info.get('device_type')

        self._device_name = dev_info.get('device_name')
        self._device_sn = device_sn
        self._device_type = device_type
        self._app_type = dev_info.get('app_type')
        self._config_vin_code = dev_info.get('vin_code')
        self._config_vehicle_type = dev_info.get('vehicle_type')
        self._config_app_env = dev_info.get('app_env')
        self._app_status = 0  # app状态 1 启动 0 未启动
        self._n_retry = 0

        self.driver = create_driver(device_sn, device_type)
        self._img_path, self._log_path, self._video_path = self.__get_task_data_path(task_key=task_key)
        self.app_config, self.app_name = self.__get_app_config()
        self._app_log_path = APP_LOG_MAP.get(self._device_type, {}).get(self._app_type, None)
        self.route_list = route_list

    def __get_route_list(self):
        """获得路线列表"""
        route_q = VpdTestRoute.gateway().select(filters={'route_name__in': self._vpd_task.route_list})

        return sum([i.route_info or [] for i in route_q], [])

    def __get_app_config(self):
        """应该配置"""
        app_config_map = APP_CONFIG_MAP.get(self._app_type, None)

        if not app_config_map:
            raise AppTypeKeyError(self._app_type)

        app_name = app_config_map[1]
        app_config = app_config_map[0].get(self._device_type, {})

        if not app_config:
            raise DeviceTypeKeyError(self._device_type)

        return app_config, app_name

    def __set_app_version(self):
        self._app_version = self.driver.get_app_version(**self.app_config.get('package'))

    def __get_task_data_path(self, task_key: str = None):
        now_time = datetime.now()
        root_path = os.path.normpath(os.path.join(self._output_path, now_time.strftime("%Y%m"),
                                                  now_time.strftime("%m%d")))
        if task_key:
            root_path = os.path.normpath(os.path.join(root_path, task_key))
        task_path_name = f'{self._vpd_task.task_name}_{now_time.strftime("%Y%m%d%H%M%S")}'
        img_path = os.path.normpath(os.path.join(root_path, task_path_name, 'imgs'))
        log_path = os.path.normpath(os.path.join(
            self._output_app_log_path, now_time.strftime("%Y%m%d"), self._device_sn, self._app_type))
        video_path = os.path.normpath(os.path.join(root_path, task_path_name, 'videos'))

        for path in [img_path, log_path, video_path]:
            if not os.path.exists(path):
                os.makedirs(path)

        return img_path, log_path, video_path

    def set_safe_code(self):
        if self._app_type == '#07':  # 开发demo无安全码
            return

        if self.driver.exists(**self.app_config.get('vpd_safe_code')):

            if self._app_type == '#06':
                for i in self._safe_code:
                    xpath_str = self.app_config.get('vpd_safe_code_input', {}).get('xpath')
                    self.driver.click(xpath=xpath_str.format(i))
                    sleep(0.6)
            elif self._app_type == '#10':
                xpath_template = self.app_config.get('vpd_safe_code_input', {}).get('xpath')
                for digit in self._safe_code:
                    real_xpath = xpath_template.format(digit)
                    if self.driver.xpath(real_xpath).wait(timeout=2):
                        self.driver.click(xpath=real_xpath)
                    sleep(0.6)
            else:
                self.driver.set_text(self._safe_code, **self.app_config.get('vpd_safe_code_input'))

            sleep(2)

    def process_identify_card(self):
        if self._app_type != '#05':
            return

        if self.driver.exists(**self.app_config.get('identify_card')):
            self.driver.click(**self.app_config.get('identify_card_close'))
            sleep(2)

    def click_pause(self):
        describe = '成功' if self.driver.click(**self.app_config.get('pause')) else '失败'
        message = f'classify: 暂停; describe: {describe}'

        self.save_event_db(message, event_time=datetime.now())

    def click_continue(self):
        describe = '成功' if self.driver.click(**self.app_config.get('continue')) else '失败'
        message = f'classify: 继续; describe: {describe}'

        self.save_event_db(message, event_time=datetime.now())

    def click_quit(self):
        describe = '成功' if self.driver.click(**self.app_config.get('quit')) else '失败'
        message = f'classify: 退出; describe: {describe}'

        self.save_event_db(message, event_time=datetime.now())

    def click_retry(self):
        describe = '成功' if self.driver.click(**self.app_config.get('retry')) else '失败'

        if self._app_type == '#05':
            sleep(3.5)
            self.set_safe_code()

        message_dt = {
            1: 'classify: 算路重试; describe: 算路重试打点',
            2: f'classify: 重试; describe: {describe}'
        }

        for i in [1, 2]:
            self.save_event_db(message_dt[i], event_time=datetime.now())

    def get_cruise_scene_type(self):
        flag = False

        for _ in range(2):

            for fail_tip in self.exception_map.keys():
                title_xpath = self.app_config.get('main_title', {}).get('xpath', '').format(fail_tip)

                if self.driver.exists(xpath=title_xpath):

                    if fail_tip == '暂不可用' and not flag:
                        sleep(5)
                        flag = True
                        break

                    sub_title_xpath = self.app_config.get('sub_title', {}).get('xpath', '').format(fail_tip)
                    raise self.exception_map[fail_tip](self.driver.get_text(xpath=sub_title_xpath) or ' ')

            if not flag:
                break

        scene_type = 6  # 巡航中异常场景 8：泊入巡航正常场景

        for k, v in self.SCENE_MAP.items():
            title_xpath = self.app_config.get('main_title', {}).get('xpath', '').format(k)
            if self.driver.exists(xpath=title_xpath):
                scene_type = v
                break

        return scene_type

    def manual_trigger(self):
        """打点"""
        if not self.driver.click(xpath='//*[@text="反馈"]'):
            self.driver.click(img_path=self.get_assets_img_path('manual_trigger.png'), screenshot_path=self._img_path,
                              np_start=0.15)

        sleep(3)

        if self._app_type in ['#01', '#02', '#04', '#08', '#09']:
            self.driver.click(xpath='//*[@text="立即反馈"]')
            sleep(3)

    def wait_for_element(self, config_keys: list, wait_time: int = 20, if_appear: bool = True, if_block: bool = False):
        time_now, flag, element_time = time(), False, None

        while time() - time_now < wait_time:
            for config_key in config_keys:
                element = self.driver.exists(**self.app_config.get(config_key))

                if (if_appear and not element) or (not if_appear and element):
                    element_time = datetime.now()
                    flag = True
                    break

            if flag:
                sleep(4)
                break

            sleep(2)

            if if_block and self.check_event_by_img('vehicle_block.png'):  # 车辆无响应监测
                raise VehicleBlockEvent()

        # 元素 出现或消失 的时间点
        return element_time

    def click_for_element(self, config_key: str, wait_time: int = 20, if_click: bool = True, swipe_type: str = 'up',
                          img_name: str = None, similarity: float = None, y_start: float = None, y_end: float = None,
                          back_xpath: str = None):
        time_now, if_success, click_time = time(), False, None

        while time() - time_now < wait_time:

            if back_xpath:
                element = self.driver.exists(xpath=back_xpath)
            elif not img_name:
                element = self.driver.exists(**self.app_config.get(config_key))
            else:
                element = self.driver.exists(img_path=self.get_assets_img_path(img_name),
                                             screenshot_path=self._img_path, similarity=similarity)

            if not element:

                if swipe_type == 'up':
                    y2 = 0.30
                else:
                    y2 = 0.70

                y1 = y_start or 0.50
                y2 = y_end or y2
                self.driver.swipe(x1=0.50, y1=y1, x2=0.50, y2=y2)
                sleep(2)
            else:
                if if_click:
                    sleep(1)
                    if back_xpath:
                        if_success = self.driver.click(xpath=back_xpath)
                    elif not img_name:
                        if_success = self.driver.click(**self.app_config.get(config_key))
                    else:
                        if_success = self.driver.click(img_path=self.get_assets_img_path(img_name),
                                                       screenshot_path=self._img_path, similarity=similarity)
                    click_time = datetime.now()

                break

        sleep(2)

        return if_success, click_time

    def app_stop(self):
        self.driver.app_stop(**self.app_config.get('package'))
        self._app_status = 0

    def check_current_app(self):
        pass

    def app_start(self):
        """启动app"""
        if self._app_status == 1:
            return

        try:
            self.driver.app_start(**self.app_config.get('package'))
        except Exception as e:
            raise AppLaunchException()

        sleep(3)

        self.check_current_app()

        if self._app_type == '#01':
            self.wait_for_element(['app_loading'], wait_time=60)
        elif self._app_type == '#02':
            self.wait_for_element(['car'], if_appear=False, wait_time=10)

            for i in ['ad_accept', 'ad_confirm', 'ad_close']:

                if self.driver.exists(**self.app_config.get(i)):
                    self.driver.click(**self.app_config.get(i))
                    sleep(2)

                sleep(1)

        elif self._app_type == '#03':

            sleep(3.5)

            if self.driver.exists(**self.app_config.get('ad_accept')):
                self.driver.click(**self.app_config.get('ad_accept'))

            sleep(3.5)
        elif self._app_type == '#04':
            self.wait_for_element(['service'], if_appear=False, wait_time=10)
            self.driver.click(**self.app_config.get('service'))
            sleep(3)
            self.driver.click(**self.app_config.get('car'))
            sleep(3)
            if self.driver.exists(**self.app_config.get('identity_skip')):
                self.driver.click(**self.app_config.get('identity_skip'))
            sleep(3)

        elif self._app_type == '#05':

            sleep(3.5)

            if self.driver.exists(**self.app_config.get('env_select')):  # 环境选择
                self.driver.click(**self.app_config.get('env_select'))
                sleep(2)

            self.wait_for_element(['car'], if_appear=False, wait_time=10)

        elif self._app_type == '#06':
            sleep(5)
            self.wait_for_element(['car'], if_appear=False, wait_time=10)

        elif self._app_type == '#07':
            sleep(2.5)
            self.driver.click(**self.app_config.get('vpd_select'))
            sleep(2)

            params = {
                'vehicle_select': self._config_vehicle_type,
                'env_select': self._config_app_env,
                'vin_select': self._config_vin_code,
            }
            for k, v in params.items():
                self.driver.click(**self.app_config.get(k))
                sleep(2)

                time_now = time()
                while time() - time_now < 10:
                    element = self.driver.exists(elements={'text': v})
                    if not element:
                        self.driver.swipe(x1=0.35, y1=0.50, x2=0.35, y2=0.40)
                    else:
                        self.driver.click(elements={'text': v})
                        break
                    sleep(1.5)

                if k == 'env_select':
                    self.driver.click(**self.app_config.get('init'))
                    sleep(3)

                elif k == 'vin_select' and not self._vin_code:
                    self._vin_code = v
        elif self._app_type == '#08':
            sleep(2)
            self.wait_for_element(['vpd'], if_appear=False)
        elif self._app_type == '#11':
            sleep(5)
            self.wait_for_element(['vpd_title'], if_appear=False)
        else:
            self.wait_for_element(['app_loading'])

        if not self._app_version:
            self.__set_app_version()

        self._app_status = 1

    def set_vin_code(self):
        """设置vin码"""
        vin_code = ''
        if self._app_type == '#01':
            for _ in range(3):
                self.driver.click(img_path=self.get_assets_img_path('car_list.png'), screenshot_path=self._img_path,
                                  np_start=0.85)  # np_start==0.8 图标较小 需至少匹配80%
                sleep(2)
                if self.driver.exists(**self.app_config.get('vin_last_6')):
                    break

            vin_string = self.driver.get_text(**self.app_config.get('vin_last_6'))
            vin_last_6 = re.search(r'[A-Z0-9]+', vin_string).group()

            if self._device_type == 'Android':
                self.driver.press('back')

            sleep(2)
            self.driver.click(**self.app_config.get('my'))
            sleep(2)
            self.click_for_element('my_car', swipe_type='down')

            self.wait_for_element(['my_car_query'])

            vin_xpath = self.app_config.get('vin_code_new', {}).get('xpath', '').format(vin_last_6)
            t1 = time()

            while not self.driver.exists(xpath=vin_xpath) or time() - t1 > 15:
                if not self.driver.exists(**self.app_config.get('vin_code')):
                    break

                self.driver.swipe(x1=0.5, y1=0.8, x2=0.5, y2=0.2)

                sleep(2)

            vin_code = self.driver.get_text(xpath=vin_xpath)

        elif self._app_type == '#02':
            self.driver.click(**self.app_config.get('car'))
            sleep(2)
            self.driver.click(**self.app_config.get('more_menu'))
            sleep(2)
            vin_code = self.driver.get_text(**self.app_config.get('vin_code'))

        elif self._app_type == '#03':
            self.driver.click(**self.app_config.get('my'))
            sleep(2)
            self.click_for_element('my_car')
            sleep(3)
            self.wait_for_element(['my_car_query'])
            vin_code = self.driver.get_text(**self.app_config.get('vin_code'))

            match = self._vin_pattern.search(vin_code)
            if match:
                vin_code = match.group()
        elif self._app_type == '#04':
            self.driver.click(**self.app_config.get('more_car'))
            sleep(2)
            self.driver.click(**self.app_config.get('default_car'))
            sleep(2)
            self.driver.click(**self.app_config.get('btn_look'))
            sleep(2)
            vin_code = self.driver.get_text(**self.app_config.get('vin_code'))
            sleep(2)
            self.driver.press('back')

        elif self._app_type == '#05':

            for k in ['car', 'setting']:
                self.driver.click(**self.app_config.get(k))
                sleep(2)

            vin_code = self.driver.get_text(**self.app_config.get('vin_code'))

        elif self._app_type == '#06':
            for k in ['my', 'my_car', 'vin_code_query']:
                self.driver.click(**self.app_config.get(k))
                sleep(2)

            vin_code = self.driver.get_text(**self.app_config.get('vin_code'))
            self.driver.press('back')

        self._vin_code = vin_code
        self.driver.press('back')

    def enter_vpd(self, retry: bool = False):
        if not retry:
            if not self._vin_code and self._app_type != '#07':
                self._vin_code = self._config_vin_code

            if self._app_type not in ['#07', '#11']:  # 非开发demo
                self.driver.click(**self.app_config.get('car'))
                sleep(2)

            if self._app_type == '#01':
                if_success, click_time = self.click_for_element('', img_name='vpd.png', similarity=0.8)
            elif self._app_type == '#02':
                self.driver.swipe(x1=0.50, y1=0.50, x2=0.50, y2=0.30)  # 规避遮挡图标
                sleep(2)
                self.click_for_element('all_control')
                sleep(2)
                if self.driver.exists(**self.app_config.get('vpd_new')):
                    if_success, click_time = self.click_for_element('vpd_new')
                else:
                    if_success, click_time = self.click_for_element('vpd_old')
            elif self._app_type == '#04':
                if_success, click_time = self.click_for_element('vpd', wait_time=60)
            elif self._app_type == '#09':
                self.driver.swipe(x1=0.50, y1=0.60, x2=0.50, y2=0.10)
                sleep(2)
                if_success, click_time = self.click_for_element('vpd')
            elif self._app_type == '#10':
                self.driver.swipe(x1=0.50, y1=0.60, x2=0.50, y2=0.30)
                sleep(2)
                if_success, click_time = self.click_for_element('vpd')
            elif self._app_type == '#11':
                self.driver.click(**self.app_config.get('env_select'))
                sleep(2)
                self.driver.click(**self.app_config.get('env_pre'))
                sleep(2)
                self.driver.press('back')
                sleep(2)

                self.driver.click(**self.app_config.get('real_function'))
                sleep(2)
                l4_ghost_button = self.driver._device.xpath(**self.app_config.get('l4_ghost_button')).get() or {}
                l4_active = l4_ghost_button.info.get('checked', False)
                if not l4_active:
                    self.driver.click(**self.app_config.get('l4_ghost_button'))
                    sleep(2)
                self.driver.press('back')

                vehicle_factory_xpath = f'//*[@text="{VEHICLE_FACTORY_MAP.get(self._vehicle_factory)}"]'
                self.click_for_element('config_key', y_start=0.58, y_end=0.4,
                                       back_xpath=vehicle_factory_xpath)
                self.wait_for_element(['vehicle_loading'])

                vin_code_xpath = f'//*[@text="{self._vin_code}"]'
                self.click_for_element('config_key', y_start=0.85, y_end=0.7,
                                       back_xpath=vin_code_xpath)
                sleep(2)
                if_success = self.driver.click(**self.app_config.get('vpd'))
                click_time = datetime.now()

            else:
                if_success, click_time = self.click_for_element('vpd')
            if if_success:
                message = 'classify: 点击VPD; describe: 成功'
                self.save_event_db(message, event_time=click_time)

            if self._app_type in ['#01', '#06','#10']:
                self.set_safe_code()
            if self._app_type in ['#03', '#04']:
                sleep(5)  # 规避岚图、广汽进图加载界面遮罩

            element_time = self.wait_for_element(['vpd_loading'], VPD_WAIT_MAP_TIME)

            conditions = [
                self.driver.exists(**self.app_config.get('vpd_success')),
                self.driver.exists(**self.app_config.get('parking')),
                not self.driver.exists(**self.app_config.get('pause'))
            ]

            describe = '失败' if not any(conditions) else '成功'

            for classify in ['条件检查', '账户登录', '加载地图']:
                message = f'classify: {classify}; describe: {describe}'
                self.save_event_db(message, event_type=2, event_time=element_time or datetime.now())

            if not any(conditions):
                raise NoMapException()

        else:
            if self._app_type == '#02':
                if self.driver.exists(**self.app_config.get('vpd_new')):
                    self.click_for_element('vpd_new')
                else:
                    self.click_for_element('vpd_old')
            elif self._app_type == '#04':
                self.click_for_element('vpd', wait_time=60)
            else:
                self.click_for_element('vpd')

            if self._app_type in ['#01', '#06']:
                self.set_safe_code()
            if self._app_type in ['#03', '#04']:
                sleep(5)  # 规避岚图、广汽进图加载界面遮罩

            self.wait_for_element(['vpd_loading'], VPD_WAIT_MAP_TIME)

    def enter_vpd_v2(self):
        if not self._vin_code and self._app_type != '#07':
            self._vin_code = self._config_vin_code

        if self._app_type not in ['#07']:  # 非开发demo
            self.driver.click(**self.app_config.get('car'))
            sleep(2)

        if self._app_type == '#01':
            if_success, click_time = self.click_for_element('', img_name='vpd.png', similarity=0.8)
        elif self._app_type == '#02':
            self.driver.swipe(x1=0.50, y1=0.50, x2=0.50, y2=0.30)  # 规避遮挡图标
            sleep(2)
            self.click_for_element('all_control')
            sleep(2)
            if self.driver.exists(**self.app_config.get('vpd_new')):
                if_success, click_time = self.click_for_element('vpd_new')
            else:
                if_success, click_time = self.click_for_element('vpd_old')
        elif self._app_type == '#04':
            if_success, click_time = self.click_for_element('vpd', wait_time=60)
        elif self._app_type == '#09':
            self.driver.swipe(x1=0.50, y1=0.60, x2=0.50, y2=0.10)
            sleep(2)
            if_success, click_time = self.click_for_element('vpd')
        elif self._app_type == '#10':
            self.driver.swipe(x1=0.50, y1=0.60, x2=0.50, y2=0.30)
            sleep(2)
            if_success, click_time = self.click_for_element('vpd')
        else:
            if_success, click_time = self.click_for_element('vpd')
        if if_success:
            message = 'classify: 点击VPD; describe: 成功'
            self.save_event_db(message, event_time=click_time)

        if self._app_type in ['#01', '#06']:
            self.set_safe_code()
        if self._app_type in ['#03', '#04']:
            sleep(5)  # 规避岚图、广汽进图加载界面遮罩

        element_time = self.wait_for_element(['vpd_loading'], VPD_WAIT_MAP_TIME)

        sleep(3)
        result, _ = self.judge_calculate_result()
        if result is not None:
            if result is False:
                raise NoMapException()
            else:
                if not self.clear_calculate_result():
                    raise NoMapException()
                self.switch_map_view()

        self.map_page_slight_swipe()

        time_now = time()
        map_loading_flag = False
        conditions = None
        while time() - time_now < VPD_WAIT_MAP_TIME:
            sleep(1)
            conditions = [
                self.driver.get_text(**self.app_config.get('parking_lot')),
                self.driver.get_text(**self.app_config.get('floor')),
                self.driver.get_text(**self.app_config.get('default_parking_space')) or
                self.driver.get_text(**self.app_config.get('default_parking_space_no_set')),
                self.driver.get_text(**self.app_config.get('default_boarding_spot')) or
                self.driver.get_text(**self.app_config.get('default_boarding_spot_no_set')),
                self.driver.exists(**self.app_config.get('favorite_flag')) or
                self.driver.exists(**self.app_config.get('favorite_empty_flag')),
                self.driver.get_text(**self.app_config.get('total_spots')),
            ]
            if all(conditions):
                map_loading_flag = True
                break

        describe = '成功' if map_loading_flag else '失败'

        for classify in ['条件检查', '账户登录', '加载地图']:
            message = f'classify: {classify}; describe: {describe}'
            self.save_event_db(message, event_time=element_time or datetime.now())

        if not map_loading_flag:
            std_logger.info(f'[enter_vpd_v2] device_sn={self._device_sn}, conditions={conditions}')
            raise NoMapException()

    def get_screen_size(self):
        pass

    def get_ele_info(self, xpath: str):
        pass

    def map_compact_state(self):
        try:
            w, h = self.get_screen_size()

            ele_info = self.get_ele_info(**self.app_config.get('right_icon_btn'))

            if self._device_type == 'Harmony':
                center_y = ele_info.center.y
            else:
                x1, y1, x2, y2 =  ele_info.bounds
                center_y = (y1 + y2) / 2

            y_rate = center_y / h if h else 0
            return y_rate > 0.6
        except Exception as e:
            std_logger.info(f'[map_compact_state] device_sn={self._device_sn}, error={e}')
            return False

    def map_page_slight_swipe(self, wait_time: int = 60):
        # 进图后主界面上滑，保证进图判断元素可以识别
        time_now, state_flag = time(), True

        while time() - time_now < wait_time:
            if self.map_compact_state():
                self.driver.swipe(x1=0.50, y1=0.90, x2=0.50, y2=0.80)

            state_flag = self.map_compact_state()
            if not state_flag:
                break
            else:
                sleep(10)
        std_logger.info(f'[map_page_slight_swipe] device_sn={self._device_sn}, state_flag={state_flag}')


    def input_parking_space(self, parking_space: str, if_wait: bool = True) -> tuple:
        pass

    def judge_calculate_result(self, is_wait: bool = True):
        # True-算路成功 False-算路失败 None-没有算路
        result, fail_tip = None, ''

        if is_wait:
            element_time = self.wait_for_element(['start_btn'], VPD_BEFORE_WAIT_TIME, if_appear=False)
        else:
            element_time = datetime.now()

        if element_time is not None:
            fail_tip = self.driver.get_text(**self.app_config.get('cruise_tip'))

            if self.driver.exists(**self.app_config.get('start_enabled')):
                result = True
            else:
                result = False

        std_logger.info(f'[judge_calculate_result] device_sn={self._device_sn}, result={result}, fail_tip={fail_tip}')
        return result, fail_tip

    def clear_calculate_result(self):
        self.driver.press('back')
        result = True

        sleep(1)
        time_now = time()
        while time() - time_now <= 5:
            if not self.driver.get_text(**self.app_config.get('parking_lot')):
                result = False
                break

        return result

    def switch_map_view(self, click_full_view: bool = True, click_ego_view: bool = True):
        if click_full_view:
            self.driver.click(**self.app_config.get('full_view_btn'))
            sleep(2)

        if click_ego_view:
            self.driver.click(**self.app_config.get('ego_view_btn'))
            sleep(2)

    def check_before_start(self):
        if_retry = False

        for _ in range(2):
            for fail_tip in self.exception_map.keys():
                title_xpath = self.app_config.get('main_title', {}).get('xpath', '').format(fail_tip)

                if title_xpath and self.driver.exists(xpath=title_xpath):

                    if self.driver.exists(**self.app_config.get('retry')) and not if_retry:
                        self.click_retry()
                        sleep(2)
                        self.wait_for_element(['start_loading'], VPD_CALCULATED_WAIT_TIME)
                        if_retry = True
                        break

                    sub_title_xpath = self.app_config.get('sub_title', {}).get('xpath', '').format(fail_tip)

                    message = f'classify: 算路; describe: 失败'
                    self.save_event_db(message, event_time=datetime.now())

                    raise self.exception_map[fail_tip](self.driver.get_text(xpath=sub_title_xpath))

            if not if_retry:
                break

        message = f'classify: 算路; describe: 成功'
        self.save_event_db(message, event_time=datetime.now())

    def confirm_vpd(self):
        describe, if_start = '失败', False

        for _ in range(3):

            if self.driver.exists(**self.app_config.get('start')):
                self.driver.click(**self.app_config.get('start'))
                sleep(2)

            if self.driver.exists(**self.app_config.get('confirm')):

                confirm_time = datetime.now()

                self.driver.click(**self.app_config.get('confirm'))

                try:
                    confirm_time_1 = self.wait_for_element(['pause'], if_appear=False, if_block=True,
                                                           wait_time=6)
                    if confirm_time_1:
                        describe = '成功'
                        confirm_time = confirm_time_1
                    else:
                        describe = '失败'
                except Exception as e:

                    raise e

                finally:
                    message = f'classify: 激活; describe: {describe}'
                    self.save_event_db(message, event_time=confirm_time)

            if_start = self.driver.exists(**self.app_config.get('pause'))

            if if_start:
                break

            sleep(2)

        if not if_start:
            raise StartCruiseError()

    def start_vpd(self, case_type: str, parking_space: str):
        self._case_type = case_type
        self.wait_for_element([case_type], VPD_BEFORE_WAIT_TIME, False)

        element_time = None
        if self._app_type == '#01':
            element_time = self.wait_for_element(['start_enabled'], VPD_BEFORE_WAIT_TIME, if_appear=False)
        if not element_time:  # 未出现可用开始按钮
            if not self.driver.exists(**self.app_config.get(case_type)):

                if  self._app_type == '#11':
                    self.driver.click(**self.app_config.get('l4_activate_btn'))
                    start_time = datetime.now()
                    sleep(2)
                    element_time = self.wait_for_element(['start_loading'], VPD_CALCULATED_WAIT_TIME)
                else:
                    start_time, element_time = self.input_parking_space(parking_space)

                if self._app_type == '#04':
                    self.set_safe_code()
                    sleep(5)

                condition = isinstance(element_time,
                                       datetime) and element_time.timestamp() - start_time.timestamp() >= 5
                describe = '成功' if condition else '失败'
                message = f'classify: 召唤上电; describe: {describe}'
                self.save_event_db(message, event_time=element_time or datetime.now())

                self.driver.click(**self.app_config.get(case_type))
                sleep(3.5)

                if self._app_type == '#05':
                    self.set_safe_code()

            self.wait_for_element(['start_loading'], VPD_CALCULATED_WAIT_TIME)
            sleep(3)
            if self._app_type == '#04':
                self.driver.click(**self.app_config.get(case_type))
                sleep(2)
                self.wait_for_element(['start_loading'], VPD_CALCULATED_WAIT_TIME)
                sleep(3)

        calculate_dict = {
            'n_calculate_road': 1
        }

        try:
            self.check_before_start()
            calculate_dict.update({'n_success_calculate_road': 1})
        except Exception as e:
            raise PlanningFailureException
        finally:
            self.__set_report_data(case_type, calculate_dict)

        self.confirm_vpd()

        if self._vpd_task.case_type == 6:  # 小窗
            self.vpd_case.start_case()

    def parser_aeb_event(self):
        sleep(25)
        self.click_continue()

    def parser_aeb_block_event(self):
        sleep(3)
        self.click_quit()

    def parser_cruise_block_event(self):
        self.click_pause()
        sleep(3)
        self.click_quit()
        sleep(3)

    def parser_charge_error_back_event(self):
        element_time = self.wait_for_element(['pause'], if_appear=False, wait_time=120)
        if not element_time:
            raise ChargeError('返回默认车位异常, 巡航开始异常')

        self.check_cruise_event('charge_error_back', '', {})

        finish_condition = [
            self.driver.exists(**self.app_config.get('parking_finish')),
            self.driver.exists(**self.app_config.get('summon_finish'))
        ]
        if any(finish_condition):
            self.driver.press('back')
            sleep(2)
        else:
            raise ChargeError('返回默认车位异常, 未驶入默认车位')

    @classmethod
    def get_assets_img_path(cls, img_name: str):
        return os.path.join(WORK_DIR, 'app', 'assets', 'imgs', 'common', img_name)

    def check_event_by_img(self, img_name: str):
        return self.driver.exists(img_path=self.get_assets_img_path(img_name), screenshot_path=self._img_path)

    def slight_swipe_and_click(self, config_key: str):
        if self.driver.exists(**self.app_config.get(config_key)):
            self.driver.swipe(x1=0.5, y1=0.5, x2=0.5, y2=0.4, duration=0.5)
            sleep(2)
            self.driver.click(**self.app_config.get(config_key))
            sleep(2)

    def check_cruise_event(self, case_type: str, parking_space: str, answer_call_route: dict = None,
                           is_charge: bool = False):
        """巡航事件检查"""
        answer_call_route = [] if not answer_call_route else answer_call_route
        self.__set_report_data(case_type, {'n_cruise': 1, 'n_park_or_summon': 1})
        start_time, scene_ls, cruise_dict, exception_to_raise = time(), [], {}, None
        is_called, answer_call_scene = False, random.choice([i for i in range(0, 7)])

        call_condition = all([
            self._vpd_task.case_type == 5,
            not is_called,
            len(answer_call_route) > 0 and list(answer_call_route.keys())[0] == case_type,
            len(answer_call_route) > 0 and list(answer_call_route.values())[0] == parking_space
        ])

        while time() - start_time <= VPD_MAX_CRUISE_TIME:

            sleep(5)  # 每5秒监测一次

            try:

                scene_type = self.get_cruise_scene_type()  # 获得场景类型

                if scene_type == answer_call_scene and call_condition:  # 接电话
                    self.vpd_case.start_case()
                    is_called = True

                if case_type == 'summon':

                    if 'n_success_park_or_summon' not in cruise_dict:
                        scene_ls.append(scene_type)

                        if 2 in scene_ls and len(scene_ls) >= 3:
                            summon_i = scene_ls.index(2)
                            summon_before = scene_ls[:summon_i]
                            summon_after = scene_ls[summon_i + 1:]

                            if 4 not in summon_before and 0 in summon_after:
                                cruise_dict.update({'n_success_park_or_summon': 1})

                if_aeb = self.driver.exists(**self.app_config.get('continue'))

                if_speed = self.check_event_by_img('zero_speed.png')
                speed = 0 if if_speed else -1
                std_logger.info(f'[VPD][check_cruise_event] device_sn: {self._device_sn}, speed: {speed},'
                                f'scene_type: {scene_type}')

                self.manual_event.check_evnet(scene_type, speed, if_aeb)

            except Exception as e:

                std_logger.info(f'[VPD][check_cruise_event] device_sn: {self._device_sn}， reason: {str(e)}, '
                                f'traceback: {traceback.format_exc()}')

                if isinstance(e, (CruiseBlockEvent, AebBlockEvent, UnavailableException, ExitedException)):
                    cruise_dict.update({'n_cruise_quit': 1})

                    if isinstance(e, (ExitedException, AebBlockEvent, CruiseBlockEvent)):
                        self.save_event_db(str(e), event_type=1)

                    if isinstance(e, CruiseBlockEvent):
                        self.parser_cruise_block_event()
                    elif isinstance(e, AebBlockEvent):
                        self.parser_aeb_block_event()
                    elif isinstance(e, ExitedException) and self._case_type == 'parking':

                        title_xpath = self.app_config.get('main_title', {}).get('xpath', '').format('异常退出')
                        exited_flag = True
                        for _ in range(5):
                            sleep(15)
                            self.driver.press('back')
                            sleep(2)
                            self.enter_vpd(retry=True)
                            sleep(2)
                            exited_flag = self.driver.exists(xpath=title_xpath)
                            if not exited_flag:
                                break
                        if not exited_flag:
                            if self.driver.exists(**self.app_config.get('start')):
                                self.confirm_vpd()
                            continue

                    std_logger.info(f'[VPD][check_cruise_event] Throwing an exception, device_sn: {self._device_sn}，'
                                    f'type: {type(e)}')

                    exception_to_raise = e

                else:
                    event_time = datetime.now()
                    event_type = 2
                    if isinstance(e, (AebEvent, CruiseTriggerEvent)):
                        event_type = 1
                    self.save_event_db(str(e), event_type=event_type, event_time=event_time)
                    if isinstance(e, AebEvent):
                        self.parser_aeb_event()

            finally:

                if not is_charge:
                    conditions = [
                        self.driver.exists(**self.app_config.get('finish')),
                        self._app_type == '#02' and self.driver.exists(
                            xpath=self.app_config.get('main_title', {}).get('xpath', '').format('输入安全码')),
                        self._app_type == '#05' and self.driver.exists(**self.app_config.get('vpd_safe_code')),
                        self._app_type in ['#01', '#02', '#04', '#05', '#08','#09','#10'] and
                        self.driver.exists(**self.app_config.get('parking_finish')),
                        self._app_type in ['#01', '#02', '#04', '#05', '#08','#09','#10'] and
                        self.driver.exists(**self.app_config.get('summon_finish')),
                    ]
                else:
                    conditions = [
                        self.driver.exists(**self.app_config.get('charge_prepare')),
                        self.driver.exists(**self.app_config.get('charging')),
                        self.driver.exists(**self.app_config.get('end_charge'))
                    ]

                if exception_to_raise or any(conditions):

                    if any(conditions):

                        if case_type != 'summon':
                            cruise_dict.update({'n_success_park_or_summon': 1})

                        sleep(10)  # 等待车端画面加载

                    break

        self.__set_report_data(case_type, cruise_dict)

        if exception_to_raise and not self.driver.exists(**self.app_config.get('finish')):
            raise exception_to_raise

        if self._vpd_task.case_type == 5 and not is_called:  # 接电话兜底
            self.vpd_case.start_case()

    def check_finish_safe_code(self):
        if self._app_type == '#02':
            xpath = self.app_config.get('main_title', {}).get('xpath', '').format('输入安全码')

            conditions = [
                self.driver.exists(xpath=xpath),
                self.driver.exists(**self.app_config.get('clear_safe_code'))
            ]

            if all(conditions):
                self.driver.click(**self.app_config.get('clear_safe_code'))
                sleep(2)

        elif self._app_type == '#05':
            self.set_safe_code()

    def vpd_finish(self, case_type: str):
        self.check_finish_safe_code()

        sleep(2)

        if self._vpd_task.case_type == 13:  # 位置图片楼层用例
            self.vpd_case.vpd_location_case()

        self.driver.press('back')
        sleep(2)

        if self._device_type == 'Harmony':  # 鸿蒙设备固定打点 保证日志完整
            self.manual_trigger()

        if self._app_type in ['#01', '#02']:
            self.driver.press('back')

        if case_type == 'parking':
            sleep(self._vpd_task.vehicle_wait_time * 60)
        elif case_type == 'summon':
            sleep(self._vpd_task.vehicle_wait_time_summon * 60)

        if self._vpd_task.case_type == 6:  # 恢复全屏
            self.vpd_case.small_windows_case(operation='maximize_window')

    def save_event_db(self, message: str, event_type: int = 2, event_time: datetime = None):
        if event_type == 1:
            self.manual_trigger()  # 打点

        match = self._event_patten.search(message)

        if match:
            event_classify = match.group(1)
            event_describe = match.group(2)
        else:
            event_classify = '其他'
            event_describe = message

        event_stats = {
            'task_id': self._vpd_task.id,
            'vin_code': self._vin_code,
            'app_version': self._app_version,
            'app_name': self.app_name,
            'device_type': self._device_type,
            'event_time': event_time or datetime.now(),
            'event_classify': event_classify,
            'event_describe': event_describe,
            'event_type': event_type
        }

        with db.atomic():
            VpdTestEventStats.gateway().insert(event_stats)

    def save_report_db(self):
        insert_ls = []

        for function_type, stats in self._report_data.items():
            (n_park_or_summon, n_success_park_or_summon, n_calculate_road, n_success_calculate_road, n_cruise,
             n_cruise_quit) = map(stats.get, ['n_park_or_summon', 'n_success_park_or_summon', 'n_calculate_road',
                                              'n_success_calculate_road', 'n_cruise', 'n_cruise_quit'])

            stats.update({
                'task_id': self._vpd_task.id,
                'function': function_type,
                'rate_park_or_summon': round(n_success_park_or_summon / n_park_or_summon, 4) if
                n_park_or_summon > 0 else 0.0,
                'rate_cruise': round((n_cruise - n_cruise_quit) / n_cruise, 4) if n_cruise > 0 else 0.0,
                'rate_calculate_road': round(n_success_calculate_road / n_calculate_road, 4) if
                n_calculate_road > 0 else 0.0,
            })
            insert_ls.append(stats)

        with db.atomic():
            VpdTestStats.gateway().insert_many(insert_ls)

        self.pull_app_log()

    def check_stop_task(self):
        with db.atomic():
            task = VpdTestTask.gateway().get_by_id(self._vpd_task.id)
            if task.status == 3:  # 停止中
                raise ManualStopEvent()

    def screenrecord_manage(self, status: str):
        if self._vpd_task.is_record_video:
            if status == 'start':
                self.driver.screenrecord_start(output_path=self._video_path, device_name=self._device_name,
                                               task_name=self._vpd_task.task_name)
            elif status == 'stop':
                sleep(3)
                self.driver.screenrecord_stop()

    def pull_app_log(self):
        pass

    def enter_sentinel_mode(self):
        try:
            if self._app_type == "#01":
                self.driver.click(**self.app_config.get('car'))
                sleep(2)

                if self.driver.exists(**self.app_config.get('sentry_mode_status')) or \
                    self.driver.exists(**self.app_config.get('sentry_mode_status_new')):
                    return

                self.click_for_element('sentry_mode')
                self.slight_swipe_and_click('sentry_mode')
                self.driver.click(**self.app_config.get('open_sentry_mode'))
                self.wait_for_element(['confirm_sentry_mode'], if_appear=False, wait_time=30)
                self.driver.click(**self.app_config.get('confirm_sentry_mode'))
                sleep(1)
                self.driver.press('back')
                sleep(1)
                self.click_for_element('sentry_mode', if_click=False)
                self.wait_for_element(['sentry_mode_status', 'sentry_mode_status_new'], if_appear=False, wait_time=120)
                sleep(2)

                sleep(1)
                if not self.driver.exists(**self.app_config.get('sentry_mode_status')) and \
                        not self.driver.exists(**self.app_config.get('sentry_mode_status_new')):
                    raise SentinelException('开启哨兵模式失败！')

            elif self._app_type == "#08":
                self.driver.click(**self.app_config.get('discover'))
                sleep(2)
                self.driver.click(**self.app_config.get('sentry_mode'))
                sleep(2)

                if not self.driver.exists(**self.app_config.get('sentry_mode_status')):
                    self.driver.click(**self.app_config.get('open_sentry_mode'))
                    self.wait_for_element(['confirm_sentry_mode'], if_appear=False, wait_time=30)
                    self.driver.click(**self.app_config.get('confirm_sentry_mode'))
                    self.wait_for_element(['close_sentry_mode'], if_appear=False, wait_time=120)

                if not self.driver.exists(**self.app_config.get('sentry_mode_status')):
                    raise SentinelException('开启哨兵模式失败！')

                self.driver.press('back')
                sleep(2)

        except Exception as e:
            std_logger.error(f'[enter_sentinel_mode] error={str(e)} traceback={traceback.format_exc()}')
            raise SentinelException(str(e))

    def exit_sentinel_mode(self):
        try:
            if self._app_type == "#01":
                self.driver.click(**self.app_config.get('car'))
                sleep(2)

                if not self.driver.exists(**self.app_config.get('sentry_mode_status')) and \
                    not self.driver.exists(**self.app_config.get('sentry_mode_status_new')):
                    return

                self.click_for_element('sentry_mode')
                self.slight_swipe_and_click('sentry_mode')
                self.driver.click(**self.app_config.get('close_sentry_mode'))
                sleep(1)
                self.driver.press('back')
                sleep(1)
                self.click_for_element('sentry_mode', if_click=False)
                self.wait_for_element(['sentry_mode_status_off'], if_appear=False, wait_time=120)

                if self.driver.exists(**self.app_config.get('sentry_mode_status')) or \
                        self.driver.exists(**self.app_config.get('sentry_mode_status_new')):
                    raise SentinelException('关闭哨兵模式失败！')

            elif self._app_type == "#08":
                self.driver.click(**self.app_config.get('discover'))
                sleep(2)
                self.driver.click(**self.app_config.get('sentry_mode'))
                sleep(2)

                if not self.driver.exists(**self.app_config.get('sentry_mode_status_off')):
                    self.driver.click(**self.app_config.get('close_sentry_mode'))
                    sleep(2)
                    if self.driver.exists(**self.app_config.get('confirm_sentry_mode')):
                        self.driver.click(**self.app_config.get('confirm_sentry_mode'))
                    self.wait_for_element(['open_sentry_mode'], if_appear=False, wait_time=120)
                    if not self.driver.exists(**self.app_config.get('open_sentry_mode')):
                        raise SentinelException('关闭哨兵模式失败！')
                self.driver.press('back')

            sleep(2)

        except Exception as e:
            std_logger.error(f'[exit_sentinel_mode] error={str(e)} traceback={traceback.format_exc()}')
            raise SentinelException(str(e))

    def charge_for_consumer(self, case_type: str, is_charged: bool):
        if self._app_type != '#08':
            return True

        if self.driver.exists(**self.app_config.get('charge_error')):
            error_msg = self.driver.get_text(**self.app_config.get('charge_error_detail'))
            raise ChargeError(error_msg)
        if not is_charged:

            if not self.driver.exists(**self.app_config.get('charging_check')):
                self.vpd_case.charge_for_consumer_case_1()

                self.check_cruise_event(case_type, parking_space='', answer_call_route={},
                                        is_charge=True)
            else:
                self.driver.click(**self.app_config.get('charging_in'))
                sleep(2)

            if not self.driver.exists(**self.app_config.get('confirm_end_charge')):
                self.driver.swipe(x1=0.50, y1=0.85, x2=0.50, y2=0.3)

            self.vpd_case.charge_for_consumer_case_2()

        return True

    def check_device_battery(self):

        BATTERY_THRESHOLD = config_util.config.get('battery_threshold')
        BATTERY_THRESHOLD = int(BATTERY_THRESHOLD or 25)
        if self._device_type == 'Android':
            current_battery = adb_get_battery_level(self._device_sn)
        elif self._device_type == 'Harmony':
            current_battery = hdc_get_battery_level(self._device_sn)
        else:
            raise Exception(f"暂不支持设备类型{self._device_type}")

        if current_battery < BATTERY_THRESHOLD:
            raise Exception(f"电量低于阈值：当前电量{current_battery}%，阈值{BATTERY_THRESHOLD}%，任务不执行")

        return current_battery

    def unlock_screen(self):
        pass

    def tap_by_scale(self, x_scale: float, y_scale: float):
        pass

    def run(self):
        try:
            self.unlock_screen()
            self.screenrecord_manage('start')

            if self._vpd_task.case_type in [11, 12]:
                self.__run_v2()
            else:
                self.__run()

        except Exception as e:
            raise e

        finally:
            self.save_report_db()

            try:
                self.screenrecord_manage('stop')
            except Exception as e:
                std_logger.error(f'[VPD][screenrecord_manage] stop screenrecord failed: {str(e)}')

            try:
                self.driver.release_driver()  # 释放资源
            except Exception as e:
                std_logger.error(f'[VPD][release_driver] release driver failed: {str(e)}')

    def __run(self):
        start_time, n_execute = time(), 0

        if self._vpd_task.case_type == 7:  # 弱网
            self.vpd_case.start_case()

        answer_call_route = {}
        if self._vpd_task.case_type == 5:  # 接电话
            answer_call_route = random.choice(self.route_list)

        self.check_device_battery()

        while True:

            for route_info in self.route_list:

                is_charged = False

                for _ in range(2):

                    try:
                        for case_type in ['parking', 'summon']:
                            parking_space = route_info.get(case_type)
                            if not parking_space:
                                continue

                            self.app_start()
                            self.enter_vpd()

                            if self._vpd_task.case_type == 10:  # 代客充电
                                is_charged = self.charge_for_consumer(case_type, is_charged)

                            self.start_vpd(case_type, parking_space)
                            self.check_cruise_event(case_type, parking_space, answer_call_route)
                            self.vpd_finish(case_type)

                            break

                        if self._case_type == 'parking':
                            self.check_stop_task()

                            if self._vpd_task.case_type == 3:  # 哨兵
                                self.vpd_case.start_case()

                            if self._vpd_task.case_type == 9:  # 开关哨兵
                                self.vpd_case.start_case()

                            conditions = [  # 按时间执行 在泊车任务才会停止
                                self._vpd_task.execute_type == 1,
                                time() - start_time >= self._vpd_task.n_execute * 3600
                            ]

                            if all(conditions):
                                raise StopEvent()

                        self._n_retry = 0
                        self.app_stop()
                        break

                    except Exception as e:
                        std_logger.info(f'[VPD][__run] device_sn: {self._device_sn}, reason: {str(e)},'
                                        f' traceback: {traceback.format_exc()}')

                        self._n_retry += 1
                        sleep(3)

                        if isinstance(e, (ManualStopEvent, StopEvent)):

                            try:
                                self.screenrecord_manage('stop')
                            except Exception as screenrecord_e:
                                std_logger.info(f'[VPD][screenrecord_manage] device_sn: {self._device_sn}， stop '
                                                f'screenrecord failed: {str(screenrecord_e)}')

                            raise e

                        else:

                            self.save_event_db(str(e))
                            if self._n_retry >= 3:
                                raise ManualTakeoverEvent()

                        self.app_stop()

                        if isinstance(e, NoMapException):
                            sleep(300)  # 5分钟确保下电

                        continue

            if self._vpd_task.execute_type == 2:  # 按次数执行
                n_execute += 1
                if n_execute >= self._vpd_task.n_execute:
                    break

    def __run_v2(self):
        self.vpd_case.start_case()


class ManualEvent:

    def __init__(self):
        self._event_map = {
            'parking': {'timestamp': 0, 'statistic_ls': [], 'duration': 0, 'if_continuity': False},
            'risk': {'timestamp': 0, 'statistic_ls': [], 'duration': 0, 'if_continuity': False},
            'parking_in': {'timestamp': 0, 'duration': 0, 'if_continuity': False},
            'parking_out': {'timestamp': 0, 'duration': 0, 'if_continuity': False},
            'speed': {'last': -1, 'duration': 0, 'timestamp': 0, 'if_trigger': False, 'if_block_50': False},
            'aeb': {'timestamp': 0, 'statistic_ls': []},
            'walk': {'event_type': '', 'timestamp': 0}
        }
        self._event_func_map = {
            1: ('parking_in', self.parking_event),
            2: ('parking_out', self.parking_event),
            3: ('risk', self.parking_or_risk_event),
            4: ('parking', self.parking_or_risk_event),
            5: ('parking', self.parking_or_risk_event),
            6: ('cruise', self.clear_duration),
            7: ('walk', self.walk_event),
            8: ('cruise_normal', self.clear_duration),
        }

    def __update_if_continuity(self, not_update_key: str):
        for k, v in self._event_map.items():

            if k == not_update_key:
                continue

            if 'if_continuity' in v:
                v['if_continuity'] = False

    def speed_event(self, speed: str, time_now: float):
        conditions = [
            isinstance(speed, str) and speed.isdigit(),
            isinstance(speed, int)
        ]

        if not any(conditions):
            return

        last, duration, timestamp, if_trigger, if_block_50 = map(
            self._event_map['speed'].get, ['last', 'duration', 'timestamp', 'if_trigger', 'if_block_50'])

        if last == -1:
            duration = 0
            if_trigger = False
            if_block_50 = False
        elif int(speed) == 0 and last == 0:
            duration += (time_now - timestamp)
        else:
            duration = 0
            if_trigger = False
            if_block_50 = False

        last = int(speed)

        self._event_map['speed'].update({'last': last, 'timestamp': time_now, 'duration': duration,
                                         'if_trigger': if_trigger, 'if_block_50': if_block_50})

        if_manual_trigger = duration >= 30
        if_cruise_block_50 = duration >= 50
        if_cruise_block = duration >= 240

        if if_manual_trigger and not if_trigger:
            self._event_map['speed'].update({'if_trigger': True})
            raise CruiseTriggerEvent()
        if if_cruise_block_50 and not if_block_50:
            self._event_map['speed'].update({'if_block_50': True})
            raise CruiseRecordEvent()
        if if_cruise_block:
            raise CruiseBlockEvent()

    def parking_event(self, key: str, time_now: float):
        """泊入/泊出事件"""
        self.__update_if_continuity(key)

        timestamp, duration, if_continuity = map(self._event_map[key].get, ['timestamp', 'duration', 'if_continuity'])

        if timestamp != 0 and if_continuity:
            duration += (time_now - timestamp)

        if_timeout = duration >= 50

        if if_timeout or not if_continuity:
            duration = 0

        self._event_map[key].update({'timestamp': time_now, 'duration': duration, 'if_continuity': True})

        if if_timeout:
            if key == 'parking_in':
                raise ParkingInTimeoutException()
            elif key == 'parking_out':
                raise ParkingOutTimeoutException()

    def parking_or_risk_event(self, key: str, time_now: float):
        """临停数或脱困事件"""
        self.__update_if_continuity(key)

        timestamp, statistic_ls, duration, if_continuity = map(
            self._event_map[key].get, ['timestamp', 'statistic_ls', 'duration', 'if_continuity'])

        if timestamp != 0:

            if if_continuity:
                duration += (time_now - timestamp)

            else:
                statistic_ls.append(time_now)
                duration = 0

        timeout_enabled = duration >= 50
        toggle_enabled = (len(statistic_ls) >= 2 and (statistic_ls[-1] - statistic_ls[-2]) <= 60)

        if timeout_enabled:
            duration = 0

        if len(statistic_ls) >= 2:
            statistic_ls = statistic_ls[-1:]

        self._event_map[key].update({'timestamp': time_now, 'statistic_ls': statistic_ls, 'duration': duration,
                                     'if_continuity': True})

        timeout_exceptions = {
            'parking': ParkingTimeoutException,
            'risk': RiskTimeoutException
        }

        toggle_exceptions = {
            'parking': ParkingToggleException,
            'risk': RiskToggleException
        }

        if timeout_enabled:
            if exception_cls := timeout_exceptions.get(key):
                raise exception_cls()

        if toggle_enabled:
            if exception_cls := toggle_exceptions.get(key):
                raise exception_cls()

    def aeb_event(self, time_now: float, if_aeb: bool):

        if not if_aeb:
            return

        timestamp, statistic_ls = map(self._event_map['aeb'].get, ['timestamp', 'statistic_ls'])
        statistic_ls.append(time_now)

        if_aeb_block = len(statistic_ls) >= 2 and (statistic_ls[-1] - statistic_ls[-2] <= 60)

        if len(statistic_ls) >= 2:
            statistic_ls = statistic_ls[-1:]

        self._event_map['aeb'].update({'timestamp': time_now, 'statistic_ls': statistic_ls})

        if if_aeb_block:
            raise AebBlockEvent()

        raise AebEvent()

    def check_evnet(self, scene_type: int, speed: [str, int], if_aeb: bool):
        time_now = time()

        self.aeb_event(time_now, if_aeb)

        event_type, event_func = self._event_func_map.get(scene_type, (None, None))
        if event_type and event_func:
            if event_type != 'walk':
                self._event_map['walk'].update({'event_type': event_type})
            event_func(event_type, time_now)

        self.speed_event(speed, time_now)

    def clear_duration(self, key: str, time_now: float):
        for key, value in self._event_map.items():
            if key == 'speed':
                continue
            if value.get('duration'):
                value['duration'] = 0
            if value.get('if_continuity'):
                value['if_continuity'] = False

    def walk_event(self, key: str, time_now: float):
        pre_event_type, timestamp = map(self._event_map['walk'].get, ['event_type', 'timestamp'])
        self._event_map['walk'].update({'event_type': key})
        if pre_event_type == 'cruise_normal' and time() - timestamp > 30:
            self._event_map['walk'].update({'timestamp': time()})
            raise CruiseWalkEvent()


class VPDCase:

    def __init__(self, vpd: VPD, vpd_task: VpdTestTask):
        self._vpd = vpd
        self._vpd_task = vpd_task

        self._case_map = {
            1: self.pause_to_continue_case,  # 暂停继续
            2: self.pause_to_quit_case,  # 暂停退出
            3: self.sentinel_mode_case,  # 哨兵模式
            4: self.background_case,  # 切后台
            5: self.answering_phone_case,  # 接电话
            6: self.small_windows_case,  # 小窗口
            7: self.weak_network_case,  # 弱网
            8: self.kill_background_case,  # 杀后台
            9: self.sentinel_mode_case_2,  # 开关哨兵
            11: self.power_on_stress_case,  # 上电率
            12: self.sentinel_connect_vpd_case,  # 哨兵衔接VPD
        }

    def pause_to_continue_case(self):
        for _ in range(self._vpd_task.case_execute_nums):
            self._vpd.click_pause()
            sleep(self._vpd_task.case_interval_time)
            self._vpd.click_continue()

    def pause_to_quit_case(self):
        self._vpd.click_pause()
        sleep(self._vpd_task.case_interval_time)
        self._vpd.click_quit()

    def sentinel_mode_case(self):
        self._vpd.click_for_element('sentry_mode', if_click=False)
        if not self._vpd.driver.exists(**self._vpd.app_config.get('sentry_mode_status')):
            for key in ['sentry_mode', 'open_sentry_mode', 'confirm_sentry_mode']:
                self._vpd.driver.click(**self._vpd.app_config.get(key))
                sleep(2)

            self._vpd.wait_for_element(['loading_sentry_mode'])
            self._vpd.driver.press('back')
            sleep(2)

    def background_case(self):
        self._vpd.driver.press('home')
        sleep(self._vpd_task.case_execute_nums)
        self._vpd.driver.app_start(**self._vpd.app_config.get('package'))

    def answering_phone_case(self):
        if cache.acquire_hash_lock('answering_phone_case', 'dialing_device'):  # 获取执行锁
            try:
                dialing_device = config_util.config.get('dialing_device')
                answer_phone_num = self._vpd_task.device_info.get('phone_number')
                if dialing_device and answer_phone_num:
                    adb_start_call(dialing_device, answer_phone_num)

                    device_sn = self._vpd_task.device_info.get('device_sn')
                    is_calling, start_time = False, time()
                    while time() - start_time <= 10:  # 最大等待来电时间
                        call_state = adb_get_call_state(device_sn)
                        if call_state == 1:
                            is_calling = True
                            break

                        sleep(1)

                    sleep(1)
                    if is_calling:
                        adb_answer_call(device_sn)
                        sleep(self._vpd_task.case_interval_time)  # 通话维持时间
                        adb_stop_call(device_sn)
            except Exception as e:
                std_logger.error(f'[VPD][answering_phone_case] answer call failed: {str(e)}')
            finally:
                cache.release_hash_lock('answering_phone_case', 'dialing_device')  # 释放执行锁

    def click_device_element(self, device_os: str, config_key: str, wait_time: int = 20):
        time_now = time()
        while time() - time_now < wait_time:
            element = self._vpd.driver.exists(**self._vpd.app_config.get(device_os).get(config_key))
            if not element:
                self._vpd.driver.swipe(x1=0.8, y1=0.5, x2=0.2, y2=0.5)  # 右划
            else:
                self._vpd.driver.click(**self._vpd.app_config.get(device_os).get(config_key))
                break

            sleep(1)

        sleep(2)

    def hw_windows_operation(self, device_os, operation):
        device_type = self._vpd_task.device_info.get('device_type')
        if device_type == 'Android':
            if operation == 'small_window':
                self._vpd.driver.press('recent')
                sleep(2)
                self.click_device_element(device_os, operation)
            elif operation == 'maximize_window':
                self._vpd.driver.click(**self._vpd.app_config.get(device_os).get(operation))
        elif device_type == 'Harmony':
            pass

    def xiaomi_windows_operation(self, device_os, operation):
        if operation == 'small_window':
            self._vpd.driver.press('recent')
            sleep(2)
            self._vpd.driver.long_click(**self._vpd.app_config.get(device_os).get('app_name'))
            self._vpd.driver.click(**self._vpd.app_config.get(device_os).get(operation))
        elif operation == 'maximize_window':
            bottom_nav_bar = self._vpd.app_config.get(device_os).get('bottom_nav_bar')
            if bottom_nav_bar and self._vpd.driver.exists(**bottom_nav_bar):
                element = self._vpd.driver.xpath(bottom_nav_bar['xpath'])
                fx, fy = element.get().center()
                self._vpd.driver.swipe(x1=fx, y1=fy, x2=0.5, y2=0.9)

    def vivo_windows_operation(self, device_os, operation):
        if operation == 'small_window':
            self._vpd.driver.press('recent')
            sleep(2)
            self.click_device_element(device_os, 'app_name')
        self._vpd.driver.click(**self._vpd.app_config.get(device_os).get(operation))

    def small_windows_case(self, operation: str = 'small_window'):
        sleep(2)  # 等待元素稳定
        device_os = self._vpd_task.device_info.get('device_os', '')
        device_windows_operation = {
            'harmony_os': self.hw_windows_operation,
            'magic_os': self.hw_windows_operation,
            'hyper_os': self.xiaomi_windows_operation,
            'miui': self.xiaomi_windows_operation,
            'origin_os': self.vivo_windows_operation,
        }
        if device_os in device_windows_operation:
            device_windows_operation[device_os](device_os, operation)
        sleep(2)

    def weak_network_case(self):
        """预装weaknet，开启权限"""
        if WEAKNET_ELEMENT.get('package', {}).get('package_name') in self._vpd.driver.app_list():
            self._vpd.driver.app_start(**WEAKNET_ELEMENT.get('package'))
            sleep(2)  # 等待元素稳定
            self._vpd.driver.click(**WEAKNET_ELEMENT.get('search_trigger'))
            sleep(2)
            self._vpd.driver.set_text(self._vpd.app_name, **WEAKNET_ELEMENT.get('search_text'))
            sleep(2)
            package_name = self._vpd.app_config.get('package', {}).get('package_name')
            self._vpd.driver.click(xpath=f'//*[@text="{package_name}"]')
            sleep(2)
            self._vpd.driver.click(**WEAKNET_ELEMENT.get('2g_net'))
            sleep(2)
            self._vpd.driver.click(**WEAKNET_ELEMENT.get('start'))
            sleep(2)
            if self._vpd.driver.exists(**WEAKNET_ELEMENT.get('connect_request')):
                self._vpd.driver.click(**WEAKNET_ELEMENT.get('confirm'))
            sleep(2)
            if self._vpd.driver.exists(**WEAKNET_ELEMENT.get('move_toast')):
                elements = self._vpd.driver.xpath(WEAKNET_ELEMENT.get('move_toast', {}).get('xpath')).get()
                x1, y1 = elements.center()
                self._vpd.driver.swipe(x1=x1, y1=y1, x2=0.0, y2=y1)
        sleep(2)

    def kill_background_case(self):
        self._vpd.app_stop()
        sleep(self._vpd_task.case_execute_nums)
        self._vpd.app_start()
        self._vpd.enter_vpd()

        sleep(3)

    def sentinel_mode_case_2(self):
        if self._vpd._device_type == "Harmony":
            self._vpd.click_for_element('', img_name='sentry_mode.jpeg', similarity=0.8)
            for key in ['open_sentry_mode', 'confirm_sentry_mode']:
                self._vpd.driver.click(**self._vpd.app_config.get(key))
                sleep(2)
            self._vpd.wait_for_element(['close_sentry_mode'], if_appear=False)
            sleep(120)  # 开启2分钟
            self._vpd.driver.click(**self._vpd.app_config.get('close_sentry_mode'))
            self._vpd.wait_for_element(['open_sentry_mode'], if_appear=False)
            sleep(2)
            self._vpd.driver.press('back')
            sleep(2)
        else:
            if self._vpd._app_type == "#02":
                self._vpd.driver.click(**self._vpd.app_config.get('all_control'))
                sleep(2)
            self._vpd.click_for_element('sentry_mode', if_click=False)
            if not self._vpd.driver.exists(**self._vpd.app_config.get('sentry_mode_status')):
                for key in ['sentry_mode', 'open_sentry_mode', 'confirm_sentry_mode']:
                    self._vpd.driver.click(**self._vpd.app_config.get(key))
                    sleep(2)

            self._vpd.wait_for_element(['close_sentry_mode'], if_appear=False)
            sleep(120)  # 开启2分钟
            self._vpd.driver.click(**self._vpd.app_config.get('close_sentry_mode'))
            if self._vpd._app_type == "#02" and \
               self._vpd.driver.exists(**self._vpd.app_config.get('close_confirm_sentry_mode')):
                self._vpd.driver.click(**self._vpd.app_config.get('close_confirm_sentry_mode'))
            self._vpd.wait_for_element(['open_sentry_mode'], if_appear=False)
            sleep(2)
            self._vpd.driver.press('back')
            sleep(2)

    def charge_for_consumer_case_1(self):
        sleep(2)
        if not self._vpd.driver.exists(**self._vpd.app_config.get('start')):
            if not self._vpd.driver.exists(**self._vpd.app_config.get('charge_station')):
                raise ChargeError('没有充电站按钮')

            self._vpd.driver.click(**self._vpd.app_config.get('charge_station'))
            sleep(2)
            if not self._vpd.driver.exists(**self._vpd.app_config.get('to_charge')):
                raise ChargeError('没有空闲充电车位')

            self._vpd.driver.click(**self._vpd.app_config.get('to_charge'))

        self._vpd.wait_for_element(['charge_start'], if_appear=False, wait_time=120)
        if not self._vpd.driver.exists(**self._vpd.app_config.get('charge_start')):
            raise ChargeError('开始按钮不可用')

        self._vpd.confirm_vpd()

    def charge_for_consumer_case_2(self):
        try:
            max_wait_time = 120
            start_time = time()
            while time() - start_time <= max_wait_time:

                sleep(2)

                if self._vpd.driver.exists(**self._vpd.app_config.get('charge_error')):
                    error_msg = self._vpd.driver.get_text(**self._vpd.app_config.get('charge_error_detail'))
                    if '即将驶离前往默认车位' in error_msg:
                        raise ChargeErrorBackEvent()
                    else:
                        raise ChargeError(error_msg)

                if self._vpd.driver.exists(**self._vpd.app_config.get('end_charge')):
                    break

            if not self._vpd.driver.exists(**self._vpd.app_config.get('end_charge')):
                raise ChargeError('结束充电按钮不可用')

            sleep(5)

            self._vpd.driver.click(**self._vpd.app_config.get('end_charge'))
            sleep(2)
            self._vpd.driver.click(**self._vpd.app_config.get('confirm_end_charge'))

            start_time = time()
            while time() - start_time <= max_wait_time:

                sleep(2)

                if self._vpd.driver.exists(**self._vpd.app_config.get('charge_error')):
                    error_msg = self._vpd.driver.get_text(**self._vpd.app_config.get('charge_error_detail'))
                    raise ChargeError(error_msg)

                if self._vpd.driver.exists(**self._vpd.app_config.get('summon_before_end_charge')):
                    break

            if not self._vpd.driver.exists(**self._vpd.app_config.get('summon_before_end_charge')):
                raise ChargeError('召唤按钮不可用')

            self._vpd.driver.click(**self._vpd.app_config.get('summon_before_end_charge'))
            sleep(2)
        except Exception as e:
            if isinstance(e, ChargeErrorBackEvent):
                self._vpd.save_event_db(str(e), event_type=1)
                self._vpd.parser_charge_error_back_event()
            else:
                raise e

    def power_on_stress_case(self):
        execute_nums = min(self._vpd_task.case_execute_nums, 200)  # 最大执行次数
        final_route_list = list(islice(cycle(self._vpd.route_list), execute_nums))
        for cur_epoch, route_info in enumerate(final_route_list, start=1):
            std_logger.info(f'[power_on_stress_case] execute epoch={cur_epoch}, route_info={route_info}')
            t1, t2, t3, t4, t5, result, remark = None, None, None, None, None, None, None
            try:
                self._vpd.check_stop_task()

                self._vpd.app_stop()
                sleep(180)  # 等待3分钟

                self._vpd.app_start()

                self._vpd.enter_vpd_v2()

                if len(route_info) == 1:
                    case_type, parking_space = list(route_info.items())[0]
                    t1, _ = self._vpd.input_parking_space(parking_space, if_wait=False)

                    self._vpd.driver.click(**self._vpd.app_config.get(case_type))

                elif self._vpd.driver.exists(**self._vpd.app_config.get('vpd_success')):
                    t1 = datetime.now()
                    self._vpd.driver.click(**self._vpd.app_config.get('vpd_success'))
                else:
                    raise Exception('没有可用目标车位')

                time_now = time()
                while time() - time_now < VPD_CALCULATED_WAIT_TIME:
                    sleep(0.5)

                    if not t2 and self._vpd.driver.exists(xpath='//*[@text="前视"]'):
                        t2 = datetime.now()

                    judge_result, fail_tip = self._vpd.judge_calculate_result(is_wait=False)

                    if judge_result is True:
                        result = 'pass'
                        t4 = datetime.now()
                        self._vpd.save_event_db(message='上电成功', event_type=1, event_time=t4)
                        break

                    std_logger.info(f'[power_on_stress_case] get cruise fail tip: {fail_tip}')
                    if fail_tip in ['泊车代驾辅助暂不可用', '路径规划失败', '路线规划失败', '抱歉，无可达路径']:
                        sub_title_xpath = self._vpd.app_config.get('sub_title', {}).get('xpath', '').format(fail_tip)
                        sub_title = self._vpd.driver.get_text(xpath=sub_title_xpath)
                        if fail_tip == '泊车代驾辅助暂不可用':
                            if not t3:
                                t3 = datetime.now()
                                error_msg = f'{fail_tip}: {sub_title}'
                                remark = error_msg
                                self._vpd.save_event_db(message=error_msg, event_type=1, event_time=t3)
                            elif (datetime.now() - t3).total_seconds() > 10:
                                result = 'fail'
                                break

                        if fail_tip in ['路径规划失败', '路线规划失败', '抱歉，无可达路径']:
                            t5 = datetime.now()
                            error_msg = f'{fail_tip}: {sub_title}'
                            remark = error_msg
                            self._vpd.save_event_db(message=error_msg, event_type=1, event_time=t5)
                            result = 'fail'
                            break

                if result is None:
                    raise Exception('未匹配到预期title')

            except Exception as e:
                result, remark, t5 = 'error', str(e), datetime.now()
                std_logger.info(f'[power_on_stress_case] error: {str(e)}, traceback: {traceback.format_exc()}')
                if isinstance(e, ManualStopEvent):
                    raise e

            finally:
                self._vpd.app_stop()
                sleep(120)  # 等待2分钟

                power_on_time = (t4 - t1).total_seconds() if t1 and t4 else None
                VpdTestCaseStats.new(task_id=self._vpd_task.id, case_type=self._vpd_task.case_type,
                                     vin_code=self._vpd._vin_code, vehicle_name='',
                                     app_name=self._vpd.app_name, app_version=self._vpd._app_version,
                                     ads_version='', device_type=self._vpd._device_type,
                                     result=result, t1=t1, t2=t2, t3=t3, t4=t4, t5=t5, power_on_time=power_on_time,
                                     remark=remark)

    def sentinel_connect_vpd_case(self):
        execute_nums = min(self._vpd_task.case_execute_nums, 200)  # 最大执行次数
        final_route_list = list(islice(cycle(self._vpd.route_list), execute_nums))
        case_scene_ls = self._vpd_task.case_scene_ls or [1, 2, 3]
        sub_case_list = list(islice(cycle(case_scene_ls), execute_nums))

        for cur_epoch, (route_info, sub_case_type) in enumerate(zip(final_route_list, sub_case_list), start=1):
            std_logger.info(f'[power_on_stress_case] execute epoch={cur_epoch}, route_info={route_info},'
                            f'sub_case_type={sub_case_type}')
            t4, t5, result, remark = None, None, None, None
            try:
                self._vpd.check_stop_task()
                self._vpd.app_stop()
                sleep(180)  # 等待3分钟

                self._vpd.app_start()

                if sub_case_type == 1:  # 哨兵开启3分钟后，进入VPD
                    self._vpd.enter_sentinel_mode()
                    sleep(180)
                elif sub_case_type == 2:  # 哨兵开启3分钟后，关闭哨兵，进入VPD
                    self._vpd.enter_sentinel_mode()
                    sleep(180)
                    self._vpd.exit_sentinel_mode()
                elif sub_case_type == 3:  # 哨兵开启3分钟后，关闭哨兵，3分钟后进入VPD
                    self._vpd.enter_sentinel_mode()
                    sleep(180)
                    self._vpd.exit_sentinel_mode()
                    sleep(180)
                else:
                    std_logger.info(f'[sentinel_connect_vpd_case] not match sub_case: {sub_case_type},'
                                    f'cur_epoch: {cur_epoch}')
                    continue

                case_type, parking_space = next(iter(route_info.items()))
                self._vpd.enter_vpd_v2()
                self._vpd.start_vpd(case_type, parking_space)
                result, t4 = 'pass', datetime.now()  # 激活成功
                self._vpd.check_cruise_event(case_type, parking_space)
                self._vpd.vpd_finish(case_type)


            except Exception as e:
                std_logger.info(f'[sentinel_connect_vpd_case] error: {str(e)}, traceback: {traceback.format_exc()}')

                if isinstance(e, ManualStopEvent):
                    result, remark = 'error', str(e)
                    raise e
                elif isinstance(e, (UnavailableException, ExitedException, PlanningFailureException)):
                    fail_tip = self._vpd.driver.get_text(**self._vpd.app_config.get('cruise_tip'))
                    sub_title_xpath = self._vpd.app_config.get('sub_title', {}).get('xpath', '').format(fail_tip)
                    sub_title = self._vpd.driver.get_text(xpath=sub_title_xpath)
                    result, remark, t5 = 'fail', f'{fail_tip}: {sub_title}', datetime.now()
                else:
                    result, remark, t5 = 'error', str(e), datetime.now()

            finally:
                VpdTestCaseStats.new(task_id=self._vpd_task.id, case_type=self._vpd_task.case_type,
                                     sub_case_type=sub_case_type, vin_code=self._vpd._vin_code, vehicle_name='',
                                     app_name=self._vpd.app_name, app_version=self._vpd._app_version,
                                     ads_version='', device_type=self._vpd._device_type,
                                     result=result, t4=t4, t5=t5, remark=remark)


    def vpd_location_case(self):
        # 运行结束没有位置信息
        pass

    def vpd_location_case_2(self, epoch):
        # 记录车辆位置信息
        enter_vpd_time, result, remark = None, '', ''
        try:
            self._vpd.app_start()
            self._vpd.enter_vpd()
            enter_vpd_time = datetime.now()  # 进图时间

            floor = self._vpd.driver.get_text(**self._vpd.app_config.get('floor')),
            if isinstance(floor, tuple):
                floor, = floor

            self._vpd.driver.swipe(x1=0.5, y1=0.6, x2=0.5, y2=0.4)
            sleep(1)
            if self._vpd._app_type == '#01':
                self._vpd.driver.swipe(x1=0.5, y1=0.6, x2=0.5, y2=0.4)
            else:
                self._vpd.driver.swipe(x1=0.5, y1=0.8, x2=0.5, y2=0.4)
            sleep(1)
            location = self._vpd.driver.get_text(**self._vpd.app_config.get('park_location'))
            location = location.strip() if location and isinstance(location, str) else '未知'
            if self._vpd._app_type == '#01':
                location = location.split(' ')[0]
                image_y = 0.8
            else:
                image_y = 0.6

            image_result = 'pass'
            image_view = ['俯视', '前视', '后视', '左前视', '右前视', '左后视', '右后视']

            try:
                for i in range(2):
                    if i == 0:
                        x2 = 0.2
                        final_image_view = image_view
                    else:
                        x2 = 0.8
                        final_image_view = [image_view[0]] + image_view[1:][::-1]

                    for view in final_image_view:
                        view_name  = self._vpd.driver.get_text(**self._vpd.app_config.get('view_name'))
                        if view != view_name:
                            raise Exception(f'视角不匹配，按顺序应为：{view}，当前视角为：{view_name}')
                        self._vpd.driver.swipe(x1=0.5, y1=image_y, x2=x2, y2=image_y)
                        sleep(1)

                if self._vpd._app_type == '#01':
                    if self._vpd._device_type == 'Android':
                        self._vpd.tap_by_scale(x_scale=0.5, y_scale=0.8)
                    else:
                        self._vpd.driver.click(x=0.5, y=0.8)
                else:
                    if self._vpd._device_type == 'Android':
                        self._vpd.tap_by_scale(x_scale=0.5, y_scale=0.6)
                sleep(2)

                for i in range(2):
                    if i == 0:
                        x1, x2 = 0.8, 0.2
                    else:
                        x1, x2 = 0.2, 0.8
                    for view in image_view:
                        self._vpd.driver.swipe(x1=0.5, y1=image_y, x2=x2, y2=image_y)
                        sleep(1)

                self._vpd.driver.press('back')
                if not self._vpd.driver.exists(**self._vpd.app_config.get('park_location')):
                    raise Exception('返回异常')

            except Exception as e:
                std_logger.info(f'[vpd_location_case_2] image_result error: {str(e)}, traceback: {traceback.format_exc()}')
                image_result = str(e)

            result = f'{floor}###{location}###{image_result}'

        except Exception as e:
            std_logger.info(f'[vpd_location_case_2] error: {str(e)}, traceback: {traceback.format_exc()}')
            result, remark = 'error', str(e)

        finally:
            VpdTestCaseStats.new(task_id=self._vpd_task.id, case_type=self._vpd_task.case_type,
                                 vin_code=self._vpd._config_vin_code, vehicle_name='',
                                 app_name=self._vpd.app_name, app_version=self._vpd._app_version,
                                 ads_version='', device_type=self._vpd._device_type,
                                 result=result, t1=enter_vpd_time, remark=remark,
                                 device_sn=self._vpd._device_sn, device_name=self._vpd._device_name,
                                 epoch=epoch)
            self._vpd.app_stop()


    def start_case(self):
        self._case_map[self._vpd_task.case_type]()


class AppLoginCar:
    def __init__(
        self,
        vpd: VPD,
        username: str,
        password: str,
        vin_code: str
    ):
        self.vpd = vpd
        self.driver = vpd.driver
        self.app_config = vpd.app_config
        self.device_type = vpd._device_type
        self.app_type = vpd._app_type

        self.username = username.strip()
        self.password = password.strip()
        self.vin_code = vin_code.strip()
        self.vin_suffix = self.vin_code[-6:]
        self.tag = "[AppLoginCar]"

    def _login(self):
        std_logger.info(self.tag + f"开始执行登录流程，账号：{self.username}")
        try:
            if self.app_type == "#01":
                if self.driver.exists(**self.app_config.get("i_know_button")):
                    self.driver.click(**self.app_config.get("i_know_button"))
                    sleep(2)

                self.driver.click(**self.app_config.get('my'))
                sleep(2)
            elif self.app_type == "#08":
                self.driver.click(**self.app_config.get('discover'))
                sleep(2)
            is_logout = self._get_login_status()
            if not is_logout:
                self._account_logout()
                sleep(2)

            if self.app_type == "#01":
                self.driver.click(**self.app_config.get("assess_info"))
                sleep(2)
                self.driver.click(**self.app_config.get("account_btn"))
                sleep(2)

                self.vpd.wait_for_element(config_keys=["number_input"], if_appear=False)
                self.driver.set_text(self.username, **self.app_config.get("number_input"))
                sleep(2)

                manual_btn = self.app_config.get("manual_btn")
                if manual_btn and self.driver.exists(**manual_btn):
                    self.driver.click(**manual_btn)
                    self.driver.set_text(self.username, **self.app_config.get("number_input"))
                    sleep(2)

                self.driver.click(**self.app_config.get("pwd_input"))
                sleep(2)
                self._input_password(self.password)
                sleep(2)

                self.driver.click(x=0.50, y=0.15)
                sleep(2)

                self.driver.click(**self.app_config.get("login_btn"))
                sleep(2)

                if self.driver.exists(**self.app_config.get("confirm_button")):
                    self.driver.click(**self.app_config.get("confirm_button"))
                    sleep(2)

            elif self.app_type == "#08":
                self.driver.click(**self.app_config.get("assess_icon"))
                sleep(2)
                self.driver.click(**self.app_config.get("login_icon"))
                sleep(2)
                self.driver.click(**self.app_config.get("pwd_login"))
                sleep(2)

                self.vpd.wait_for_element(config_keys=["number_input"], if_appear=False)
                self.driver.set_text(self.username, **self.app_config.get("number_input"))
                sleep(2)

                manual_btn = self.app_config.get("manual_btn")
                if manual_btn and self.driver.exists(**manual_btn):
                    self.driver.click(**manual_btn)
                    self.driver.set_text(self.username, **self.app_config.get("number_input"))
                    sleep(2)

                self.driver.click(x=0.50, y=0.15)
                sleep(2)

                self.driver.click(**self.app_config.get("pwd_input"))
                sleep(2)
                self._input_password(self.password)
                sleep(2)

                self.driver.click(**self.app_config.get("agree_btn"))
                sleep(2)
                self.driver.click(x=0.50, y=0.15)
                self.driver.click(**self.app_config.get("login_btn"))
                sleep(2)

            sleep(5)
            self._handle_common_popups()
            sleep(5)
            self._verify_login_result()
        except Exception as e:
            raise

    def _select_car(self):
        vin_suffix = str(self.vin_code).strip()[-6:]
        std_logger.info(self.tag + f"目标VIN后六位：{vin_suffix}")
        try:
            if self.app_type == "#01":
                self.driver.click(**self.app_config.get("car"))
                self.vpd.wait_for_element(config_keys=["car_select"], if_appear=False)
                car_select_cfg = self.app_config.get("car_select")
                if car_select_cfg:
                    self.driver.click(**car_select_cfg)
                sleep(2)

            elif self.app_type == "#08":
                car_select_cfg = self.app_config.get("car_select")
                if car_select_cfg:
                    self.driver.click(**car_select_cfg)
                sleep(2)
            std_logger.info(self.tag + "开始自动查找车辆")

            max_swipes = 15
            for i in range(max_swipes):
                if self._find_car(vin_suffix):
                    return
                self.driver.swipe(x1=0.50, y1=0.60, x2=0.50, y2=0.30)
                sleep(2)
            raise Exception(f"未找到车辆VIN后六位：{vin_suffix}")
        except Exception as e:
            std_logger.error(self.tag + f"选车失败：{str(e)}")
            raise

    def _get_login_status(self) -> bool:
        logout_text_map = {"#01": "注册/登录", "#08": "探索"}
        if self.app_type not in logout_text_map:
            return True
        assess_info = self.app_config.get("assess_info")
        if not assess_info or not self.driver.exists(**assess_info):
            return True
        try:
            current_text = self.driver.get_text(**assess_info).strip()
        except Exception:
            return True
        return not current_text or current_text == logout_text_map[self.app_type]

    def _input_password(self, pwd):
        try:
            pwd_cfg = self.app_config.get("pwd_input")
            if not pwd_cfg:
                raise Exception("[密码输入] 无pwd_input控件")
            # self.vpd.wait_for_element(config_keys=["pwd_input"], wait_time=10)
            self.driver.set_text(text=pwd, **pwd_cfg)
        except Exception as e:
            std_logger.error(f"[密码输入] 失败：{str(e)}")
            raise

    def _handle_common_popups(self):
        popup_keys = ["update_btn", "return_btn", "confirm","online_confirm", "next_btn"]
        try:
            for key in popup_keys:
                cfg = self.app_config.get(key)
                if cfg and self.driver.exists(**cfg):
                    self.driver.click(**cfg)
                    sleep(0.8)
        except Exception as e:
            std_logger.warning(f"[处理弹窗] 异常：{str(e)}")

    def _verify_login_result(self):
        """登录校验"""
        if self.app_type == "#08":
            self.driver.click(**self.app_config.get('discover'))
            sleep(1)
        if self._get_login_status():
            raise Exception("[登录检查] 登录失败")
        std_logger.info("[登录检查] 登录成功验证通过")

    def _find_car(self, vin_suffix: str):
        try:
            car_vin_cfg = self.app_config.get("car_vin")
            car_vin_xpath = car_vin_cfg.get("xpath")
            if "%s" in car_vin_xpath:
                target_xpath = car_vin_xpath % vin_suffix
                if self.driver.exists(xpath=target_xpath):
                    self.driver.click(xpath=target_xpath)
                    sleep(2)
                    return True
            else:
                elements = self.driver.xpath(car_vin_xpath).all()
                for ele in elements:
                    if vin_suffix in ele.text.strip():
                        ele.click()
                        sleep(2)
                        return True
        except Exception:
            return False
        return False

    def _account_logout(self):
        try:
            config_map = {
                "#01": {"entry": "my", "confirm": "logout_confirm"},
                "#08": {"entry": "user_center", "confirm": "confirm"}
            }
            if self.app_type not in config_map:
                return
            entry_key = config_map[self.app_type]["entry"]
            confirm_key = config_map[self.app_type]["confirm"]

            if not self.app_config.get(entry_key):
                return
            std_logger.info("[通用登出] 执行账户登出流程")
            self.driver.click(**self.app_config.get(entry_key))
            sleep(2)

            if self.app_type == "#01":
                self.driver.swipe(x1=0.50, y1=0.70, x2=0.50, y2=0.20)
                sleep(2)

            self.driver.click(**self.app_config.get("settings"))
            sleep(2)

            self.driver.click(**self.app_config.get("logout"))
            sleep(2)

            self.driver.click(**self.app_config.get(confirm_key))
            sleep(1)
            if self.app_type == "#01":
                self.driver.swipe(x1=0.50, y1=0.20, x2=0.50, y2=0.70)
                sleep(2)
            std_logger.info("[通用登出] 账户登出完成")
        except Exception as e:
            std_logger.error(f"[通用登出] 账户登出失败：{str(e)}")

    def run(self):
        try:
            self.vpd.app_start()
            self._login()
            self._select_car()
        except Exception as e:
            std_logger.error(f"{self.tag} 执行失败：{str(e)}, {traceback.format_exc()}")
        finally:
            self.vpd.app_stop()
