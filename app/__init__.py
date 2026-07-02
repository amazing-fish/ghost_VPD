from flask import Flask

from app.blueprint.interior_api import bp_interior_api


class Config:

    @classmethod
    def init_app(cls, app: Flask):
        app.register_blueprint(bp_interior_api, url_prefix='/api')


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    Config.init_app(app)
    return app
