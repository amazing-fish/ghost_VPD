from flask import Blueprint

from .hooks import before_request, teardown_request
from ..api_helper import APIError, handle_api_error, handle_503_error, handle_400_error, handle_401_error, \
    handle_500_error, before_api_request

bp_interior_api = Blueprint('bp_interior_api', __name__)
bp_interior_api.register_error_handler(APIError, handle_api_error)
bp_interior_api.register_error_handler(500, handle_500_error)
bp_interior_api.register_error_handler(503, handle_503_error)
bp_interior_api.register_error_handler(400, handle_400_error)
bp_interior_api.register_error_handler(401, handle_401_error)
bp_interior_api.before_request(before_api_request)
bp_interior_api.before_request(before_request)
bp_interior_api.teardown_request(teardown_request)
from . import v_task
