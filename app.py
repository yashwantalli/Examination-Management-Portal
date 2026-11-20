from flask import Flask, render_template, redirect, url_for
from flask_login import LoginManager, current_user
from config import Config
from models import db, User
from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.examiner import examiner_bp

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
    app.register_blueprint(admin_bp)
    app.register_blueprint(examiner_bp)

    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', role='admin', is_approved=True)
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()


    @app.route('/')
    def index():
        if current_user.is_authenticated:
            if current_user.role == 'admin':
                return redirect(url_for('admin.dashboard'))
            elif current_user.role == 'examiner':
                return redirect(url_for('examiner.dashboard'))
            elif current_user.role == 'student':
                return redirect(url_for('student.dashboard'))
        return render_template('base.html')

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)