"""
fix_quiz_times.py

Usage:
  python fix_quiz_times.py --all
  python fix_quiz_times.py --id 3

This script will interpret existing Quiz.start_time and Quiz.end_time values
as UTC-naive datetimes and convert them to IST-naive datetimes by applying
UTC->Asia/Kolkata conversion and then storing the resulting IST datetime
(with tzinfo removed, to match the project's existing DB style).

It prints proposed changes and asks for confirmation before updating.
Make a DB backup before running.
"""
import sys
from app import app, IST
from models import Quiz, db
from zoneinfo import ZoneInfo
from datetime import timezone


def convert_naive_utc_to_ist(dt):
    # dt: naive datetime assumed to be UTC
    if dt is None:
        return None
    # attach UTC, convert to IST
    aware_utc = dt.replace(tzinfo=timezone.utc)
    ist = aware_utc.astimezone(IST)
    # return naive IST (strip tzinfo) to match current DB style
    return ist.replace(tzinfo=None)


def run_update(quiz_ids=None):
    with app.app_context():
        if quiz_ids is None:
            quizzes = Quiz.query.all()
        else:
            quizzes = Quiz.query.filter(Quiz.id.in_(quiz_ids)).all()
        if not quizzes:
            print("No quizzes found for the given ids.")
            return

        print(f"Found {len(quizzes)} quizzes. Proposed changes:")
        changes = []
        for q in quizzes:
            old_s = q.start_time
            old_e = q.end_time
            new_s = convert_naive_utc_to_ist(old_s)
            new_e = convert_naive_utc_to_ist(old_e)
            print(f"---\nQuiz id={q.id} title={q.title}")
            print(f"  old start: {old_s}  -> new start (IST): {new_s}")
            print(f"  old end:   {old_e}  -> new end (IST): {new_e}")
            changes.append((q, new_s, new_e))

        ans = input('\nApply these changes to the database? (yes/no): ').strip().lower()
        if ans not in ('yes', 'y'):
            print('Aborted. No changes made.')
            return

        for q, ns, ne in changes:
            q.start_time = ns
            q.end_time = ne
            db.session.add(q)
        db.session.commit()
        print('Updated quizzes successfully.')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python fix_quiz_times.py --all | --id <id>')
        sys.exit(1)
    if sys.argv[1] == '--all':
        run_update(None)
    elif sys.argv[1] == '--id' and len(sys.argv) >= 3:
        ids = [int(x) for x in sys.argv[2:]]
        run_update(ids)
    else:
        print('Unknown args')
        sys.exit(1)
