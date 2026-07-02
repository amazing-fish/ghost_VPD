from datetime import datetime
from time import sleep

from app.constant import VPD_CALCULATED_WAIT_TIME
from app.models.mysql.tables import VpdTestTask
from app.service.vpd.vpd import VPD
from app.utils.adb_util import adb_get_device_files_by_date, adb_input_tap
from app.utils.exception_util import ParkingSpaceException, AppLaunchException


class VPDAndroid(VPD):

    def __init__(self, vpd_task: VpdTestTask, skip_init_driver: bool = False):
        super().__init__(vpd_task, skip_init_driver)

    def input_parking_space(self, parking_space: str, if_wait: bool = True):

        if self._app_type == '#01' and self.driver.exists(**self.app_config.get('parking_space_input_new')):
            self.driver.click(**self.app_config.get('parking_space_input_new'))
        else:
            self.driver.click(**self.app_config.get('parking_space_input'))

        sleep(2)
        self.driver.set_text(parking_space)
        sleep(3)

        xpath = self.app_config['parking_space_select']['xpath'].format(parking_space)
        if not self.driver.exists(xpath=xpath):
            raise ParkingSpaceException()

        go_xpath_exist, start_time = True, None
        for _ in range(3):
            start_time = datetime.now()
            self.driver.click(xpath=xpath)
            sleep(1)
            if not self.driver.exists(xpath=xpath):
                go_xpath_exist = False
                break
        if go_xpath_exist:
            raise Exception('点击去目标车位失败')

        if self._app_type == '#05':
            sleep(5)
            self.process_identify_card()
            self.set_safe_code()
            self.process_identify_card()

        element_time = None
        if if_wait:
            element_time = self.wait_for_element(['start_loading'], VPD_CALCULATED_WAIT_TIME)

        return start_time, element_time

    def pull_app_log(self):

        if isinstance(self._app_log_path, list):
            for log_path in self._app_log_path:
                adb_get_device_files_by_date(self._device_sn, log_path, self._log_path)

        elif isinstance(self._app_log_path, str):
            adb_get_device_files_by_date(self._device_sn, self._app_log_path, self._log_path)

    def check_current_app_v1(self):
        # 暂不使用
        current_app = self.driver.get_current_app()

        if current_app != self.app_config.get('package', {}).get('package_name'):
            raise AppLaunchException()

    def unlock_screen(self, password: str = '123456'):
        current_package_name = self.driver.info().get('currentPackageName')
        if current_package_name and current_package_name != 'com.android.systemui':
            return

        self.driver.screen_on()
        sleep(0.1)

        self.driver.swipe_ext(direction='up', scale=0.8)
        sleep(0.5)

        key_map = {
            '0': 7, '1': 8, '2': 9, '3': 10, '4': 11,
            '5': 12, '6': 13, '7': 14, '8': 15, '9': 16
        }
        for char in password:
            if char in key_map:
                self.driver.press(key_map[char])
                sleep(0.1)

    def tap_by_scale(self, x_scale: float, y_scale: float):
        device_info = self.driver.info()
        width = device_info.get('displayWidth')
        height = device_info.get('displayHeight')
        adb_input_tap(self._device_sn, x_scale * width, y_scale * height)

    def get_screen_size(self):
        device_info = self.driver.info()
        width = device_info.get('displayWidth')
        height = device_info.get('displayHeight')
        return width, height

    def get_ele_info(self, xpath: str):
        if self.driver.exists(xpath=xpath):
            return self.driver.xpath(xpath).get()
        return None