MHERO_ANDROID_CONFIG = {
    'package': {
        'package_name': 'com.yinwang.qiankunapp'
    },
    'car': {
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/content', 'text': '爱车'}
    },
    'vpd': {
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/content', 'text': 'ADS'}
    },
    'discover': {
        'xpath': '//*[@resource-id="com.yinwang.qiankunapp:id/navigation"]/android.widget.LinearLayout[4]'
    },
    'vpd_safe_code': {
        'elements': {'text': '请输入安全码'}
    },
    'vpd_safe_code_input': {
        'elements': {'className': 'android.widget.EditText', 'text': ''}
    },
    'vpd_loading': {
        'elements': {'text': '正在加载...'}
    },
    'vpd_success': {
        'elements': {'text': '默认车位'}
    },
    'parking_space_input': {
        'xpath': '//*[@text="输入车位号设为目的地"]'
    },
    'parking_space_select': {
        'xpath': "//*[contains(@text, '{}')]/../..//*[contains(@text, '去这里')]"
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
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/base_btn_text', 'text': '开始'},
        'judge_enable': True
    },
    'start_btn': {
        'xpath': '//*[@resource-id="com.yinwang.qiankunapp:id/cl_park_btn_start"]'
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
    'speed': {
        'xpath': '//*[contains(@text, "km/h")]'
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
    },
    'sentry_mode': {
        'xpath': '//*[@text="哨兵模式增强"]/../parent::*/following-sibling::*[2][@text="查看"]'
    },
    'sentry_mode_status': {
        'xpath': '//*[@text="守卫中"]'
    },
    'open_sentry_mode': {
        'xpath': '//*[@text="立即开启"]'
    },
    'confirm_sentry_mode': {
        'xpath': '//*[@text="确定"]'
    },
    'loading_sentry_mode': {
        'xpath': '//*[@text="开启中..."]'
    },
    'close_sentry_mode': {
        'xpath': '//*[@text="立即关闭"]'
    },
    'charge_error': {
        'xpath': "//*[@text='充电异常']"
    },
    'charge_error_detail': {
        'xpath': "//*[@text='充电异常']/following-sibling::*"
    },
    'charging_check': {
        'elements': {'text': '车辆充电中'}
    },
    'charging_in': {
        'elements': {'text': '查看'}
    },
    'charge_start': {
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/base_btn_text', 'text': '开始'},
        'judge_enable': True
    },
    'charge_station': {
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/charging_station_card_title', 'text': '充电站'}
    },
    'to_charge': {
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/btn_to_charge', 'text': '去充电'}
    },
    'book_charge': {
        'xpath': '//*[@resource-id="com.yinwang.qiankunapp:id/cl_park_btn_start"]'
    },
    'charge_prepare': {
        'elements': {'text': '充电准备'}
    },
    'charging': {
        'elements': {'text': '充电中'}
    },
    'end_charge': {
        'elements': {'text': '结束充电'},
        'judge_enable': True
    },
    'confirm_end_charge': {
        'elements': {'text': '结束充电'}
    },
    'summon_before_end_charge': {
        'elements': {'text': '召唤'},
        'judge_enable': True
    },
    'parking_finish': {
        'elements': {'text': '车已泊入车位'}
    },
    'summon_finish': {
        'elements': {'text': '车已到达上车点'}
    },
    'cruise_tip': {
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/cl_park_tv_title'}
    },
    'parking_lot': {
        'xpath': '//*[@resource-id="com.yinwang.qiankunapp:id/vpd_title"]//android.widget.TextView[1]'
    },
    'floor': {
        'xpath': '//*[@resource-id="com.yinwang.qiankunapp:id/avp_floor_prev_img"]/following-sibling::*[1]'
    },
    'total_spots': {
        'xpath': '//*[@resource-id="com.yinwang.qiankunapp:id/tv_total_parking_spots"]'
    },
    'default_parking_space': {
        'xpath': '//*[@text="默认车位"]/preceding-sibling::*[1]'
    },
    'default_parking_space_no_set': {
        'xpath': '//*[@text="默认车位"]/following-sibling::*[1]'
    },
    'default_boarding_spot': {
        'xpath': '//*[@text="默认上车点"]/preceding-sibling::*[1]'
    },
    'default_boarding_spot_no_set': {
        'xpath': '//*[@text="默认上车点"]/following-sibling::*[1]'
    },
    'right_icon_btn': {
        'xpath': '//*[@resource-id="com.yinwang.qiankunapp:id/float_btn_container"]'
    },
    'favorite_flag': {
        'xpath': '//*[@resource-id="com.yinwang.qiankunapp:id/frequent_place_card"]//*[@text="去这里"]'
    },
    'favorite_empty_flag': {
        'xpath': '//*[@text="可在地图中点选并收藏车位和停车点"]'
    },
    'full_view_btn': {
        'xpath': '//*[@text="全览"]'
    },
    'ego_view_btn': {
        'xpath': '//*[@text="回自车"]'
    },
    'assess_info': {
        'xpath': '//*[@resource-id="android:id/text1"]'
    },
    'assess_icon': {
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/mine_portrait'}
    },
    'login_icon':{
        'xpath':'//*[@resource-id="com.yinwang.qiankunapp:id/user_nickname"]',
        'text':'登录/注册'
    },
    'return_btn':{
        'xpath':'//*[@resource-id="com.yinwang.qiankunapp:id/toolbar"]/android.widget.ImageButton[1]'
    },
    'pwd_login':{
        'xpath':'//*[@resource-id="com.yinwang.qiankunapp:id/tv_pwd"]',
        'text':'密码登录'
    },
    'number_input':{
        'xpath':'//*[@resource-id="com.yinwang.qiankunapp:id/id_main_login_input_phone"]'
    },
    'pwd_input':{
        'xpath':'//*[@resource-id="com.yinwang.qiankunapp:id/layout_pwd"]'
    },
    'agree_btn':{
        'xpath':'//*[@resource-id="com.yinwang.qiankunapp:id/ll_cbx_agree"]',
        'text':'我已阅读并同意'
    },
    'login_btn':{
        'xpath':'//*[@resource-id="com.yinwang.qiankunapp:id/tv_login"]',
        'text':'登录'
    },
    'update_btn':{
        'xpath':'//*[@resource-id="android:id/button1"]',
        'text':'更新'
    },
    'car_select':{
        'xpath':'//*[@resource-id="com.yinwang.qiankunapp:id/tv_expand"]'
    },
    'car_vin': {
        'xpath': '//*[@resource-id="com.yinwang.qiankunapp:id/tv_vin"]'
    },
    'user_center':{
        'xpath':'//*[@resource-id="com.yinwang.qiankunapp:id/mine_portrait"]'
    },
    'settings':{
        'xpath':'//*[@resource-id="com.yinwang.qiankunapp:id/settings"]'
    },
    'logout':{
        'xpath':'//*[@resource-id="com.yinwang.qiankunapp:id/tv_log_out"]',
        'text':'退出登录'
    },
    'park_location': {
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/cl_select_tv_name'}
    },
    'view_name': {
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/direct_textview'}
    },
}

MHERO_HARMONY_CONFIG = {
    'package': {
        'package_name': 'com.yinwang.qiankunapp.hm'
    },
    'car': {
        'elements': {'text': '爱车'}
    },
    'vpd': {
        'elements': {'text': 'ADS'}
    },
    'discover': {
        'elements': {'text': '探索'}
    },
    'vpd_safe_code': {
        'elements': {'text': '请输入安全码'}
    },
    'vpd_safe_code_input': {
        'xpath': '//*[@id="SafeCodeInputVM"]'
    },
    'vpd_loading': {
        'elements': {'text': '正在加载...'}
    },
    'vpd_success': {
        'elements': {'text': '默认车位'}
    },
    'parking_space_input': {
        'xpath': '//*[@text="输入车位号设为目的地"]'
    },
    'parking_space_input_text': {
        'xpath': '//*[@id="search_input"]'
    },
    'parking_space_select': {
        'xpath': "//*[contains(@text, '{}')]/../..//*[contains(@text, '去这里')]"
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
        'xpath': '//*[@id="VPD_HOME_VIEW"]/RelativeContainer[1]/Column[1]/__Common__[2]/Stack[1]/Column[1]/Column[1]'
                 '/Column[1]/GridRow[1]/GridCol[2]/Button[@opacity="1.000000"]'
    },
    'start_btn': {
        'xpath': '//*[@id="VPD_HOME_VIEW"]/RelativeContainer[1]/Column[1]/__Common__[2]/Stack[1]/Column[1]/Column[1]'
                 '/Column[1]/GridRow[1]/GridCol[2]/Button[1]'
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
    'speed': {
        'xpath': '//*[contains(@text, "km/h")]'
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
    },
    'sentry_mode': {
        'xpath': '//*[@text="哨兵模式增强"]/../Text[@text="查看"]'
    },
    'sentry_mode_status': {
        'xpath': '//*[@text="守卫中"]'
    },
    'open_sentry_mode': {
        'xpath': '//*[@text="立即开启"]'
    },
    'confirm_sentry_mode': {
        'xpath': '//*[@text="确定"]'
    },
    'loading_sentry_mode': {
        'xpath': '//*[@text="开启中..."]'
    },
    'close_sentry_mode': {
        'xpath': '//*[@text="立即关闭"]'
    },
    'charge_error': {
        'xpath': "//*[@text='充电异常']"
    },
    'charge_error_detail': {
        'xpath': "//*[@text='充电异常']/following-sibling::*"
    },
    'charging_check': {
        'elements': {'text': '车辆充电中'}
    },
    'charging_in': {
        'elements': {'text': '查看'}
    },
    'charge_start': {
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/base_btn_text', 'text': '开始'},
        'judge_enable': True
    },
    'charge_station': {
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/charging_station_card_title', 'text': '充电站'}
    },
    'to_charge': {
        'elements': {'resourceId': 'com.yinwang.qiankunapp:id/btn_to_charge', 'text': '去充电'}
    },
    'book_charge': {
        'xpath': '//*[@resource-id="com.yinwang.qiankunapp:id/cl_park_btn_start"]'
    },
    'charge_prepare': {
        'elements': {'text': '充电准备'}
    },
    'charging': {
        'elements': {'text': '充电中'}
    },
    'end_charge': {
        'elements': {'text': '结束充电'},
        'judge_enable': True
    },
    'confirm_end_charge': {
        'elements': {'text': '结束充电'}
    },
    'summon_before_end_charge': {
        'elements': {'text': '召唤'},
        'judge_enable': True
    },
    'parking_finish': {
        'elements': {'text': '车已泊入车位'}
    },
    'summon_finish': {
        'elements': {'text': '车已到达上车点'}
    },
    'cruise_tip': {
        'xpath': '//*[@id="VPD_HOME_VIEW"]/RelativeContainer[1]/Column[1]/__Common__[2]/Stack[1]/Column[1]'
                 '/Column[1]/Column[1]/GridRow[1]/GridCol[1]/Row[1]/Column[1]/Text[1]'
    },
    'parking_lot': {
        'xpath': '//*[@id="VPD_HOME_VIEW"]/RelativeContainer[1]/Column[1]/__Common__[2]/Stack[1]/Column[1]'
                 '/Column[1]/Column[1]/Column[1]/Column[1]/Row[1]/Text[1]'
    },
    'floor': {
        'xpath': '//*[@id="component_floor_id"]/Scroll[1]/Column[1]//Row/Image[1]/following-sibling::*[1]'
    },
    'total_spots': {
        'xpath': '//*[@id="VPD_HOME_VIEW"]/RelativeContainer[1]/Column[1]/__Common__[2]/Stack[1]/Column[1]'
                 '/Column[1]/Column[1]/Column[1]/Column[1]/Row[1]/Row[1]/Text[1]'
    },
    'default_parking_space': {
        'xpath': '//*[@text="默认车位"]/preceding-sibling::*[1]'
    },
    'default_parking_space_no_set': {
        'xpath': '//*[@text="默认车位"]/following-sibling::*[1]'
    },
    'default_boarding_spot': {
        'xpath': '//*[@text="默认上车点"]/preceding-sibling::*[1]'
    },
    'default_boarding_spot_no_set': {
        'xpath': '//*[@text="默认上车点"]/following-sibling::*[1]'
    },
    'favorite_flag': {
        'xpath': '//*[@text="常去地点"]/../following-sibling::Column[1]//*[@text="去这里"]'
    },
    'full_view_btn': {
        'xpath': '//*[@text="全览"]'
    },
    'ego_view_btn': {
        'xpath': '//*[@text="回自车"]'
    },
    'assess_info': {
        'xpath': '//root[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/'
                 'NavDestinationContent[1]/Navigation[1]/NavBar[1]/NavBarContent[1]/'
                 'Tabs[1]/Swiper[1]/TabContent[1]/Stack[1]/Row[1]/Row[1]//*[@text]'
    },
    'assess_icon': {
        'xpath': '//root[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/'
                 'NavDestinationContent[1]/Navigation[1]/NavBar[1]/NavBarContent[1]/'
                 'Tabs[1]/Swiper[1]/TabContent[1]/Stack[1]/Row[1]/__Common__[1]'
    },
    'login_icon':{
        'xpath':'//root[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/'
                'Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/Column[1]/'
                'Scroll[1]/Column[1]/Column[1]/Image[1]'
    },
    'return_btn':{
        'xpath':'//root[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/'
                'Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/Column[1]/'
                'Row[1]/Row[1]/Image[1]'
    },
    'pwd_login':{
        'xpath':'//*[@text="密码登录"]'
    },
    'number_input':{
        'xpath':'//root[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/'
                'Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/Column[1]/'
                'Row[1]/Column[3]/TextInput[1]'
    },
    'manual_btn': {
        'xpath': '//*[@text="手动输入"]'
    },
    'pwd_input':{
        'xpath':'//root[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/'
                'Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/Column[1]/'
                'Column[2]/TextInput[1]'
    },
    'agree_btn':{
        'xpath':'//*[@text="userProtocol"]'
    },
    'login_btn':{
        'xpath':'//*[@text="登录"]',
        'text':'登录'
    },
    'car_select':{
        'xpath':'//root[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/'
                'Navigation[1]/NavBar[1]/NavBarContent[1]/Tabs[1]/Swiper[1]/TabContent[1]/Stack[1]/Row[1]/Row[1]'
    },
    'car_vin': {
        'xpath': '//*[@text="车架号 %s(后6位)"]'
    },
    'update_btn':{
        'xpath':'//*[@text="更新"]'
    },
    'user_center':{
        'xpath':'//root[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/'
                'Navigation[1]/NavBar[1]/NavBarContent[1]/Tabs[1]/Swiper[1]/TabContent[1]/Stack[1]/Row[1]/'
                '__Common__[1]'
    },
    'settings':{
        'xpath':'//root[1]/Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/'
                'Navigation[1]/NavigationContent[1]/NavDestination[1]/NavDestinationContent[1]/Column[1]/'
                'Row[1]/__Common__[1]/Row[1]/Image[2]'
    },
    'logout':{
        'xpath':'//*[@text="退出登录"]'
    }
}

MHERO_CONFIG_MAP = {
    'Android': MHERO_ANDROID_CONFIG,
    'Harmony': MHERO_HARMONY_CONFIG
}
