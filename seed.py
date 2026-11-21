"""Optional demo data for local demonstration and the presentation video.

Run:  flask --app app seed-demo --reset
All demo passwords are listed in README.md. Remove/change them before any public deployment.
Historical records (past slots, evaluations) are inserted directly so that every
workflow can be demonstrated immediately; live actions in the UI go through services.py.
"""
from datetime import datetime, time, timedelta

from models import (Booking, BookingEvent, Course, Evaluation, EvaluationScore, ExaminerAssignment,
                    ExaminerProfile, Examination, RubricCriterion, Slot, StudentProfile, User, db, local_now)

DEMO_PASSWORD = "Demo@1234"


def _user(name, email, role, **profile):
    u = User.query.filter_by(email=email).first()
    if u:
        return u
    u = User(name=name, email=email, role=role, is_active=True)
    u.set_password(DEMO_PASSWORD)
    if role == "examiner":
        u.examiner_profile = ExaminerProfile(department=profile["department"],
                                             approval_status=profile.get("status", "approved"),
                                             contact_details=profile.get("contact"))
    elif role == "student":
        u.student_profile = StudentProfile(student_number=profile["number"], program=profile.get("program"),
                                           contact_details=profile.get("contact"))
    db.session.add(u)
    return u


def _slot(exam, examiner, day, start, minutes, capacity, venue):
    st = time(start[0], start[1])
    et = (datetime.combine(day, st) + timedelta(minutes=minutes)).time()
    s = Slot(examination=exam, examiner=examiner, exam_date=day, start_time=st, end_time=et,
             capacity=capacity, available_seats=capacity, venue=venue, status="open")
    db.session.add(s)
    return s


def _book(student, slot, booked_at, status="booked"):
    b = Booking(student=student, slot=slot, examination=slot.examination, status=status, booked_at=booked_at)
    slot.available_seats -= 1
    slot.status = "full" if slot.available_seats == 0 else "open"
    db.session.add(b)
    db.session.add(BookingEvent(booking=b, action="booked", actor=student, to_slot=slot, created_at=booked_at))
    return b


def _evaluate(booking, examiner, marks, remarks, at, published_at=None):
    ev = Evaluation(booking=booking, examiner=examiner, remarks=remarks, evaluated_at=at,
                    published_at=published_at, status="published" if published_at else "submitted")
    for crit, m in zip(booking.examination.criteria, marks):
        ev.scores.append(EvaluationScore(criterion=crit, marks_awarded=m))
    ev.total_marks = sum(marks)
    booking.status = "completed"
    db.session.add(ev)
    db.session.add(BookingEvent(booking=booking, action="evaluated", actor=examiner,
                                note=f"Marks submitted: {ev.total_marks:g}", created_at=at))
    if published_at:
        db.session.add(BookingEvent(booking=booking, action="published", note="Result published",
                                    created_at=published_at))
    return ev


def seed_demo():
    if Course.query.first():
        print("Demo data already present — use --reset to start again.")
        return
    now = local_now().replace(minute=0, second=0)
    today = now.date()

    # ---------------------------------------------------------------- people
    rao = _user("Dr. Kavya Rao", "kavya.rao@emp.local", "examiner", department="Computer Science",
                contact="Room B-204")
    mehta = _user("Prof. Arjun Mehta", "arjun.mehta@emp.local", "examiner", department="Computer Science",
                  contact="Room B-110")
    _user("Dr. Nisha Iyer", "nisha.iyer@emp.local", "examiner", department="Data Science", status="pending")
    aarav = _user("Aarav Sharma", "aarav@student.emp.local", "student", number="SE22UCSE001",
                  program="B.Tech CSE")
    others = [
        _user("Diya Patel", "diya@student.emp.local", "student", number="SE22UCSE014", program="B.Tech CSE"),
        _user("Rohan Verma", "rohan@student.emp.local", "student", number="SE22UCSE027", program="B.Tech CSE"),
        _user("Ananya Gupta", "ananya@student.emp.local", "student", number="SE22UCSE033", program="B.Tech AI"),
        _user("Kabir Singh", "kabir@student.emp.local", "student", number="SE22UCSE045", program="B.Tech CSE"),
        _user("Meera Nair", "meera@student.emp.local", "student", number="SE22UCSE052", program="B.Tech AI"),
    ]
    diya, rohan, ananya, kabir, meera = others

    # ---------------------------------------------------------------- courses
    dbms = Course(course_code="CS301", name="Database Management Systems",
                  description="Relational model, SQL, normalisation and transactions.")
    os_ = Course(course_code="CS302", name="Operating Systems", description="Processes, memory, file systems.")
    ml = Course(course_code="CS410", name="Machine Learning", description="Supervised and unsupervised learning.")
    web = Course(course_code="CS205", name="Web Application Development", description="Flask, HTML, CSS, REST.")
    db.session.add_all([dbms, os_, ml, web])

    def exam(course, name, etype, max_marks, pass_marks, minutes, sc, bk, criteria, status="active", cancel=24):
        e = Examination(course=course, name=name, exam_type=etype, maximum_marks=max_marks,
                        passing_marks=pass_marks, duration_minutes=minutes,
                        slot_creation_start=sc[0], slot_creation_end=sc[1],
                        booking_start=bk[0], booking_end=bk[1], cancellation_hours=cancel, status=status,
                        slot_creation_open=True, booking_open=True)
        for cname, cmax, w, desc in criteria:
            e.criteria.append(RubricCriterion(criterion_name=cname, maximum_marks=cmax, weightage=w,
                                              description=desc))
        db.session.add(e)
        return e

    dbms_viva = exam(dbms, "DBMS Mid-Semester Viva", "Viva", 50, 20, 20,
                     (now - timedelta(days=6), now + timedelta(days=5)),
                     (now - timedelta(days=5), now + timedelta(days=9)),
                     [("SQL & Queries", 15, 30, "Writing and explaining SQL queries"),
                      ("Normalisation", 10, 20, "Functional dependencies and normal forms"),
                      ("Transactions", 15, 30, "ACID, concurrency control"),
                      ("Communication", 10, 20, "Clarity of explanation")])
    os_lab = exam(os_, "OS Lab Practical", "Practical", 40, 16, 45,
                  (now - timedelta(days=1), now + timedelta(days=6)),
                  (now + timedelta(days=4), now + timedelta(days=12)),
                  [("Implementation", 20, 50, "Working program"), ("Viva", 10, 25, "Concept questions"),
                   ("Code quality", 10, 25, "Readability and structure")])
    ml_review = exam(ml, "ML Project Final Review", "Project Review", 100, 40, 30,
                     (now - timedelta(days=30), now - timedelta(days=20)),
                     (now - timedelta(days=25), now - timedelta(days=12)),
                     [("Problem & Data", 20, 20, ""), ("Model & Experiments", 40, 40, ""),
                      ("Results & Analysis", 25, 25, ""), ("Presentation", 15, 15, "")], status="completed")
    web_oral = exam(web, "Web Dev Oral Assessment", "Oral", 30, 12, 15,
                    (now - timedelta(days=4), now + timedelta(days=2)),
                    (now - timedelta(days=3), now + timedelta(days=8)),
                    [("Flask & Routing", 10, 34, ""), ("Templates & Bootstrap", 10, 33, ""),
                     ("Database & API", 10, 33, "")], cancel=12)
    db.session.flush()

    for e, examiners in ((dbms_viva, [rao, mehta]), (os_lab, [mehta]), (ml_review, [rao]), (web_oral, [rao, mehta])):
        for x in examiners:
            db.session.add(ExaminerAssignment(examination=e, examiner=x))

    # ---------------------------------------------------------------- slots
    yday = today - timedelta(days=1)
    s_dbms_past = _slot(dbms_viva, rao, yday, (10, 0), 60, 4, "Lab B-201")
    s_dbms_1 = _slot(dbms_viva, rao, today + timedelta(days=2), (10, 0), 60, 4, "Lab B-201")
    s_dbms_2 = _slot(dbms_viva, mehta, today + timedelta(days=3), (14, 0), 60, 3, "Lab B-105")
    _slot(dbms_viva, mehta, today + timedelta(days=4), (11, 0), 40, 2, "Lab B-105")
    _slot(os_lab, mehta, today + timedelta(days=7), (9, 30), 90, 6, "OS Lab C-3")
    _slot(os_lab, mehta, today + timedelta(days=8), (9, 30), 90, 6, "OS Lab C-3")
    s_ml = _slot(ml_review, rao, today - timedelta(days=10), (11, 0), 120, 5, "Seminar Hall 2")
    s_web_full = _slot(web_oral, mehta, today + timedelta(days=3), (16, 0), 30, 2, "Room A-12")
    s_web_open = _slot(web_oral, rao, today + timedelta(days=4), (15, 0), 45, 3, "Room A-14")
    db.session.flush()

    # ---------------------------------------------------------------- bookings
    t = lambda d, h=10: now - timedelta(days=d, hours=h % 7)
    b1 = _book(diya, s_dbms_past, t(4))
    b2 = _book(rohan, s_dbms_past, t(4, 3))
    _book(ananya, s_dbms_past, t(3))
    _book(kabir, s_dbms_1, t(2))
    _book(meera, s_dbms_2, t(1))
    _book(kabir, s_web_full, t(2, 4))
    _book(meera, s_web_full, t(1, 2))
    _book(aarav, s_web_open, t(0, 3))
    ml_bookings = [_book(s, s_ml, t(d)) for s, d in ((aarav, 22), (diya, 21), (rohan, 20), (ananya, 18))]

    # cancelled booking history example
    cb = _book(diya, s_web_open, t(5))
    cb.status, cb.cancelled_at, cb.cancel_reason = "cancelled", t(4), "Cancelled by student"
    s_web_open.available_seats += 1
    s_web_open.status = "open"
    db.session.add(BookingEvent(booking=cb, action="cancelled", actor=diya, from_slot=s_web_open,
                                note="Cancelled by student", created_at=t(4)))

    # ---------------------------------------------------------------- evaluations
    _evaluate(b1, rao, [13, 8, 12, 9], "Strong SQL skills; revise BCNF.", now - timedelta(hours=20))
    pub = now - timedelta(days=8)
    for bk, marks, rem in zip(ml_bookings,
                              ([17, 33, 21, 13], [15, 28, 18, 11], [12, 20, 14, 9], [18, 36, 22, 14]),
                              ("Well-structured project with thorough experiments.",
                               "Good baseline; analysis could go deeper.",
                               "Model choice needs justification.", "Excellent end-to-end work.")):
        _evaluate(bk, rao, marks, rem, now - timedelta(days=9), published_at=pub)
    db.session.commit()
    _ = b2  # rohan & ananya remain pending evaluation for the examiner demo
