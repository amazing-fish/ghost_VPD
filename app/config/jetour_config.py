JETOUR_ANDROID_CONFIG = {
    'package': {
        'package_name': 'com.jetour.zongheng'
    },
    'app_loading': {
        'xpath': '//android.widget.FrameLayout[1]'
    },
    'car': {
        'xpath': '//*[contains(@content-desc, "爱车")]'
    },
    'vpd': {
        'xpath': '//*[@content-desc="泊车代驾辅助"]'
    },
    'vpd_loading': {
        'xpath': '//*[@text="正在加载..."]'
    },
    'vpd_success': {
        'xpath': '//*[@text="默认车位"]'
    },
    'parking_space_input': {
        'xpath': '//*[@resource-id="com.jetour.zongheng:id/tv_keyword"]',
        'text': '输入车位号设为目的地'
    },
    'parking_space_select': {
        'xpath': '//*[@resource-id="com.jetour.zongheng:id/tv_button"]'
    },
    'start_loading': {
        'elements': {'text': '路线规划中，请稍后…'}
    },
    'parking': {
        'elements': {'text': '泊入车位'}
    },
    'summon': {
        'elements': {'text': '靠边停车'}
    },
    'main_title': {
        'xpath': "//*[contains(@text, '{}')]"
    },
    'sub_title': {
        'xpath': "//*[contains(@text, '{}')]/following-sibling::*"
    },
    'retry': {
        'elements': {'text': '重试'},
    },
    'start': {
        'xpath': '//*[@text="开始"]'
    },
    'start_enabled': {
        'elements': {'resourceId': 'com.jetour.zongheng:id/btn_start', 'text': '开始'},
        'judge_enable': True
    },
    'start_btn': {
        'xpath': '//*[@resource-id="com.jetour.zongheng:id/cl_park_btn_start"]'
    },
    'confirm': {
        'xpath': '//*[@text="确定"]'
    },
    'pause': {
        'xpath': '//*[@text="暂停"]'
    },
    'quit': {
        'xpath': '//*[@text="退出"]'
    },
    'continue': {
        'xpath': '//*[@text="继续"]'
    },
    'finish': {
        'xpath': '//*[@text="返回"]'
    },
    'parking_finish': {
        'elements': {'resourceId': 'com.jetour.zongheng:id/cl_finish_tv_title', 'text': '车已泊入车位'}

    },
    'summon_finish': {
        'elements': {'resourceId': 'com.jetour.zongheng:id/cl_finish_tv_title','text': '车已到达上车点'}
    },
    'driver_distance': {
        'xpath': '//*[@text="行驶距离"]/../Row[1]/*[@text="米"]/preceding-sibling::*[1]'
    },
    'min_total_cost': {
        'xpath': '//*[@text="总用时"]/../Row[1]/*[@text="分"]/preceding-sibling::*[1]'
    },
    'sec_total_cost': {
        'xpath': '//*[@text="总用时"]/../Row[1]/*[@text="秒"]/preceding-sibling::*[1]'
    },
    'min_parking_cost': {
        'xpath': '//*[@text="泊车用时"]/../Row[1]/*[@text="分"]/preceding-sibling::*[1]'
    },
    'sec_parking_cost': {
        'xpath': '//*[@text="泊车用时"]/../Row[1]/*[@text="秒"]/preceding-sibling::*[1]'
    }
}
JETOUR_CONFIG_MAP = {
    'Android': JETOUR_ANDROID_CONFIG
}