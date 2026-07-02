CHERY_ANDROID_CONFIG = {
    'package': {
        'package_name': 'com.chery.tha.chery'
    },
    'app_loading': {
        'xpath': '//android.widget.FrameLayout[2]'
    },
    'car': {
         'xpath': '//*[contains(@content-desc, "爱车")]'
    },
    'vpd': {
        'xpath': '//*[@content-desc="VPD"]'
    },
    'vpd_safe_code': {
        'xpath': '//*[@content-desc="Del"]'
    },
    'vpd_safe_code_input': {
        'xpath': '//*[@content-desc="{}"]'
    },
    'vpd_loading': {
        'xpath': '//*[@resource-id="com.chery.tha.chery:id/hws_loading"]'
    },
    'vpd_success': {
        'xpath': '//*[@text="Default parking"]'
    },
    'parking_space_input': {
        'xpath': '//*[@resource-id="com.chery.tha.chery:id/tv_keyword"]'
    },
    'parking_space_select': {
        'xpath': '//*[@resource-id="com.chery.tha.chery:id/tv_button"]'
    },
    'start_loading': {
        'elements': {'text': 'Planning route…'}
    },
    'parking': {
        'xpath': '//*[@text="Park in spot"]'
    },
    'summon': {
        'xpath': '//*[@text="Pull over"]'
    },
    'main_title': {
        'xpath': "//*[contains(@text, '{}')]"
    },
    'sub_title': {
        'xpath': "//*[contains(@text, '{}')]/following-sibling::*"
    },
    'start': {
        'xpath': '//*[@resource-id="com.chery.tha.chery:id/base_btn_text"]'
    },
    'start_enabled': {
        'elements': {'resourceId': 'com.chery.tha.chery:id/cl_park_btn_start'},
        'enable': True
    },
    'start_btn': {
        'xpath': '//*[@resource-id="com.chery.tha.chery:id/cl_park_btn_start"]'
    },
    'confirm': {
        'xpath': '//*[@resource-id="com.chery.tha.chery:id/tv_confirm"]'
    },
    'pause': {
        'xpath': '//*[@resource-id="com.chery.tha.chery:id/base_btn_text"]'
    },
    'quit': {
        'xpath': '//*[@resource-id="com.chery.tha.chery:id/base_btn_text"]'
    },
    'continue': {
        'xpath': '//*[@resource-id="com.chery.tha.chery:id/base_btn_text"]'
    },
    'finish': {
        'xpath': '//*[@resource-id="com.chery.tha.chery:id/fl_back"]'
    },
    'parking_finish': {
        'elements': {'resourceId': 'com.chery.tha.chery:id/cl_finish_tv_title', 'text': 'Parking complete'}

    },
    'summon_finish': {
        'elements': {'resourceId': 'com.chery.tha.chery:id/cl_finish_tv_title','text': 'Arrived at pick-up point'}
    }
}

CHERY_CONFIG_MAP = {
    'Android': CHERY_ANDROID_CONFIG
}
