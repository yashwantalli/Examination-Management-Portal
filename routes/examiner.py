from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from models import db, Exam, Slot, Booking, Evaluation

examiner_bp = Blueprint('examiner', __name__, url_prefix='/examiner')

def examiner_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'examiner':
            flash('Examiner access required.')
            return redirect(url_for('index'))
        if not current_user.is_approved:
            flash('Your account is awaiting Admin approval.')
            return redirect(url_for('auth.logout'))
        return f(*args, **kwargs)
    return decorated

@examiner_bp.route('/dashboard')
@login_required
@examiner_required
def dashboard():
    assigned_exams = Exam.query.filter_by(examiner_id=current_user.id).all()
    my_slots = Slot.query.filter_by(examiner_id=current_user.id).all()
    return render_template('examiner/dashboard.html', exams=assigned_exams, slots=my_slots)

@examiner_bp.route('/exams/<int:exam_id>/slots', methods=['POST'])
@login_required
@examiner_required
def create_slot(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if exam.examiner_id != current_user.id:
        flash('You are not assigned to this exam.')
        return redirect(url_for('examiner.dashboard'))

    start_time = datetime.strptime(request.form.get('start_time'), '%Y-%m-%dT%H:%M')
    end_time = datetime.strptime(request.form.get('end_time'), '%Y-%m-%dT%H:%M')
    capacity = int(request.form.get('capacity', 1))

    if end_time <= start_time:
        flash('Slot end time must be after start time.')
        return redirect(url_for('examiner.dashboard'))

    if capacity < 1:
        flash('Capacity must be at least 1.')
        return redirect(url_for('examiner.dashboard'))

    slot = Slot(
        exam_id=exam.id,
        examiner_id=current_user.id,
        start_time=start_time,
        end_time=end_time,
        capacity=capacity
    )
    db.session.add(slot)
    db.session.commit()
    flash('Slot created successfully.')
    return redirect(url_for('examiner.dashboard'))

@examiner_bp.route('/evaluate/<int:booking_id>', methods=['POST'])
@login_required
@examiner_required
def evaluate_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.slot.examiner_id != current_user.id:
        flash('Unauthorized: You can only evaluate your own booked students.')
        return redirect(url_for('examiner.dashboard'))

    if booking.status == 'cancelled':
        flash('Cannot evaluate a cancelled booking.')
        return redirect(url_for('examiner.dashboard'))

    # Sum marks across all rubric criteria for this exam
    total_marks = 0.0
    for crit in booking.slot.exam.criteria:
        score = float(request.form.get(f'crit_{crit.id}', 0))
        if score < 0 or score > crit.max_marks:
            flash(f'Marks for {crit.name} must be between 0 and {crit.max_marks}.')
            return redirect(url_for('examiner.dashboard'))
        total_marks += score

    remarks = request.form.get('remarks', '').strip()

    if booking.evaluation:
        booking.evaluation.total_marks = total_marks
        booking.evaluation.remarks = remarks
    else:
        eval_record = Evaluation(
            booking_id=booking.id,
            examiner_id=current_user.id,
            total_marks=total_marks,
            remarks=remarks
        )
        db.session.add(eval_record)

    booking.status = 'evaluated'
    db.session.commit()
    flash(f'Evaluation saved for {booking.student.username}.')
    return redirect(url_for('examiner.dashboard'))