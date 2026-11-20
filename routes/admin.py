from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from models import db, User, Course, Exam, RubricCriterion

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Admin access required.')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

@admin_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    pending_examiners = User.query.filter_by(role='examiner', is_approved=False).all()
    approved_examiners = User.query.filter_by(role='examiner', is_approved=True).all()
    courses = Course.query.all()
    exams = Exam.query.all()
    return render_template(
        'admin/dashboard.html',
        pending_examiners=pending_examiners,
        approved_examiners=approved_examiners,
        courses=courses,
        exams=exams
    )

@admin_bp.route('/approve-examiner/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def approve_examiner(user_id):
    user = User.query.get_or_404(user_id)
    if user.role == 'examiner':
        user.is_approved = True
        db.session.commit()
        flash(f'Examiner {user.username} approved.')
    return redirect(url_for('admin.dashboard'))

@admin_bp.route('/courses', methods=['POST'])
@login_required
@admin_required
def create_course():
    code = request.form.get('code').strip().upper()
    name = request.form.get('name').strip()
    if Course.query.filter_by(code=code).first():
        flash('Course code already exists.')
    else:
        db.session.add(Course(code=code, name=name))
        db.session.commit()
        flash('Course created.')
    return redirect(url_for('admin.dashboard'))

@admin_bp.route('/exams', methods=['POST'])
@login_required
@admin_required
def create_exam():
    course_id = request.form.get('course_id')
    title = request.form.get('title').strip()
    booking_start = datetime.strptime(request.form.get('booking_start'), '%Y-%m-%dT%H:%M')
    booking_end = datetime.strptime(request.form.get('booking_end'), '%Y-%m-%dT%H:%M')
    examiner_id = request.form.get('examiner_id') or None

    if booking_end <= booking_start:
        flash('Booking end time must be after start time.')
        return redirect(url_for('admin.dashboard'))

    exam = Exam(
        course_id=int(course_id),
        title=title,
        booking_start=booking_start,
        booking_end=booking_end,
        examiner_id=int(examiner_id) if examiner_id else None
    )
    db.session.add(exam)
    db.session.commit()
    flash('Exam created.')
    return redirect(url_for('admin.dashboard'))

@admin_bp.route('/exams/<int:exam_id>/criteria', methods=['POST'])
@login_required
@admin_required
def add_criterion(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    name = request.form.get('name').strip()
    max_marks = float(request.form.get('max_marks'))
    db.session.add(RubricCriterion(exam_id=exam.id, name=name, max_marks=max_marks))
    db.session.commit()
    flash(f'Criterion added to {exam.title}.')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/exams/<int:exam_id>/reassign', methods=['POST'])
@login_required
@admin_required
def reassign_examiner(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    new_examiner_id = request.form.get('examiner_id')
    if new_examiner_id:
        exam.examiner_id = int(new_examiner_id)
        db.session.commit()
        flash(f'Reassigned examiner for {exam.title}.')
    return redirect(url_for('admin.dashboard'))

@admin_bp.route('/bookings/<int:booking_id>/reschedule', methods=['POST'])
@login_required
@admin_required
def reschedule_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    new_slot_id = int(request.form.get('new_slot_id'))
    new_slot = Slot.query.get_or_404(new_slot_id)

    if new_slot.exam_id != booking.slot.exam_id:
        flash('Can only reschedule to another slot of the same exam.')
        return redirect(url_for('admin.dashboard'))

    active_count = Booking.query.filter_by(slot_id=new_slot.id).filter(Booking.status != 'cancelled').count()
    if new_slot.is_full or active_count >= new_slot.capacity:
        flash('Target slot is already full.')
        return redirect(url_for('admin.dashboard'))

    old_slot = booking.slot
    booking.slot_id = new_slot.id
    old_slot.is_full = False
    if active_count + 1 >= new_slot.capacity:
        new_slot.is_full = True

    db.session.commit()
    flash(f'Rescheduled {booking.student.username} to slot {new_slot.start_time.strftime("%Y-%m-%d %H:%M")}.')
    return redirect(url_for('admin.dashboard'))