import traceback
from pathlib import Path

from app.utils.adb_util import adb_get_devices
from app.utils.hdc_util import hdc_get_devices
from app.utils.json_reader import json_reader
from app.utils.logger import std_logger


class ConfigUtil:

    def __init__(self):
        self._json_reader = json_reader
        self._android_devices = []
        self._harmony_devices = []
        self.devices = []
        self.config = {}
        self.__set_config()

    def __set_config(self):
        self.__set_current_device()

        config_map = self._json_reader.read_json()
        config, devices, account_info = map(config_map.get, ['config', 'devices', 'account_info'])

        self.config = config
        self.devices = devices
        self.account_info = account_info

    def __set_current_device(self):
        self._android_devices = adb_get_devices()
        self._harmony_devices = hdc_get_devices()

        self._all_devices = [x[0] for x in self._android_devices + self._harmony_devices
                             if len(x) > 1 and x[1] == 'device']

    def __get_device_status(self, device_sn: str, device_type: str):
        device_status = '离线'

        if device_type == 'Android':
            for android_device_infos in self._android_devices:
                conditions = [
                    len(android_device_infos) > 1,
                    android_device_infos[0] == device_sn,
                    android_device_infos[1] == 'device'
                ]

                if all(conditions):
                    device_status = '在线'

        elif device_type == 'Harmony':
            if device_sn in self._harmony_devices:
                device_status = '在线'

        return device_status

    def get_devices_info(self):
        self.__set_current_device()
        return self._all_devices

    def build_dir_tree(self, dir_path):
        path = Path(dir_path)

        sub_dirs = sorted([p for p in path.iterdir() if p.is_dir()])

        # 叶子目录
        if not sub_dirs:
            return sorted([
                p.name
                for p in path.iterdir()
                if p.is_file()
            ])

        # 非叶子目录
        return {
            subdir.name: self.build_dir_tree(subdir)
            for subdir in sub_dirs
        }

    def get_package_info(self):
        package_info = {}

        try:
            app_dir = self.config.get('app_dir')
            package_info = self.build_dir_tree(app_dir)

            result = {}
            for app_type, item in package_info.items():
                for device_type, packages in item.items():
                    result.setdefault(device_type, {}).setdefault(app_type, []).extend(packages)

            package_info = result
        except Exception as e:
            std_logger.error(f'[get_package_info] error={traceback.format_exc()}')

        return package_info

config_util = ConfigUtil()
