from numbers import Number
from datetime import datetime
from flask import g, make_response, request, Response
from peewee import Query
from werkzeug.exceptions import HTTPException


__all__ = [
    'APIError',
    'handle_api_error',
    'handle_500_error',
    'handle_503_error',
    'handle_400_error',
    'handle_401_error',
    'before_api_request',
    'api_success_response',
    'claim_args',
    'claim_args_null',
    'claim_args_true',
    'claim_args_bool',
    'claim_args_int',
    'claim_args_number',
    'claim_args_str',
    'claim_args_digit_str',
    'claim_args_list',
    'claim_args_dict',
    'claim_args_length',
    'paginate',
    'claim_args_list_oversize',
    'claim_args_time_range',
    'claim_args_not_none'
]

from app.utils.logger import std_logger


class APIError(Exception):
    """API错误"""

    # 错误码 & 错误描述
    ERRORS = {
        1000: 'Internal Server Error',
        1003: 'Service Unavailable',
        1100: 'Bad Request',
        1101: 'Unauthorized',
        1102: 'No permission',
        1103: 'Forbidden',
        1104: 'Not Found',
        1201: 'url参数不完整',
        1202: 'url参数值错误',
        1203: 'json数据不完整',
        1204: 'json数据类型错误',
        1205: 'json数据值错误',
        1206: 'json数据长度超出',
        1207: 'json数据不能为空值',
        1301: '用户名或密码错误',
        1302: '密码长度不符合要求',
        1307: '时间格式错误',
        1308: '存在运行中或异常任务',
        # 新增
        10002: '版本不存在',
        10003: '账号未在平台注册，请联系管理员',
        10004: 'w3服务异常',
        10005: 'w3账号或密码错误',
        10006: 'DI服务异常或查询超时',
        10007: 'ATI服务异常',
        10008: '请检查版本号是否正确',
        10009: 'CIDA登录失败',
        13001: '用户已存在',
        13002: '密码不合规',
        13003: '角色已存在',
        14001: '无效token',
        16001: '非管理员用户无权操作',
        16002: '无法重置自身密码',
        16003: '请检查当前用户信息',
        17001: '密码错误',
        17002: '密码长度不符合要求',
        18001: '无法删除自身账号',
        18002: '当前用户无权操作',
        19001: '当前环境ip已存在',
        20001: '该人员信息已存在',
        20002: '该版本已存在',
        20003: '该角色不存在',
        20004: '此问题单已经存在!',
        20005: '未找到单号相关数据',
        20006: '工号格式错误',
        20007: '创建提单失败',
        20008: '重复提交',
        20009: '当前流程已完成',
        20010: '当前流程已终止',
        20011: '非创建人无法操作',
        20012: '数据已存在',
        20013: '文件重复上传',
        20014: '上传文件名错误',
        20015: '系统找不到指定的文件',
        20016: '未找到相关文件',
        20017: '非指定人无法操作',
        20018: '文件损坏',
        20019: '模板不存在',
        20020: '车型已存在',
        21001: '当前公告号已存在',
        21002: '请检查员工信息',
        21003: '该员工未录入邮箱信息',
        21004: '请完善邮箱信息',
        21005: '邮件发送失败',
        21006: '邮箱或密码错误',
        21007: '非责任人及同组成员，无法编辑',
        21008: '非录入人及同组成员，无权删除',
        21009: '平台未登记该责任人信息，请联系管理员',
        21010: '员工信息不全，请退出重新登录',
        21011: '未找到项目团队，请联系管理人员',
        30001: '数据正在更新中',
        30002: '多次登录失败，请联系管理人员',
        30003: '该MDC已存在',
        30004: '该任务无权限操作',
        30005: '版本号格式错误',
        30006: '当前用户无DI账号',
        30007: '版本和id不能同时存在',
        30008: '该名称已存在',
        30009: '该分布图已存在',
        30010: '该关键词已存在',
        30011: '请检查map_problem_ids是否正确',
        30012: '请检查DTS单号',
        30013: '该表名称已存在',
        30014: '该车型下已存在此车',
        30015: '该路线已存在',
        30016: '默认组不能删除',
        30017: '默认组不能修改名称',
        30018: '该分类已存在',
        30019: '该车辆品牌已存在',
        30020: '仅能编辑自己或同PL组名下的专题',
        30021: '任务正在执行中，请勿重复操作',
        30022: '当前用户无DDI生产账号',
        30023: '当前用户无DDI预生产账号',
        40001: '更新版本数量不得大于5',
        40002: '该报告KPI分析表已生成/生成中',
        40003: '已有任务正在运行中，请稍后操作',
        40004: '请输入地图版本或RC版本',
        40005: '输入数据不在允许范围内',
        40006: '任务运行中，请稍后操作',
        40007: '每页最多显示100条',
        40008: '导入表格表头不正确，请在事件对比工具导出表格基础上进行编辑',
        40009: '未找到新增事件对比任务',
        40010: '未运行任务和进行中任务总数不能大于20',
        40011: '一次最多支持导出60个任务',
        40012: '需要有DTS问题单号或者为非问题时，才能选择分析完成',
        40013: '一次最多支持10个任务',
        40014: '一次最多支持10个专题',
        40015: '非可重跑任务',
        40016: '批量选择范围为2~50条',
        40017: '载入模板失败',
        40018: '文件格式错误',
        40019: '导入表格表头不正确',
        40020: 'AdsCloud-Grafana服务不可用',
        40021: '专题不可用',
        40022: '专题已存在',
        40023: '该任务状态不可执行或已生成kpi',
        40024: '查询初始化数据失败',
        40025: '基线版本最多选择2个',
        40026: '请选择报告对应任务类型的任务',
        40027: '模板任务不存在',
        40028: '存在未被授权的任务',
        40029: '存在不符合条件的任务',
        40030: '加入车辆超过5辆',
        40031: '存在未配置授权名单的任务',
        40032: '当前已存在同步任务',
        40033: '该车型已存在模板',
        40034: '存在编号或路线为空',
        40035: '上传文件内容为空',
        40036: '仅支持单个目录URL',
        40037: '存在非法目标URL',
        40038: '仅支持状态为已完成任务',
        40039: '当前两任务不支持合并查看',
        40040: '任务类型不能为空',
        40041: '事件已存在',
        40042: '事件链接DDI不存在',
        40043: '仅创建人或责任人可操作',
        40044: '当前存在同步最新车型看板数据任务',
        40045: '上传文件内容存在空值',
        40046: '当前任务已启动，不支持编辑'
    }

    def __init__(self, code: int, message: str = None, status_code: int = 200):
        """Initializer

        Args:
            code: 错误码
            message: 错误描述
            status_code: HTTP状态码
        """
        super().__init__()
        self.code = code
        self.message = message or self.ERRORS.get(code, 'Undefined')
        self.status_code = status_code

    def to_response(self) -> Response:
        """转换为flask.Response"""
        if request.method in ['POST', 'PUT', 'PATCH']:
            if g.json and g.json.get('password'):
                g.json['password'] = '********'
            if g.json and g.json.get('token'):
                g.json['token'] = '********'
            std_logger.warning('[Interface]: @{0.endpoint} {0.view_args} {1} => {2!r}'.format(request, g.json, self))
        else:
            std_logger.warning('[Interface]: @{0.endpoint} {0.full_path} => {1!r}'.format(request, self))
        resp_data = {
            'code': self.code,
            'message': self.message,
            'data': {}
        }
        return make_response(resp_data, self.status_code)

    def __repr__(self):
        return f'{self.__class__.__name__}({self.code}, {self.message!r})'

    def __str__(self):
        return self.message


def handle_500_error(e: HTTPException) -> Response:
    """处理500错误"""
    std_logger.critical(repr(e))
    e = APIError(1000)
    return e.to_response()


def handle_503_error(e: HTTPException) -> Response:
    """处理503错误"""
    std_logger.critical(repr(e))
    e = APIError(1003)
    return e.to_response()


def handle_api_error(e: APIError) -> Response:
    """处理APIError"""
    return e.to_response()


def handle_400_error(e: HTTPException) -> Response:
    """处理400错误"""
    std_logger.error(repr(e))
    e = APIError(1100)
    return e.to_response()


def handle_401_error(e: HTTPException) -> Response:
    """处理401错误"""
    std_logger.error(repr(e))
    e = APIError(1101)
    return e.to_response()


def before_api_request():
    """API请求前钩子函数

    Raises:
        werkzeug.exceptions.BadRequest
    """
    if request.method in ['POST', 'PUT', 'PATCH']:
        g.json = None  # g.json
        if request.is_json:
            g.json = request.get_json()
            if isinstance(g.json, dict):
                return


def api_success_response(data: dict) -> dict:
    """API请求成功的响应"""
    client_ip = request.headers.get('X-Forwarded-For', None)
    if client_ip:
        # X-Forwarded-For 可能包含多个 IP 地址，取第一个即可
        client_ip = client_ip.split(',')[0]
    else:
        client_ip = request.remote_addr
    std_logger.info('[Interface]: {0}@{1.endpoint} {1.view_args} {2} => Success'.format(client_ip, request,
                                                                                        g.json))
    return {
        'code': 200,
        'message': 'Success',
        'data': data
    }


def claim_args(code: int, *args, message: str = None):
    """如果args存在None，则抛出APIError

    Args:
        code: APIError错误码
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if a is None:
            raise APIError(code, message)


def claim_args_null(code: int, *args, message: str = None):
    """如果args存在None或空值，则抛出APIError

    Args:
        code: APIError错误码
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if not a:
            raise APIError(code, message)


def claim_args_true(code: int, *args, message: str = None):
    """如果args不都为真值，则抛出APIError

    Args:
        code: APIError错误码
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if not a:
            raise APIError(code, message)


def claim_args_bool(code: int, *args, message: str = None):
    """如果args不都为bool，则抛出APIError

    Args:
        code: APIError错误码
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if not isinstance(a, bool):
            raise APIError(code, message)


def claim_args_int(code: int, *args, message: str = None):
    """如果args不都为int，则抛出APIError

    Args:
        code: APIError错误码
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if not isinstance(a, int):
            raise APIError(code, message)


def claim_args_number(code: int, *args, message: str = None):
    """如果args不都为Number，则抛出APIError

    Args:
        code: APIError错误码
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if not isinstance(a, Number):
            raise APIError(code, message)


def claim_args_str(code: int, *args, message: str = None):
    """如果args不都为str，则抛出APIError

    Args:
        code: APIError错误码
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if not isinstance(a, str):
            raise APIError(code, message)


def claim_args_digit_str(code: int, *args, message: str = None):
    """如果args不都为数字字符串，则抛出APIError

    Args:
        code: APIError错误码
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if not (isinstance(a, str) and a.isdigit()):
            raise APIError(code, message)


def claim_args_list(code: int, *args, message: str = None):
    """如果args不都为list，则抛出APIError

    Args:
        code: APIError错误码
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if not isinstance(a, list):
            raise APIError(code, message)


def claim_args_list_oversize(code: int, size_check: int, *args, message: str = None):
    """如果args len 大于size_check，则抛出APIError

    Args:
        code: APIError错误码
        size_check: 指定size
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if len(a) > size_check:
            raise APIError(code, message)


def claim_args_dict(code: int, *args, message: str = None):
    """如果args不都为dict，则抛出APIError

    Args:
        code: APIError错误码
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if not isinstance(a, dict):
            raise APIError(code, message)


def claim_args_length(code: int, length: int, *args, message: str = None):
    """如果args长度都超过length，则抛出APIError

    Args:
        code: APIError错误码
        length: 长度
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if len(a) > length:
            raise APIError(code, message)


def claim_args_time_range(code: int, *args, message: str = None):
    """如果args不都为str，则抛出APIError

    Args:
        code: APIError错误码
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        start_time = datetime.strptime(a[0], '%Y-%m-%d %H:%M:%S')
        end_time = datetime.strptime(a[1], '%Y-%m-%d %H:%M:%S')
        delta = end_time - start_time
        if delta.days > 31:
            raise APIError(code, message)


def claim_args_not_none(code: int, *args, message: str = None):
    """如果args不存在None，则抛出APIError

    Args:
        code: APIError错误码
        args: 参数列表
        message: APIError错误描述
    """
    for a in args:
        if a is not None:
            raise APIError(code, message)


def paginate(query: Query) -> Query:
    """分页查询"""
    page, limit = map(request.get_json().get, ['page', 'limit'])
    try:
        page = int(page) if page else 0
        limit = int(limit) if limit else 0
    except ValueError:
        pass
    else:
        if page > 0:
            if limit > 0:
                return query.paginate(page, limit)
            else:
                return query.paginate(page)
    return query
