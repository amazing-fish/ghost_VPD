import numpy as np

from app.utils.logger import std_logger


def is_button_clickable_by_color(d, bounds, threshold: int = 50, if_show: bool = False):
    """
    通过颜色判断是否置灰
    """
    if not bounds:
        return False

    screenshot = d.screenshot()
    button_img = screenshot.crop(bounds)

    if if_show:
        button_img.show(title='debug')

    img_np = np.array(button_img.convert('RGB'))
    h, w, _ = img_np.shape
    center_block = img_np[int(h * 0.2):int(h * 0.8), int(w * 0.2):int(w * 0.8)]  # 取中心区域

    # 核心步骤：按 R, G, B 通道分别取中位数，剔除掉占少数的文字像素（白色或黑色）
    median_rgb = np.median(center_block, axis=(0, 1))
    r_val = median_rgb[0]
    std_logger.info(f"检测坐标: {bounds}, R,G,B 中位数: {median_rgb}")
    return r_val < threshold
