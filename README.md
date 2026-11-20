# Examination Management Portal (EMP)

A role-based **Examination Management Portal** built from scratch using **Python, Flask, Flask-SQLAlchemy, Flask-Login, SQLite, and Bootstrap 5**. The portal manages end-to-end viva and practical examination workflows across three distinct user roles—**Admin**, **Examiner**, and **Student**—with strict server-side validation for booking windows, slot capacities, duplicate-booking prevention, and rubric-based evaluations.

---

## Table of Contents
1. [Key Features & Roles](#key-features--roles)
2. [Business Rules & Server-Side Validation](#business-rules--server-side-validation)
3. [Project Structure](#project-structure)
4. [Setup & Installation](#setup--installation)
5. [Default Credentials](#default-credentials)
6. [Running Automated Tests](#running-automated-tests)

---

## Key Features & Roles

### 1. Admin
- **Examiner Approval:** Review self-registered Examiner accounts and approve access before they can log in.
- **Course & Exam Management:** Create courses and schedule exams with configured booking start and end windows.
- **Rubric Definition:** Add custom evaluation criteria with maximum marks per exam.
- **Examiner Assignment & Overrides:** Assign or reassign examiners to exams and reschedule active student bookings to alternative available slots.

### 2. Examiner
- **Assigned Exams:** View exams assigned by the Admin along with configured booking windows.
- **Slot Publishing:** Create examination time slots with defined start/end times and student capacities.
- **Rubric-Based Evaluation:** View students booked into their own slots, score each rubric criterion within valid limits, and submit feedback/remarks.

### 3. Student
- **Exam Discovery:** Search available examinations by title and inspect published slots and real-time seat availability.
- **Slot Booking & Cancellation:** Book available slots within configured windows or cancel active bookings before the deadline (automatically reopening slot capacity).
- **Results & History:** Track booking statuses (`booked`, `cancelled`, `evaluated`) and view finalized rubric marks and examiner feedback.

---

## Business Rules & Server-Side Validation
- **Role-Based Access Control:** Custom route decorators (`@admin_required`, `@examiner_required`, `@student_required`) enforce strict endpoint isolation.
- **Booking Window Enforcement:** Students can only book or cancel slots inside the exam's active `booking_start` and `booking_end` window.
- **Capacity & Full-Slot Automation:** Slots automatically lock (`is_full = True`) once active bookings reach capacity, preventing overbooking.
- **Duplicate Booking Prevention:** A student cannot hold more than one active (`booked` or `evaluated`) booking for the same examination.
- **Evaluation Ownership:** Examiners can only evaluate active bookings belonging to their own slots.

---

## Project Structure

```text
empp/
├── app.py                  # Flask application factory, LoginManager & default Admin seeding
├── config.py               # App configuration and SQLite database URI
├── models.py               # SQLAlchemy database models (User, Course, Exam, RubricCriterion, Slot, Booking, Evaluation)
├── requirements.txt        # Python dependencies
├── README.md               # Project documentation
├── .gitignore              # Git ignore rules (venv, __pycache__, *.db)
├── routes/
│   ├── __init__.py
│   ├── auth.py             # Registration, login, and logout routes
│   ├── admin.py            # Admin dashboard, approvals, courses/exams/rubrics, and overrides
│   ├── examiner.py         # Examiner dashboard, slot creation, and rubric evaluation
│   └── student.py          # Student dashboard, exam search, slot booking/cancellation, and results
├── templates/
│   ├── base.html           # Shared Bootstrap 5 layout and navigation bar
│   ├── auth/
│   │   ├── login.html
│   │   └── register.html
│   ├── admin/
│   │   └── dashboard.html
│   ├── examiner/
│   │   └── dashboard.html
│   └── student/
│       └── dashboard.html
└── tests/
    └── test_app.py         # Automated unit tests for capacity and duplicate booking enforcement
```

---

## Setup & Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/yashwantalli/empp.git](https://github.com/yashwantalli/empp.git)
   cd empp
   ```

2. **Create and activate a virtual environment:**
   - **Windows (PowerShell):**
     ```powershell
     python -m venv venv
     venv\Scripts\activate
     ```
   - **macOS / Linux:**
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the application:**
   ```bash
   python app.py
   ```
   Open `http://127.0.0.1:5000/` in your browser. The SQLite database (`instance/emp.db`) and default Admin account are created automatically on first launch.

---

## Default Credentials

| Role | Username | Password | Notes |
| :--- | :--- | :--- | :--- |
| **Admin** | `admin` | `admin123` | Auto-seeded on startup |
| **Examiner** | *(Self-register)* | *(User-defined)* | Requires Admin approval before login |
| **Student** | *(Self-register)* | *(User-defined)* | Approved immediately upon registration |

---

## Running Automated Tests

Run the server-side unit test suite (verifying capacity enforcement and duplicate-booking prevention) using:

```bash
python -m unittest discover tests
```