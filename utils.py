import csv
import io
from datetime import datetime


def normalize_headers(row):
    """Helper: normalize CSV headers (lowercase, no spaces)."""
    return {k.strip().lower().replace(" ", "_"): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}


def parse_csv_faculty(file):
    """Parse uploaded faculty CSV and return list of dicts."""
    data = []
    stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
    reader = csv.DictReader(stream)
    for row in reader:
        r = normalize_headers(row)
        data.append({
            "name": r.get("name", ""),
            "email": r.get("email", ""),
            "department": r.get("department", ""),
            "branch": r.get("branch", ""),
            "section": r.get("section", ""),
            "password": r.get("password", "") or "password123"
        })
    return data


def parse_csv_students(file):
    """Parse uploaded student CSV and return list of dicts (auto-handles missing email/year)."""
    import re
    data = []
    stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
    reader = csv.DictReader(stream)
    for row in reader:
        r = normalize_headers(row)

        name = r.get("name", "")
        roll = r.get("roll_number", "") or r.get("rollno", "") or r.get("roll", "")
        email = r.get("email", "")

        # ✅ Auto-generate email if not in CSV
        if not email and roll:
            email = f"{roll.lower()}@student.vignan.ac.in"

        # ✅ Auto-generate year if not in CSV
        year = r.get("year", "").strip()
        if not year and roll:
            match = re.match(r'^(\d{2})', roll.strip())
            if match:
                join_year = int(match.group(1))
                current_year_suffix = 26  # Academic year 2026-2027
                years_diff = current_year_suffix - join_year
                if years_diff <= 0:
                    year = "1"
                elif years_diff == 1:
                    year = "2"
                elif years_diff == 2:
                    year = "3"
                elif years_diff == 3:
                    year = "4"
                else:
                    year = "Graduated"
            else:
                year = "1"

        data.append({
            "name": name,
            "email": email,
            "roll_number": roll,
            "department": r.get("department", ""),
            "branch": r.get("branch", ""),
            "section": r.get("section", ""),
            "year": year or "1",
            "password": r.get("password", "") or "password123"
        })
    return data


def parse_csv_questions(file):
    """Parse uploaded quiz questions CSV and return list of dicts (supports both MCQ and Coding types)."""
    data = []
    if isinstance(file, str):
        content = file
    elif hasattr(file, "stream"):
        raw = file.stream.read()
        content = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    elif hasattr(file, "read"):
        raw = file.read()
        content = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    else:
        content = str(file)

    stream = io.StringIO(content)
    reader = csv.DictReader(stream)
    for row in reader:
        if not row:
            continue
        r = normalize_headers(row)
        img = (
            r.get("image_url")
            or r.get("image_file")
            or r.get("diagram")
            or r.get("image")
            or r.get("diagram_url")
            or ""
        )
        q_type = (r.get("type") or r.get("q_type") or r.get("question_type") or "mcq").strip().lower()
        if q_type not in ("coding", "mcq"):
            q_type = "mcq"

        data.append({
            "q_type": q_type,
            "text": r.get("question", "") or r.get("text", ""),
            "A": r.get("a", "") or r.get("option_a", ""),
            "B": r.get("b", "") or r.get("option_b", ""),
            "C": r.get("c", "") or r.get("option_c", ""),
            "D": r.get("d", "") or r.get("option_d", ""),
            "correct": (r.get("correct", "") or "").strip().upper(),
            "image_url": img.strip(),
            "sample_input": r.get("sample_input", "") or r.get("sample_in", ""),
            "sample_output": r.get("sample_output", "") or r.get("sample_out", ""),
            "test_cases": r.get("test_cases", "") or r.get("hidden_test_cases", ""),
            "allowed_language": r.get("allowed_language", "") or r.get("languages", "c,cpp,python,java")
        })
    return data

