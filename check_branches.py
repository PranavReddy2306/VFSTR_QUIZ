from app import app
from models import User
from app import db

with app.app_context():
    # Query student branches
    result = db.session.query(User.branch).filter(User.role == 'student').distinct().all()
    branches = [r[0] for r in result]
    print("Student Branches:", branches)
    
    # Check year-branch matrix
    result2 = db.session.query(User.year, User.branch).filter(User.role == 'student').distinct().all()
    print("\nYear-Branch Combinations:")
    for y, b in result2:
        print(f"  Year={y}, Branch={b}")
        
    # Check all students
    all_students = User.query.filter(User.role == 'student').all()
    print(f"\nTotal students: {len(all_students)}")
    if all_students:
        print("\nFirst 5 students:")
        for s in all_students[:5]:
            print(f"  {s.name} (Year: {s.year}, Branch: {s.branch})")
