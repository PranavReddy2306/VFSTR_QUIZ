import sqlite3

def migrate():
    db_path = "instance/quiz.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if marks_per_question column exists in quiz table
    cursor.execute("PRAGMA table_info(quiz)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "marks_per_question" not in columns:
        print("Adding 'marks_per_question' column to 'quiz' table...")
        cursor.execute("ALTER TABLE quiz ADD COLUMN marks_per_question INTEGER DEFAULT 1;")
        conn.commit()
        print("'marks_per_question' column added successfully.")
    else:
        print("'marks_per_question' column already exists in 'quiz' table.")
        
    conn.close()
    print("Migration completed.")

if __name__ == "__main__":
    migrate()
