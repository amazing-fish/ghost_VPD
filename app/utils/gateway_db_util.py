import datetime
import json
import operator
import sys
import traceback
from functools import reduce

import requests
from peewee import DateTimeField, BooleanField
from requests import Response

from app.constant import REMOTE_MODE, ST_DOMAIN
from app.utils.config_util import config_util

from app.utils.logger import std_logger

ALLOWED_ACTIONS = ('SELECT', 'INSERT', 'INSERT_MANY', 'UPDATE')

OPERATOR_MAP = {
    'eq': operator.eq,  # 等于 (默认)
    'neq': operator.ne,  # 不等于
    'gt': operator.gt,  # 大于 >
    'gte': operator.ge,  # 大于等于 >=
    'lt': operator.lt,  # 小于 <
    'lte': operator.le,  # 小于等于 <=

    # Peewee 特殊操作符映射
    'in': lambda field, val: field << val,  # 包含 IN (Peewee 中用 << 表示 IN)
    'not_in': lambda field, val: ~(field << val),  # 不包含 NOT IN
    'like': lambda field, val: field ** val,  # 模糊匹配 LIKE (Peewee 中用 ** 表示 LIKE)
    'contains': lambda field, val: field.contains(val),  # 字符串包含 (自动加 %%)
    'is_null': lambda field, val: field.is_null(val),  # 是否为空 (传布尔值 True/False)
}


class GatewayClient:
    """Gateway数据库服务"""

    def __init__(self):
        self.access_key =  config_util.config.get('access_key')
        self.secret_key = config_util.config.get('secret_key')
        self.headers = {
            "Accept-Language": "zh-CN",
            "Content-Type": "application/json",
            "X-Access-Key": self.access_key,
            "X-Secret-Key": self.secret_key
        }
        self.session = requests.Session()
        self.domain = ST_DOMAIN

    def get_resp_json(self, resp: Response):
        try:
            method = resp.request.method
            if_success = (200 == resp.status_code)
            parent_func_name = sys._getframe(1).f_code.co_name
            len_req = len(resp.request.body) if isinstance(resp.request.body, bytes) else 0
            len_resp = len(resp.text) if isinstance(resp.text, str) else 0
            f_resp = resp.text if len_resp < 5000 else resp.text[:5000]
            if if_success:
                simple_log = (f'[GATEWAY][Success][{method}] func=[{parent_func_name}], len_req=[{len_req}], '
                              f'len_resp=[{len_resp}]')
            else:
                simple_log = (f'[GATEWAY][Error][{method}] func=[{parent_func_name}], len_req=[{len_req}], '
                              f'status_code=[{resp.status_code}]')
            std_logger.info(f'{simple_log}, url=[{resp.url}], req=[{resp.request.body}], f_resp=[{f_resp}]')
        except Exception:
            std_logger.info(traceback.format_exc())
            std_logger.info(f'[GATEWAY][Error] fail to record log')

        try:
            resp.raise_for_status()
        except Exception as e:
            std_logger.info(traceback.format_exc())
            raise e

        ret = resp.json()
        if ret.get('code') != 200 or ret.get('message') != 'Success':
            raise RuntimeError(ret.get('message'))
        return ret

    def http_db_execute(self, action: str, table_name: str, filters: dict = None, data: dict = None,
                   data_list: list = None):
        url = f'{self.domain}/api/external/gateway_vpd/db/execute/'
        body = {
            "action": action,
            "table_name": table_name,
            "filters": filters or {},
            "data": data or {},
            "data_list": data_list or [],
        }
        resp = self.session.post(url, json=body, headers=self.headers, verify=False)
        ret = self.get_resp_json(resp).get('data', {})
        return ret


gateway_client = None
if REMOTE_MODE:
    gateway_client = GatewayClient()


class DataAccessor:
    """
    统一数据访问层，支持本地与网关双路切换
    """

    def __init__(self, model_class):
        self.model_class = model_class
        self.table_name = model_class._meta.table_name
        self.client = gateway_client

    @staticmethod
    def build_where_clause(model_class, filters: dict):
        """
        filters: {key: val}, key: "age__gt" -> field="age", op="gt"
        """
        if not filters:
            return None

        expressions = []

        for key, value in filters.items():
            if not isinstance(key, str):
                raise ValueError("Security Error: Filter keys must be strings")
            parts = key.split('__', 1)
            field_name = parts[0]
            op_name = parts[1] if len(parts) > 1 else 'eq'  # 默认操作符: eq

            if not hasattr(model_class, field_name):
                raise ValueError(
                    f"Security Error: Invalid field '{field_name}' in table '{model_class._meta.table_name}'")
            if op_name not in OPERATOR_MAP:
                raise ValueError(f"Security Error: Unsupported operator '{op_name}'")

            field_obj = getattr(model_class, field_name)
            op_func = OPERATOR_MAP[op_name]
            try:
                expression = op_func(field_obj, value)
                expressions.append(expression)
            except Exception as e:
                raise ValueError(f"Error applying operator '{op_name}' on field '{field_name}': {str(e)}")

        if expressions:
            return reduce(operator.and_, expressions)

        return None

    @staticmethod
    def serialize_param(params: dict):
        if not params:
            return {}

        def __serialize_obj(obj):
            if isinstance(obj, (datetime.datetime, datetime.date)):
                return obj.strftime('%Y-%m-%d %H:%M:%S')
            else:
                return obj

        return {k: __serialize_obj(v) for k, v in params.items()}

    def _map_to_model(self, raw_data_list: list):
        """
        将网关返回的 list[dict] 映射为正确的 Peewee Model 实例
        :param raw_data_list: 从网关获取的字典列表 (如 [{"id": 1, "name": "a"}, ...])
        :return: 对应的 Model 实例列表
        """
        if not raw_data_list:
            return []

        instances = []
        fields = self.model_class._meta.fields

        for row in raw_data_list:
            processed_data = {}
            for name, value in row.items():
                if name not in fields or value is None:
                    processed_data[name] = value
                    continue

                field_obj = fields[name]

                try:
                    if type(field_obj).__name__ == 'JSONTextField':
                        if isinstance(value, str):
                            processed_data[name] = json.loads(value)
                        else:
                            processed_data[name] = value

                    elif isinstance(field_obj, DateTimeField):
                        if isinstance(value, str):
                            processed_data[name] = field_obj.python_value(value)
                        else:
                            processed_data[name] = value

                    elif isinstance(field_obj, BooleanField):
                        if isinstance(value, bool):
                            processed_data[name] = value
                        elif isinstance(value, int):
                            processed_data[name] = bool(value)
                        else:
                            processed_data[name] = field_obj.python_value(value)

                    else:
                        processed_data[name] = field_obj.python_value(value)

                except Exception as e:
                    std_logger.error(f"fields transfer failed, field={name}, value={value},error={str(e)}")
                    processed_data[name] = value

            instance = self.model_class(**processed_data)
            instance._dirty.clear()
            instances.append(instance)

        return instances

    def _execute_local(self, action: str, filters: dict, data: dict, data_list: list):
        """本地原生 Peewee 执行"""
        where_clause = self.build_where_clause(self.model_class, filters)

        if action == 'SELECT':
            query = self.model_class.select()
            if where_clause: query = query.where(where_clause)
            return query

        elif action == 'INSERT':
            return self.model_class.insert(**data).execute()

        elif action == 'INSERT_MANY':
            return self.model_class.insert_many(data_list).execute()

        elif action == 'UPDATE':
            return self.model_class.update(**data).where(where_clause).execute()

    def _execute_remote(self, action: str, filters: dict, data: dict, data_list: list):
        """网关 HTTP 执行"""
        resp_json = self.client.http_db_execute(action,
                                           self.table_name,
                                           self.serialize_param(filters),
                                           self.serialize_param(data),
                                           [self.serialize_param(item) for item in data_list] if data_list else None)

        if action == 'SELECT' and resp_json.get("query_data"):
            resp_json["query_data"] = self._map_to_model(resp_json["query_data"])

        if action == 'SELECT':
            return resp_json.get("query_data") or []
        elif action == 'INSERT':
            return resp_json.get("last_row_id") or None
        elif action in ['UPDATE', 'INSERT_MANY']:
            return resp_json.get("row_count") or 0

        return resp_json

    def execute(self, action: str, filters: dict = None, data: dict = None, data_list: list = None):
        """统一路由入口"""
        if REMOTE_MODE:
            return self._execute_remote(action, filters, data, data_list)
        return self._execute_local(action, filters, data, data_list)

    def select(self, filters: dict):
        return self.execute('SELECT', filters=filters)

    def get_by_id(self, pk):
        pk_field = self.model_class._meta.primary_key.name
        query_data = self.execute('SELECT', filters={pk_field: pk})
        return query_data[0] if query_data else None

    def insert(self, data: dict):
        return self.execute('INSERT', data=data)

    def insert_many(self, data_list: list):
        if not data_list:
            return 0
        return self.execute('INSERT_MANY', data_list=data_list)

    def update(self, filters: dict, data: dict):
        return self.execute('UPDATE', filters=filters, data=data)

    def update_status(self, pk, status: int, reason: str = ''):
        return self.execute('UPDATE', filters={'id': pk}, data={'status': status, 'reason': reason})

