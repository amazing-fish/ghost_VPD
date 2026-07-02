import os
import re
from datetime import datetime
from app.drivers.driver import Driver
from app.drivers.harmony.hm_driver_new import HMDriverNew
from app.utils.logger import std_logger

KEY_MAP = {
    'back': 2,
    'home': 1
}


class HarmonyDriver(Driver):

    def __init__(self, device_sn: str):
        super().__init__(device_sn)
        self._device = HMDriverNew(device_sn)

    def screenshot(self, output_path: str = None) -> str:
        if output_path is None:
            output_path = self._temp_path

        img_path = os.path.join(output_path, f"{datetime.now().strftime('%Y%m%d%H%M%S')}.png")

        self._device.screenshot(img_path)

        return img_path

    def screenrecord_start(self, output_path: str, **kwargs):
        file_name = f'{kwargs["task_name"]}_{datetime.now().strftime("%Y%m%d%H%M%S")}.mp4'
        video_path = os.path.join(output_path, file_name)

        self._device.screenrecord.start(video_path)

    def screenrecord_stop(self):
        self._device.screenrecord.stop()

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
                self._device.xpath(kwargs['xpath']).click()
            elif 'elements' in kwargs:
                self._device(**kwargs['elements']).click()
        except Exception as e:
            if_success = False

        return if_success

    def get_text(self, **kwargs) -> str:
        text = ''

        try:
            if 'xpath' in kwargs:
                text = self._device.xpath_get_attrib(kwargs['xpath']).get('text')
            elif 'elements' in kwargs:
                text = self._device.ui_select(**kwargs['elements']).text
        except Exception as e:
            pass

        return text

    def set_text(self, text: str, **kwargs):
        if 'xpath' in kwargs:
            xpath_select = self._device.xpath_select(kwargs['xpath'])
            xpath_select.input_text(text)
        elif 'elements' in kwargs:
            ui_select = self._device.ui_select(**kwargs['elements'])
            ui_select.input_text(text)
        else:
            self._device.input_text(text)

    def exists(self, **kwargs):
        ret = False

        try:

            if 'xpath' in kwargs:
                ret = self._device.xpath_exists(kwargs['xpath'])
            elif 'elements' in kwargs:
                ret = self._device.ui_select(**kwargs['elements']).exists()
            elif 'img_path' in kwargs and 'screenshot_path' in kwargs:
                img_path, screenshot_path, np_start, similarity = (
                    map(kwargs.get, ['img_path', 'screenshot_path', 'np_start', 'similarity']))
                ret = self._img_parser.get_result(self.screenshot(screenshot_path), img_path, np_start, similarity)

        except Exception as e:

            std_logger.info(f'[HarmonyDriver][exists] kwargs: {kwargs}, e: {e}')

        if_exists = True if ret else False

        return if_exists

    def app_start(self, package_name: str):
        self._device.start_app(package_name, 'EntryAbility')

    def app_stop(self, package_name: str):
        self._device.stop_app(package_name)

    def get_app_version(self, package_name: str) -> str:
        app_version = '--'

        try:
            version_string = self._device.shell(f'bm dump -n {package_name} | find "versionName"').output
            match = re.search(r'"versionName": "(\d+\.\d+\.\d+\.\d+)"', version_string)
            if match:
                app_version = match.group(1)
        except Exception:
            pass
        return app_version

    def get_current_app(self):
        return self._device.current_app()[0]

    def dump_hierarchy(self):
        return self._device.dump_hierarchy()

    def press(self, key: [int, str]):
        self._device.press_key(KEY_MAP.get(key, key))

    def swipe(self, **kwargs):
        x1, y1, x2, y2, speed = map(kwargs.get, ['x1', 'y1', 'x2', 'y2', 'speed'])
        if not speed or not isinstance(speed, int):
            speed = 2000
        self._device.swipe(x1, y1, x2, y2, speed)

    def release_driver(self):
        self._device.clear_driver()
