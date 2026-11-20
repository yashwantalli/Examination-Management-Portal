from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from models import db, Exam, Slot, Booking

student_bp = Blueprint('student', __name__, url_prefix='/student')

def student_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'student':
            flash('Student access required.')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

@student_bp.route('/dashboard')
@login_required
@student_required
def dashboard():
    query = request.args.get('q', '').strip()
    if query:
        exams = Exam.query.filter(Exam.title.ilike(f'%{query}%')).all()
    else:
        exams = Exam.query.all()

    my_bookings = Booking.query.filter_by(student_id=current_user.id).order_by(Booking.created_at.desc()).all()
    return render_template('student/dashboard.html', exams=exams, bookings=my_bookings, query=query, now=datetime.now())

@student_bp.route('/book/<int:slot_id>', methods=['POST'])
@login_required
@student_required
def book_slot(slot_id):
    slot = Slot.query.get_or_404(slot_id)
    exam = slot.exam
    now = datetime.now()

    # 1. Enforce configured booking time window
    if not (exam.booking_start <= now <= exam.booking_end):
        flash('Booking is only allowed within the configured exam booking window.')
        return redirect(url_for('student.dashboard'))

    # 2. Prevent duplicate active booking for the same exam
    existing = (
        Booking.query.join(Slot)
        .filter(
            Booking.student_id == current_user.id,
            Slot.exam_id == exam.id,
            Booking.status != 'cancelled'
        )
        .first()
    )
    if existing:
        flash('You already have an active booking for this exam.')
        return redirect(url_for('student.dashboard'))

    # 3. Enforce slot capacity
    active_count = Booking.query.filter_by(slot_id=slot.id).filter(Booking.status != 'cancelled').count()
    if slot.is_full or active_count >= slot.capacity:
        slot.is_full = True
        db.session.commit()
        flash('This slot is already full.')
        return redirect(url_for('student.dashboard'))

    booking = Booking(student_id=current_user.id, slot_id=slot.id, status='booked')
    db.session.add(booking)

    # Mark slot full automatically if capacity reached
    if active_count + 1 >= slot.capacity:
        slot.is_full = True

    db.session.commit()
    flash('Slot booked successfully!')
    return redirect(url_for('student.dashboard'))

@student_bp.route('/cancel/<int:booking_id>', methods=['POST'])
@login_required
@student_required
def cancel_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.student_id != current_user.id:
        flash('Unauthorized.')
        return redirect(url_for('student.dashboard'))

    if booking.status != 'booked':
        flash('Only active un-evaluated bookings can be cancelled.')
        return redirect(url_for('student.dashboard'))

    if datetime.now() > booking.slot.exam.booking_end:
        flash('Cancellation deadline has passed.')
        return redirect(url_for('student.dashboard'))

    booking.status = 'cancelled'
    # Reopen slot capacity
    booking.slot.is_full = False
    db.session.commit()
    flash('Booking cancelled.')
    return redirect(url_for('student.dashboard'))