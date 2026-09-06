import sqlite3
import re

def migrate():
    db_path = "instance/quiz.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. Check if year column exists in user table
    cursor.execute("PRAGMA table_info(user)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "year" not in columns:
        print("Adding 'year' column to 'user' table...")
        cursor.execute("ALTER TABLE user ADD COLUMN year VARCHAR(10);")
        conn.commit()
        print("'year' column added successfully.")
    else:
        print("'year' column already exists in 'user' table.")
        
    # 2. Update existing student years based on their roll number
    cursor.execute("SELECT id, roll_number FROM user WHERE role = 'student'")
    students = cursor.fetchall()
    
    updated = 0
    current_year_suffix = 26 # Academic year 2026-2027
    
    for student_id, roll in students:
        if not roll:
            continue
        # Extract the first 2 digits of the roll number
        match = re.match(r'^(\d{2})', roll.strip())
        if match:
            join_year = int(match.group(1))
            years_diff = current_year_suffix - join_year
            if years_diff < 0:
                year_str = "1"
            elif years_diff == 0:
                year_str = "1"
            elif years_diff == 1:
                year_str = "2"
            elif years_diff == 2:
                year_str = "3"
            elif years_diff == 3:
                year_str = "4"
            else:
                year_str = "Graduated"
        else:
            year_str = "1" # default
            
        cursor.execute("UPDATE user SET year = ? WHERE id = ?", (year_str, student_id))
        updated += 1
        
    conn.commit()
    conn.close()
    print(f"Migration completed. Updated {updated} students' year values.")

if __name__ == "__main__":
    migrate()
