import sqlite3
import os

def migrate():
    db_paths = ["instance/quiz.db", "quiz.db"]
    for db_path in db_paths:
        if not os.path.exists(db_path):
            continue
        print(f"Checking database at {db_path}...")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check quiz table columns
        cursor.execute("PRAGMA table_info(quiz)")
        quiz_cols = [col[1] for col in cursor.fetchall()]
        if "questions_per_student" not in quiz_cols:
            print("Adding 'questions_per_student' column to 'quiz' table...")
            cursor.execute("ALTER TABLE quiz ADD COLUMN questions_per_student INTEGER DEFAULT NULL;")
            conn.commit()
            print("'questions_per_student' column added to 'quiz'.")
        else:
            print("'questions_per_student' column already exists in 'quiz'.")

        # Check attempt table columns
        cursor.execute("PRAGMA table_info(attempt)")
        attempt_cols = [col[1] for col in cursor.fetchall()]
        if "question_ids" not in attempt_cols:
            print("Adding 'question_ids' column to 'attempt' table...")
            cursor.execute("ALTER TABLE attempt ADD COLUMN question_ids TEXT DEFAULT NULL;")
            conn.commit()
            print("'question_ids' column added to 'attempt'.")
        else:
            print("'question_ids' column already exists in 'attempt'.")
            
        conn.close()
    print("Migration completed successfully.")

if __name__ == "__main__":
    migrate()
