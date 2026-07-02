import os
from abc import ABC, abstractmethod

from app.constant import WORK_DIR
from app.drivers.img_parser.img_parser import ImgParser


class Driver(ABC):

    def __init__(self, device_sn: str, similarity: float = 0.9):
        self._img_parser = ImgParser(similarity)  # 图像
        self._temp_path = os.path.join(WORK_DIR, 'temp')

        if not os.path.exists(self._temp_path):
            os.makedirs(self._temp_path)

        self.device_sn = device_sn

    @abstractmethod
    def screenshot(self, output_path: str = None) -> str:
        pass

    @abstractmethod
    def screenrecord_start(self, output_path: str, **kwargs) -> str:
        pass

    @abstractmethod
    def screenrecord_stop(self):
        pass

    @abstractmethod
    def click(self, **kwargs):
        pass

    def long_click(self, **kwargs):
        pass

    @abstractmethod
    def get_text(self, **kwargs):
        pass

    @abstractmethod
    def set_text(self, text: str, **kwargs):
        pass

    @abstractmethod
    def exists(self, **kwargs):
        pass

    @abstractmethod
    def app_start(self, **kwargs):
        pass

    @abstractmethod
    def app_stop(self, **kwargs):
        pass

    @abstractmethod
    def get_app_version(self, **kwargs) -> str:
        pass

    @abstractmethod
    def get_current_app(self) -> str:
        pass

    @abstractmethod
    def dump_hierarchy(self) -> dict:
        pass

    @abstractmethod
    def press(self, key: [str, int]):
        pass

    @abstractmethod
    def swipe(self, **kwargs):
        pass

    def xpath(self, key: str):
        pass

    def app_list(self):
        pass

    def release_driver(self):
        pass

    def info(self):
        pass

    def screen_on(self):
        pass

    def swipe_ext(self, **kwargs):
        pass