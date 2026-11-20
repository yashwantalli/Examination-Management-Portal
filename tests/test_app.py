import unittest
from datetime import datetime, timedelta
from app import create_app
from models import db, User, Course, Exam, Slot, Booking

class EMPTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

        with self.app.app_context():
            db.drop_all()
            db.create_all()

            examiner = User(username='ex1', role='examiner', is_approved=True)
            examiner.set_password('pass')
            s1 = User(username='stu1', role='student', is_approved=True)
            s1.set_password('pass')
            s2 = User(username='stu2', role='student', is_approved=True)
            s2.set_password('pass')
            db.session.add_all([examiner, s1, s2])
            db.session.commit()

            course = Course(code='CS101', name='Intro to CS')
            db.session.add(course)
            db.session.commit()

            now = datetime.now()
            exam = Exam(
                course_id=course.id,
                title='Final Viva',
                booking_start=now - timedelta(hours=1),
                booking_end=now + timedelta(hours=5),
                examiner_id=examiner.id
            )
            db.session.add(exam)
            db.session.commit()

            slot = Slot(
                exam_id=exam.id,
                examiner_id=examiner.id,
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=1, hours=1),
                capacity=1
            )
            db.session.add(slot)
            db.session.commit()
            self.slot_id = slot.id

    def login(self, username, password):
        return self.client.post('/login', data={'username': username, 'password': password}, follow_redirects=True)

    def test_capacity_and_duplicate_booking_prevention(self):
        # Student 1 books the 1-capacity slot
        self.login('stu1', 'pass')
        res = self.client.post(f'/student/book/{self.slot_id}', follow_redirects=True)
        self.assertIn(b'Slot booked successfully!', res.data)

        # Student 1 tries duplicate booking -> blocked
        res_dup = self.client.post(f'/student/book/{self.slot_id}', follow_redirects=True)
        self.assertIn(b'already have an active booking', res_dup.data)
        self.client.get('/logout')

        # Student 2 tries booking the now-full slot -> blocked
        self.login('stu2', 'pass')
        res_full = self.client.post(f'/student/book/{self.slot_id}', follow_redirects=True)
        self.assertIn(b'already full', res_full.data)

if __name__ == '__main__':
    unittest.main()