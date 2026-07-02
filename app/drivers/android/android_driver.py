import os
from datetime import datetime
from app.drivers.driver import Driver
import uiautomator2

from app.utils.img_util import is_button_clickable_by_color
from app.utils.scrcpy_util import ScrcpyUtil


class AndroidDriver(Driver):

    def __init__(self, device_sn: str):
        super().__init__(device_sn)
        self._device = uiautomator2.connect_usb(device_sn)
        self._device.set_fastinput_ime()
        self._scrcpy = ScrcpyUtil(device_sn)
        self._device.implicitly_wait(5)

    def screenshot(self, output_path: str = None) -> str:
        if output_path is None:
            output_path = self._temp_path

        img_path = os.path.join(output_path, f"{datetime.now().strftime('%Y%m%d%H%M%S')}.png")
        self._device.screenshot(img_path)

        return img_path

    def screenrecord_start(self, output_path: str, **kwargs):
        self._scrcpy.record_start(output_path, kwargs['device_name'], kwargs['task_name'])

    def screenrecord_stop(self):
        self._scrcpy.record_stop()

    def click(self, **kwargs):
        if_success = True

        try:
            if 'img_path' in kwargs and 'screenshot_path' in kwargs:

                img_path, screenshot_path, np_start, similarity = (
                    map(kwargs.get, ['img_path', 'screenshot_path', 'np_start', 'similarity']))

                self._device.click(**self._img_parser.get_result(self.screenshot(screenshot_path),
                                                                 img_path, np_start, similarity))
            elif 'x' and 'y' in kwargs:
                self._device.click(x=kwargs['x'], y=kwargs['y'])
            elif 'xpath' in kwargs:
                self._device.xpath.click(kwargs['xpath'])
            elif 'elements' in kwargs:
                self._device(**kwargs['elements']).click()
        except Exception as e:
            if_success = False

        return if_success

    def long_click(self, **kwargs):
        if_success = True
        try:
            if 'x' and 'y' in kwargs:
                self._device.long_click(x=kwargs['x'], y=kwargs['y'])
            elif 'xpath' in kwargs:
                self._device.xpath(kwargs['xpath']).long_click()
            elif 'elements' in kwargs:
                self._device(**kwargs['elements']).long_click()
        except Exception as e:
            if_success = False

        return if_success

    def get_text(self, **kwargs) -> str:
        text = ''

        try:
            if 'xpath' in kwargs:
                text = self._device.xpath(kwargs['xpath']).get_text()
            elif 'elements' in kwargs:
                text = self._device(**kwargs['elements']).get_text()
        except Exception as e:
            pass

        return text

    def set_text(self, text: str, **kwargs):
        if 'xpath' in kwargs:
            self._device.xpath(kwargs['xpath']).set_text(text)
        elif 'elements' in kwargs:
            self._device(**kwargs['elements']).set_text(text)
        else:
            self._device.send_keys(text)

    def exists(self, **kwargs):

        if 'xpath' in kwargs:
            ret = self._device.xpath(kwargs['xpath']).exists
        elif 'elements' in kwargs:

            try:
                ret = self._device(**kwargs['elements']).exists
                if ret and 'judge_enable' in kwargs and kwargs['judge_enable']:
                    el_info = self._device(**kwargs['elements']).info
                    bounds = el_info.get('bounds', {})
                    bounds_tuple = (bounds['left'], bounds['top'], bounds['right'], bounds['bottom'])
                    ret = is_button_clickable_by_color(self._device, bounds_tuple)
                    print(f'bounds: {bounds}, judge_enable: {ret}')
            except Exception:
                ret = False

        elif 'img_path' in kwargs and 'screenshot_path' in kwargs:

            try:
                img_path, screenshot_path, np_start, similarity = (
                    map(kwargs.get, ['img_path', 'screenshot_path', 'np_start', 'similarity']))
                ret = self._img_parser.get_result(self.screenshot(screenshot_path), img_path, np_start, similarity)
            except Exception:
                ret = False

        else:
            raise ValueError(f'Unrecognized arguments: {kwargs}')

        if_exists = True if ret else False

        return if_exists

    def app_start(self, package_name: str):
        self._device.app_start(package_name)

    def app_stop(self, package_name: str):
        self._device.app_stop(package_name)

    def get_app_version(self, package_name: str) -> str:
        return self._device.app_info(package_name).get('versionName')

    def get_current_app(self):
        return self._device.info.get('currentPackageName')

    def dump_hierarchy(self):
        self._device.dump_hierarchy()

    def press(self, key: str):
        self._device.press(key)

    def swipe(self, **kwargs):
        x1, y1, x2, y2, duration, steps = map(kwargs.get, ['x1', 'y1', 'x2', 'y2', 'duration', 'steps'])
        self._device.swipe(x1, y1, x2, y2, duration, steps)

    def xpath(self, key: str):
        return self._device.xpath(key)

    def app_list(self):
        return self._device.app_list()

    def info(self):
        return self._device.info

    def screen_on(self):
        self._device.screen_on()

    def swipe_ext(self, **kwargs):
        self._device.swipe_ext(**kwargs)
