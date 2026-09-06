# clear_students.py
from app import create_app, db
from models import User

# Create app context
app = create_app()

with app.app_context():
    deleted = User.query.filter_by(role="student").delete()
    db.session.commit()
    print(f"✅ Deleted {deleted} student(s).")
