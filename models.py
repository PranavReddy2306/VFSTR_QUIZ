
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class BaseModel(db.Model):
    __abstract__ = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class User(UserMixin, BaseModel):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False, default="student")  # manager | faculty | student
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    # Academic attributes (used for both faculty and students as needed)
    department = db.Column(db.String(50))  # e.g., CSE, ECE, EEE
    branch = db.Column(db.String(80), nullable=True)      # optional if you distinguish branch vs department
    section = db.Column(db.String(20), nullable=True)
    year = db.Column(db.String(10), nullable=True)

    # Student-only (nullable for others)
    roll_number = db.Column(db.String(50), unique=True, nullable=True)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Quiz(BaseModel):
    __tablename__ = 'quiz'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)

    # Targeting
    department = db.Column(db.String(80), nullable=False)
    branch = db.Column(db.String(80), nullable=False)
    section = db.Column(db.String(20), nullable=False)
    year = db.Column(db.String(10))  # 1,2,3,4

    # Ownership & timing
    faculty_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)

    # Access control
    start_password = db.Column(db.String(50), nullable=False)
    marks_per_question = db.Column(db.Integer, nullable=False, default=1)
    questions_per_student = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    questions = db.relationship('Question', backref='quiz', cascade='all,delete-orphan', lazy=True)


class Question(BaseModel):
    __tablename__ = 'question'
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.String(500), nullable=False)
    option_b = db.Column(db.String(500), nullable=False)
    option_c = db.Column(db.String(500), nullable=False)
    option_d = db.Column(db.String(500), nullable=False)
    correct = db.Column(db.String(1), nullable=False)  # 'A','B','C','D'
    image_url = db.Column(db.Text, nullable=True)


class Attempt(BaseModel):
    __tablename__ = 'attempt'
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime)
    score = db.Column(db.Integer, default=0)
    max_score = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20))  # in_progress, submitted, disqualified
    question_ids = db.Column(db.Text, nullable=True)


class Response(BaseModel):
    __tablename__ = 'response'
    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey('attempt.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    selected = db.Column(db.String(1), nullable=True)  # 'A','B','C','D' or None


class Result(BaseModel):
    __tablename__ = 'result'
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    student_name = db.Column(db.String(100), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    quiz = db.relationship('Quiz', backref=db.backref('results', lazy=True))

class Faculty(BaseModel):
    __tablename__ = 'faculty'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    department = db.Column(db.String(100))
    branch = db.Column(db.String(100))
    section = db.Column(db.String(100))
    year = db.Column(db.String(10))
    password = db.Column(db.String(100), default="password123")


class StudentQuizOverride(BaseModel):
    __tablename__ = 'student_quiz_override'
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    extended_end_time = db.Column(db.DateTime, nullable=True)
    reason = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    quiz = db.relationship('Quiz', backref=db.backref('student_overrides', cascade='all,delete-orphan', lazy=True))
    student = db.relationship('User', backref=db.backref('quiz_overrides', cascade='all,delete-orphan', lazy=True))


