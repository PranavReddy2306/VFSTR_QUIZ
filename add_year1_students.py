from app import app
from models import User
from app import db

with app.app_context():
    # Create 5 Year 1 BCA students
    year1_bca_students = [
        ("26FA04001", "Pranav Reddy G", "CSE"),
        ("26FA04002", "John Doe", "BCA"),
        ("26FA04003", "Jane Smith", "BCA"),
        ("26FA04004", "Alice Johnson", "BCA"),
        ("26FA04005", "Bob Wilson", "BCA"),
    ]
    
    for roll, name, branch in year1_bca_students:
        existing = User.query.filter_by(roll_number=roll).first()
        if existing:
            print(f"  Skipping {roll} (already exists)")
            continue
            
        email = f"{roll.lower()}@student.vignan.ac.in"
        u = User(
            name=name,
            roll_number=roll,
            email=email,
            role="student",
            department="DCMS",
            branch=branch,
            section="A",
            year="1"
        )
        u.set_password("password123")
        db.session.add(u)
        print(f"  Added {name} ({roll})")
    
    db.session.commit()
    print("\n✅ Year 1 BCA students created successfully!")
    
    # Verify
    result = db.session.query(User.year, User.branch).filter(User.role == 'student', User.year == '1').distinct().all()
    print("\nYear 1 branches now:")
    for y, b in result:
        count = User.query.filter_by(role='student', year=y, branch=b).count()
        print(f"  Year {y}, Branch {b}: {count} students")
