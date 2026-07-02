import json
import os.path
import sys

from app.constant import WORK_DIR


class JsonReader:

    def __init__(self):
        json_path = os.getcwd() if hasattr(sys, 'frozen') and hasattr(sys, '_MEIPASS') else WORK_DIR
        self._json_path = os.path.join(json_path, 'config.json')
        self.json_data = []

        if not os.path.exists(self._json_path):
            with open(self._json_path, 'w', encoding='utf-8') as file:
                json.dump([], file, ensure_ascii=False, indent=4)

        self.read_json()

    def read_json(self):
        with open(self._json_path, 'r', encoding='utf-8') as file:
            self.json_data = json.load(file)

        return self.json_data


json_reader = JsonReader()
