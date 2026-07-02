import os
import subprocess as sp
from datetime import datetime
from app.constant import WORK_DIR
from app.utils.port_util import port_util
import psutil


class ScrcpyUtil:
    """scrcpy util"""

    def __init__(self, serial: str):
        self._serial = serial
        self._start_time = None
        self._pid = None
        self._scrcpy_path = os.path.join(WORK_DIR, 'app', 'assets', 'scrcpy', 'scrcpy.exe')

    @classmethod
    def get_video_path(cls, output_path: str, file_name_key: str):
        """获得录屏路径"""
        file_name = f'{file_name_key}_{datetime.now().strftime("%Y%m%d%H%M%S")}.mp4'
        video_path = os.path.join(output_path, file_name)
        return video_path

    def record_start(self, output_path: str, device_name: str, task_name: str):
        """start record"""
        device_name = device_name.replace(' ', '')

        command = (f"{self._scrcpy_path} -s {self._serial} --no-control --window-title={device_name} --no-audio"
                   f" --video-codec=h264 --max-size=720 --max-fps=24 --video-bit-rate=500000 --record "
                   f"{self.get_video_path(output_path, task_name)}")
        record_proc = sp.Popen(command, creationflags=sp.CREATE_NEW_PROCESS_GROUP)

        try:
            process = psutil.Process(record_proc.pid)
            self._start_time = process.create_time()
            self._pid = process.pid
        except Exception as e:
            self.__init_process_info()

    def record_stop(self):
        """stop record"""
        if self.__check_process():
            sp.run(["taskkill", "/PID", str(self._pid)])
            self.__init_process_info()

    def __init_process_info(self):
        self._pid = None
        self._start_time = None

    def __check_process(self):
        ret = False

        if self._pid and self._start_time:
            try:
                process = psutil.Process(self._pid)
                ret = process.create_time() == self._start_time

            except Exception as e:
                ret = False

        return ret
