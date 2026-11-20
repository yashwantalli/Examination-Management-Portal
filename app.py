from flask import Flask, render_template
from flask_login import LoginManager
from config import Config
from models import db, User
from routes.auth import auth_bp

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    app.register_blueprint(auth_bp)

    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', role='admin', is_approved=True)
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()

    @app.route('/')
    def index():
        return render_template('base.html')

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)