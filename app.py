from flask import Flask
from config import Config

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    @app.route('/')
    def index():
        return "Examination Management Portal - Under Construction"

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)