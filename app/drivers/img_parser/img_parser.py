import os

import cv2
import numpy as np

from app.utils.logger import std_logger


class ImgParser:

    def __init__(self, similarity: float = 0.7, if_show: bool = False):
        self._similarity = similarity
        self._screenshot = None
        self._target = None
        self._if_show = if_show
        self._np_start = 0.35

    def _cv_imread(self, screenshot_path: str, target_path: str):

        with open(screenshot_path, 'rb') as f:
            data = np.frombuffer(f.read(), dtype=np.uint8)
            screenshot = cv2.imdecode(data, cv2.IMREAD_COLOR)

        with open(target_path, 'rb') as f:
            data = np.frombuffer(f.read(), dtype=np.uint8)
            target = cv2.imdecode(data, cv2.IMREAD_COLOR)

        if screenshot is None or target is None:
            raise FileNotFoundError('[ImgParser][_cv_imread] file not found!')

        gray_screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        gray_target = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)

        self._cv_screenshot = screenshot
        self._cv_target = target

        self._screenshot = gray_screenshot
        self._target = gray_target

    def _match_targets(self, similarity: float = None):
        h_target, w_target = self._target.shape[:2]
        best_match, best_max_val = None, -1
        match_similarity = similarity if isinstance(similarity, float) else self._similarity

        # 定义缩放比例范围
        for scale in np.linspace(self._np_start, 2, 50):

            # 调整target尺寸
            resized_target = cv2.resize(self._target, (int(w_target * scale), int(h_target * scale)))
            h, w = resized_target.shape[:2]

            # 如果调整后target图片比模板大 跳过
            if h > self._screenshot.shape[0] or w > self._screenshot.shape[1]:
                continue

            # 模板匹配
            res = cv2.matchTemplate(self._screenshot, resized_target, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

            if max_val > best_max_val:
                best_max_val = max_val
                best_match = max_loc

            if best_max_val >= match_similarity:

                if self._if_show:
                    top_left = max_loc

                    bottom_right = (top_left[0] + w, top_left[1] + h)

                    cv2.rectangle(self._cv_screenshot, top_left, bottom_right, (0, 255, 0), 2)
                    cv2.imshow('result', self._cv_screenshot)
                    cv2.waitKey(0)
                    cv2.destroyAllWindows()

                    std_logger.info(f'[ImgParser][_match_targets] best_max_val: {best_max_val}')

                break

        if best_max_val <= match_similarity or best_match is None:
            raise RuntimeError('[ImgParser][_match_targets] target not has enough similarity!')

        return {'x': best_match[0], 'y': best_match[1]}

    def get_result(self, screenshot_path: str, target_path: str, np_start: float = 0.35, similarity: float = None):
        self._np_start = np_start if isinstance(np_start, float) else 0.35
        self._cv_imread(screenshot_path, target_path)

        return self._match_targets(similarity)
