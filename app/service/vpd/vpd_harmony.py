import os
from datetime import datetime
from time import sleep

from app.constant import VPD_CALCULATED_WAIT_TIME, WORK_DIR
from app.models.mysql.tables import VpdTestTask
from app.service.vpd.vpd import VPD
from app.utils.exception_util import ParkingSpaceException
from app.utils.hdc_util import hdc_wakeup, hdc_get_screen_state


class VPDHarmony(VPD):

    def __init__(self, vpd_task: VpdTestTask, skip_init_driver: bool = False):
        super().__init__(vpd_task, skip_init_driver)

    def input_parking_space(self, parking_space: str, if_wait: bool = True):

        self.click_parking_input()

        xpath = self.app_config['parking_space_select']['xpath'].format(parking_space)

        for _ in range(3):
            self.driver.set_text(parking_space, **self.app_config.get('parking_space_input_text'))
            sleep(2)

            if self.driver.exists(xpath=xpath):
                break
            else:
                self.driver.press('back')
                sleep(2)
                self.driver.press('back')
                sleep(2)
                self.click_parking_input()

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

        element_time = None
        if if_wait:
            element_time = self.wait_for_element(['start_loading'], VPD_CALCULATED_WAIT_TIME)

        return start_time, element_time

    def click_parking_input(self):
        self.driver.click(**self.app_config.get('parking_space_input'))
        sleep(2.5)

        if self._app_type == '#07':
            self.driver.click(img_path=os.path.join(WORK_DIR, 'app', 'assets', 'imgs', 'hm', 'parking_input.png'),
                              screenshot_path=self._img_path)

    def unlock_screen(self, password: str = '123456'):
        if hdc_get_screen_state(self._device_sn) != 'SLEEP':
            return

        hdc_wakeup(self._device_sn)
        self.driver.swipe(x1=0.5, y1=0.7, x2=0.5, y2=0.3)
        sleep(0.1)
        self.driver.set_text(password)
        sleep(0.1)

    def get_screen_size(self):
        width, height = self.driver._device.device_info.displaySize
        return width, height

    def get_ele_info(self, xpath: str):
        if self.driver.exists(xpath=xpath):
            return self.driver._device.xpath_select(xpath=xpath)
        return None