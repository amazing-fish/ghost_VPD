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
