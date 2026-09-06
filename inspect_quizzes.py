from app import app
from models import Quiz

with app.app_context():
    qs = Quiz.query.order_by(Quiz.id.asc()).all()
    print(f"Found {len(qs)} quizzes")
    for q in qs:
        st = q.start_time
        et = q.end_time
        print("---")
        print("id:", q.id)
        print("title:", q.title)
        print("start_time repr:", repr(st))
        print("start_time tzinfo:", getattr(st, 'tzinfo', None))
        print("end_time repr:", repr(et))
        print("end_time tzinfo:", getattr(et, 'tzinfo', None))
        try:
            from app import IST
            print("start_time as IST:", st.astimezone(IST))
            print("end_time as IST:", et.astimezone(IST))
        except Exception as e:
            print("couldn't convert to IST:", e)
    
print("Done")
