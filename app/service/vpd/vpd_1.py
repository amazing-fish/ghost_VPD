
                self._vpd.app_stop()
                sleep(180)  # 等待3分钟

                self._vpd.app_start()

                self._vpd.enter_vpd_v2()

                if len(route_info) == 1:
                    case_type, parking_space = list(route_info.items())[0]
                    t1, _ = self._vpd.input_parking_space(parking_space, if_wait=False)

                    self._vpd.driver.click(**self._vpd.app_config.get(case_type))

                elif self._vpd.driver.exists(**self._vpd.app_config.get('vpd_success')):
                    t1 = datetime.now()
                    self._vpd.driver.click(**self._vpd.app_config.get('vpd_success'))
                else:
                    raise Exception('没有可用目标车位')

                time_now = time()
                while time() - time_now < VPD_CALCULATED_WAIT_TIME:
                    sleep(0.5)

                    if not t2 and self._vpd.driver.exists(xpath='//*[@text="前视"]'):
                        t2 = datetime.now()

                    judge_result, fail_tip = self._vpd.judge_calculate_result(is_wait=False)

                    if judge_result is True:
                        result = 'pass'
                        t4 = datetime.now()
                        self._vpd.save_event_db(message='上电成功', event_type=1, event_time=t4)
                        break

                    std_logger.info(f'[power_on_stress_case] get cruise fail tip: {fail_tip}')
                    if fail_tip in ['泊车代驾辅助暂不可用', '路径规划失败', '路线规划失败', '抱歉，无可达路径']:
                        sub_title_xpath = self._vpd.app_config.get('sub_title', {}).get('xpath', '').format(fail_tip)
                        sub_title = self._vpd.driver.get_text(xpath=sub_title_xpath)
                        if fail_tip == '泊车代驾辅助暂不可用':
                            if not t3:
                                t3 = datetime.now()
                                error_msg = f'{fail_tip}: {sub_title}'
                                remark = error_msg
                                self._vpd.save_event_db(message=error_msg, event_type=1, event_time=t3)
                            elif (datetime.now() - t3).total_seconds() > 10:
                                result = 'fail'
                                break

                        if fail_tip in ['路径规划失败', '路线规划失败', '抱歉，无可达路径']:
                            t5 = datetime.now()
                            error_msg = f'{fail_tip}: {sub_title}'
                            remark = error_msg
                            self._vpd.save_event_db(message=error_msg, event_type=1, event_time=t5)
                            result = 'fail'
                            break

                if result is None:
                    raise Exception('未匹配到预期title')

            except Exception as e:
                result, remark, t5 = 'error', str(e), datetime.now()
                std_logger.info(f'[power_on_stress_case] error: {str(e)}, traceback: {traceback.format_exc()}')
                if isinstance(e, ManualStopEvent):
                    raise e

            finally:
                self._vpd.app_stop()
                sleep(120)  # 等待2分钟

                power_on_time = (t4 - t1).total_seconds() if t1 and t4 else None
                VpdTestCaseStats.new(task_id=self._vpd_task.id, case_type=self._vpd_task.case_type,
                                     vin_code=self._vpd._vin_code, vehicle_name='',
                                     app_name=self._vpd.app_name, app_version=self._vpd._app_version,
                                     ads_version='', device_type=self._vpd._device_type,
                                     result=result, t1=t1, t2=t2, t3=t3, t4=t4, t5=t5, power_on_time=power_on_time,
                                     remark=remark)

    def sentinel_connect_vpd_case(self):
        execute_nums = min(self._vpd_task.case_execute_nums, 200)  # 最大执行次数
        final_route_list = list(islice(cycle(self._vpd.route_list), execute_nums))
        case_scene_ls = self._vpd_task.case_scene_ls or [1, 2, 3]
        sub_case_list = list(islice(cycle(case_scene_ls), execute_nums))

        for cur_epoch, (route_info, sub_case_type) in enumerate(zip(final_route_list, sub_case_list), start=1):
            std_logger.info(f'[power_on_stress_case] execute epoch={cur_epoch}, route_info={route_info},'
                            f'sub_case_type={sub_case_type}')
            t4, t5, result, remark = None, None, None, None
            try:
                self._vpd.check_stop_task()
                self._vpd.app_stop()
                sleep(180)  # 等待3分钟

                self._vpd.app_start()

                if sub_case_type == 1:  # 哨兵开启3分钟后，进入VPD
                    self._vpd.enter_sentinel_mode()
                    sleep(180)
                elif sub_case_type == 2:  # 哨兵开启3分钟后，关闭哨兵，进入VPD
                    self._vpd.enter_sentinel_mode()
                    sleep(180)
                    self._vpd.exit_sentinel_mode()
                elif sub_case_type == 3:  # 哨兵开启3分钟后，关闭哨兵，3分钟后进入VPD
                    self._vpd.enter_sentinel_mode()
                    sleep(180)
                    self._vpd.exit_sentinel_mode()
                    sleep(180)
                else:
                    std_logger.info(f'[sentinel_connect_vpd_case] not match sub_case: {sub_case_type},'
                                    f'cur_epoch: {cur_epoch}')
                    continue

                case_type, parking_space = next(iter(route_info.items()))
                self._vpd.enter_vpd_v2()
                self._vpd.start_vpd(case_type, parking_space)
                result, t4 = 'pass', datetime.now()  # 激活成功
                self._vpd.check_cruise_event(case_type, parking_space)
                self._vpd.vpd_finish(case_type)


            except Exception as e:
                std_logger.info(f'[sentinel_connect_vpd_case] error: {str(e)}, traceback: {traceback.format_exc()}')

                if isinstance(e, ManualStopEvent):
                    result, remark = 'error', str(e)
                    raise e
                elif isinstance(e, (UnavailableException, ExitedException, PlanningFailureException)):
                    fail_tip = self._vpd.driver.get_text(**self._vpd.app_config.get('cruise_tip'))
                    sub_title_xpath = self._vpd.app_config.get('sub_title', {}).get('xpath', '').format(fail_tip)
                    sub_title = self._vpd.driver.get_text(xpath=sub_title_xpath)
                    result, remark, t5 = 'fail', f'{fail_tip}: {sub_title}', datetime.now()
                else:
                    result, remark, t5 = 'error', str(e), datetime.now()

            finally:
                VpdTestCaseStats.new(task_id=self._vpd_task.id, case_type=self._vpd_task.case_type,
                                     sub_case_type=sub_case_type, vin_code=self._vpd._vin_code, vehicle_name='',
                                     app_name=self._vpd.app_name, app_version=self._vpd._app_version,
                                     ads_version='', device_type=self._vpd._device_type,
                                     result=result, t4=t4, t5=t5, remark=remark)


    def vpd_location_case(self):
        # 运行结束没有位置信息
        pass

    def vpd_location_case_2(self, epoch):
        # 记录车辆位置信息
        enter_vpd_time, result, remark = None, '', ''
        try:
            self._vpd.app_start()
            self._vpd.enter_vpd()
            enter_vpd_time = datetime.now()  # 进图时间

            floor = self._vpd.driver.get_text(**self._vpd.app_config.get('floor')),
            if isinstance(floor, tuple):
                floor, = floor

            self._vpd.driver.swipe(x1=0.5, y1=0.6, x2=0.5, y2=0.4)
            sleep(1)
            if self._vpd._app_type == '#01':
                self._vpd.driver.swipe(x1=0.5, y1=0.6, x2=0.5, y2=0.4)
            else:
                self._vpd.driver.swipe(x1=0.5, y1=0.8, x2=0.5, y2=0.4)
            sleep(1)
            location = self._vpd.driver.get_text(**self._vpd.app_config.get('park_location'))
            location = location.strip() if location and isinstance(location, str) else '未知'
            if self._vpd._app_type == '#01':
                location = location.split(' ')[0]
                image_y = 0.8
            else:
                image_y = 0.6

            image_result = 'pass'
            image_view = ['俯视', '前视', '后视', '左前视', '右前视', '左后视', '右后视']

            try:
                for i in range(2):
                    if i == 0:
                        x2 = 0.2
                        final_image_view = image_view
                    else:
                        x2 = 0.8
                        final_image_view = [image_view[0]] + image_view[1:][::-1]

                    for view in final_image_view:
                        view_name  = self._vpd.driver.get_text(**self._vpd.app_config.get('view_name'))
                        if view != view_name:
                            raise Exception(f'视角不匹配，按顺序应为：{view}，当前视角为：{view_name}')
                        self._vpd.driver.swipe(x1=0.5, y1=image_y, x2=x2, y2=image_y)
                        sleep(1)

                if self._vpd._app_type == '#01':
                    if self._vpd._device_type == 'Android':
                        self._vpd.tap_by_scale(x_scale=0.5, y_scale=0.8)
                    else:
                        self._vpd.driver.click(x=0.5, y=0.8)
                else:
                    if self._vpd._device_type == 'Android':
                        self._vpd.tap_by_scale(x_scale=0.5, y_scale=0.6)
                sleep(2)

                for i in range(2):
                    if i == 0:
                        x1, x2 = 0.8, 0.2
                    else:
                        x1, x2 = 0.2, 0.8
                    for view in image_view:
                        self._vpd.driver.swipe(x1=0.5, y1=image_y, x2=x2, y2=image_y)
                        sleep(1)

                self._vpd.driver.press('back')
                if not self._vpd.driver.exists(**self._vpd.app_config.get('park_location')):
                    raise Exception('返回异常')

            except Exception as e:
                std_logger.info(f'[vpd_location_case_2] image_result error: {str(e)}, traceback: {traceback.format_exc()}')
                image_result = str(e)

            result = f'{floor}###{location}###{image_result}'

        except Exception as e:
            std_logger.info(f'[vpd_location_case_2] error: {str(e)}, traceback: {traceback.format_exc()}')
            result, remark = 'error', str(e)

        finally:
            VpdTestCaseStats.new(task_id=self._vpd_task.id, case_type=self._vpd_task.case_type,
                                 vin_code=self._vpd._config_vin_code, vehicle_name='',
                                 app_name=self._vpd.app_name, app_version=self._vpd._app_version,
                                 ads_version='', device_type=self._vpd._device_type,
                                 result=result, t1=enter_vpd_time, remark=remark,
                                 device_sn=self._vpd._device_sn, device_name=self._vpd._device_name,
                                 epoch=epoch)
            self._vpd.app_stop()


    def start_case(self):
        self._case_map[self._vpd_task.case_type]()


class AppLoginCar:
    def __init__(
        self,
        vpd: VPD,
        username: str,
        password: str,
        vin_code: str
    ):
        self.vpd = vpd
        self.driver = vpd.driver
        self.app_config = vpd.app_config
        self.device_type = vpd._device_type
        self.app_type = vpd._app_type

        self.username = username.strip()
        self.password = password.strip()
        self.vin_code = vin_code.strip()
        self.vin_suffix = self.vin_code[-6:]
        self.tag = "[AppLoginCar]"

    def _login(self):
        std_logger.info(self.tag + f"开始执行登录流程，账号：{self.username}")
        try:
            if self.app_type == "#01":
                if self.driver.exists(**self.app_config.get("i_know_button")):
                    self.driver.click(**self.app_config.get("i_know_button"))
                    sleep(2)

                self.driver.click(**self.app_config.get('my'))
                sleep(2)
            elif self.app_type == "#08":
                self.driver.click(**self.app_config.get('discover'))
                sleep(2)
            is_logout = self._get_login_status()
            if not is_logout:
                self._account_logout()
                sleep(2)

            if self.app_type == "#01":
                self.driver.click(**self.app_config.get("assess_info"))
                sleep(2)
                self.driver.click(**self.app_config.get("account_btn"))
                sleep(2)

                self.vpd.wait_for_element(config_keys=["number_input"], if_appear=False)
                self.driver.set_text(self.username, **self.app_config.get("number_input"))
                sleep(2)

                manual_btn = self.app_config.get("manual_btn")
                if manual_btn and self.driver.exists(**manual_btn):
                    self.driver.click(**manual_btn)
                    self.driver.set_text(self.username, **self.app_config.get("number_input"))
                    sleep(2)

                self.driver.click(**self.app_config.get("pwd_input"))
                sleep(2)
                self._input_password(self.password)
                sleep(2)

                self.driver.click(x=0.50, y=0.15)
                sleep(2)

                self.driver.click(**self.app_config.get("login_btn"))
                sleep(2)

                if self.driver.exists(**self.app_config.get("confirm_button")):
                    self.driver.click(**self.app_config.get("confirm_button"))
                    sleep(2)

            elif self.app_type == "#08":
                self.driver.click(**self.app_config.get("assess_icon"))
                sleep(2)
                self.driver.click(**self.app_config.get("login_icon"))
                sleep(2)
                self.driver.click(**self.app_config.get("pwd_login"))
                sleep(2)

                self.vpd.wait_for_element(config_keys=["number_input"], if_appear=False)
                self.driver.set_text(self.username, **self.app_config.get("number_input"))
                sleep(2)

                manual_btn = self.app_config.get("manual_btn")
                if manual_btn and self.driver.exists(**manual_btn):
                    self.driver.click(**manual_btn)
                    self.driver.set_text(self.username, **self.app_config.get("number_input"))
                    sleep(2)

                self.driver.click(x=0.50, y=0.15)
                sleep(2)

                self.driver.click(**self.app_config.get("pwd_input"))
                sleep(2)
                self._input_password(self.password)
                sleep(2)

                self.driver.click(**self.app_config.get("agree_btn"))
                sleep(2)
                self.driver.click(x=0.50, y=0.15)
                self.driver.click(**self.app_config.get("login_btn"))
                sleep(2)

            sleep(5)
            self._handle_common_popups()
            sleep(5)
            self._verify_login_result()
        except Exception as e:
            raise

    def _select_car(self):
        vin_suffix = str(self.vin_code).strip()[-6:]
        std_logger.info(self.tag + f"目标VIN后六位：{vin_suffix}")
        try:
            if self.app_type == "#01":
                self.driver.click(**self.app_config.get("car"))
                self.vpd.wait_for_element(config_keys=["car_select"], if_appear=False)
                car_select_cfg = self.app_config.get("car_select")
                if car_select_cfg:
                    self.driver.click(**car_select_cfg)
                sleep(2)

            elif self.app_type == "#08":
                car_select_cfg = self.app_config.get("car_select")
                if car_select_cfg:
                    self.driver.click(**car_select_cfg)
                sleep(2)
            std_logger.info(self.tag + "开始自动查找车辆")

            max_swipes = 15
            for i in range(max_swipes):
                if self._find_car(vin_suffix):
                    return
                self.driver.swipe(x1=0.50, y1=0.60, x2=0.50, y2=0.30)
                sleep(2)
            raise Exception(f"未找到车辆VIN后六位：{vin_suffix}")
        except Exception as e:
            std_logger.error(self.tag + f"选车失败：{str(e)}")
            raise

    def _get_login_status(self) -> bool:
        logout_text_map = {"#01": "注册/登录", "#08": "探索"}
        if self.app_type not in logout_text_map:
            return True
        assess_info = self.app_config.get("assess_info")
        if not assess_info or not self.driver.exists(**assess_info):
            return True
        try:
            current_text = self.driver.get_text(**assess_info).strip()
        except Exception:
            return True
        return not current_text or current_text == logout_text_map[self.app_type]

    def _input_password(self, pwd):
        try:
            pwd_cfg = self.app_config.get("pwd_input")
            if not pwd_cfg:
                raise Exception("[密码输入] 无pwd_input控件")
            # self.vpd.wait_for_element(config_keys=["pwd_input"], wait_time=10)
            self.driver.set_text(text=pwd, **pwd_cfg)
        except Exception as e:
            std_logger.error(f"[密码输入] 失败：{str(e)}")
            raise

    def _handle_common_popups(self):
        popup_keys = ["update_btn", "return_btn", "confirm","online_confirm", "next_btn"]
        try:
            for key in popup_keys:
                cfg = self.app_config.get(key)
                if cfg and self.driver.exists(**cfg):
                    self.driver.click(**cfg)
                    sleep(0.8)
        except Exception as e:
            std_logger.warning(f"[处理弹窗] 异常：{str(e)}")

    def _verify_login_result(self):
        """登录校验"""
        if self.app_type == "#08":
            self.driver.click(**self.app_config.get('discover'))
            sleep(1)
        if self._get_login_status():
            raise Exception("[登录检查] 登录失败")
        std_logger.info("[登录检查] 登录成功验证通过")

    def _find_car(self, vin_suffix: str):
        try:
            car_vin_cfg = self.app_config.get("car_vin")
            car_vin_xpath = car_vin_cfg.get("xpath")
            if "%s" in car_vin_xpath:
                target_xpath = car_vin_xpath % vin_suffix
                if self.driver.exists(xpath=target_xpath):
                    self.driver.click(xpath=target_xpath)
                    sleep(2)
                    return True
            else:
                elements = self.driver.xpath(car_vin_xpath).all()
                for ele in elements:
                    if vin_suffix in ele.text.strip():
                        ele.click()
                        sleep(2)
                        return True
        except Exception:
            return False
        return False

    def _account_logout(self):
        try:
            config_map = {
                "#01": {"entry": "my", "confirm": "logout_confirm"},
                "#08": {"entry": "user_center", "confirm": "confirm"}
            }
            if self.app_type not in config_map:
                return
            entry_key = config_map[self.app_type]["entry"]
            confirm_key = config_map[self.app_type]["confirm"]

            if not self.app_config.get(entry_key):
                return
            std_logger.info("[通用登出] 执行账户登出流程")
            self.driver.click(**self.app_config.get(entry_key))
            sleep(2)

            if self.app_type == "#01":
                self.driver.swipe(x1=0.50, y1=0.70, x2=0.50, y2=0.20)
                sleep(2)

            self.driver.click(**self.app_config.get("settings"))
            sleep(2)

            self.driver.click(**self.app_config.get("logout"))
            sleep(2)

            self.driver.click(**self.app_config.get(confirm_key))
            sleep(1)
            if self.app_type == "#01":
                self.driver.swipe(x1=0.50, y1=0.20, x2=0.50, y2=0.70)
                sleep(2)
            std_logger.info("[通用登出] 账户登出完成")
        except Exception as e:
            std_logger.error(f"[通用登出] 账户登出失败：{str(e)}")

    def run(self):
        try:
            self.vpd.app_start()
            self._login()
            self._select_car()
        except Exception as e:
            std_logger.error(f"{self.tag} 执行失败：{str(e)}, {traceback.format_exc()}")
        finally:
            self.vpd.app_stop()
