import csv
from io import TextIOWrapper
from flask import Blueprint, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Quiz, Question

bp = Blueprint("upload", __name__)

def parse_csv_questions(file_stream):
    """Parse uploaded CSV into a list of questions"""
    questions = []
    wrapper = TextIOWrapper(file_stream, encoding='utf-8')
    reader = csv.DictReader(wrapper)

    for row in reader:
        q_text = row.get("question", "").strip()
        a = row.get("option_a", "").strip()
        b = row.get("option_b", "").strip()
        c = row.get("option_c", "").strip()
        d = row.get("option_d", "").strip()
        correct = row.get("correct", "").strip().upper()

        if q_text and a and b and c and d and correct in ["A", "B", "C", "D"]:
            questions.append({
                "text": q_text,
                "A": a,
                "B": b,
                "C": c,
                "D": d,
                "correct": correct
            })

    return questions


@bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload_quiz():
    if request.method == "POST":
        title = request.form["title"]
        branch = request.form["branch"]
        section = request.form["section"]
        start_time = request.form["start_time"]
        end_time = request.form["end_time"]
        start_password = request.form["start_password"]

        file = request.files.get("csv")
        if not file or not file.filename.endswith(".csv"):
            flash("Please upload a valid CSV file.", "danger")
            return redirect(url_for("upload.upload_quiz"))

        questions = parse_csv_questions(file.stream)

        if not questions:
            flash("No valid questions found in CSV.", "danger")
            return redirect(url_for("upload.upload_quiz"))

        # Create quiz
        quiz = Quiz(
            title=title,
            branch=branch,
            section=section,
            start_time=start_time,
            end_time=end_time,
            start_password=start_password,
            faculty_id=current_user.id
        )
        db.session.add(quiz)
        db.session.flush()  # ensures quiz.id is available

        # Add questions
        for q in questions:
            question = Question(
                quiz_id=quiz.id,
                text=q["text"],
                option_a=q["A"],
                option_b=q["B"],
                option_c=q["C"],
                option_d=q["D"],
                correct=q["correct"]
            )
            db.session.add(question)

        db.session.commit()
        flash("Quiz created successfully!", "success")
        return redirect(url_for("faculty.dashboard"))

    return """
    <h2>Upload Quiz (CSV)</h2>
    <form method="post" enctype="multipart/form-data">
      <label>Title: <input name="title"></label><br>
      <label>Branch: <input name="branch"></label><br>
      <label>Section: <input name="section"></label><br>
      <label>Start Time: <input type="datetime-local" name="start_time"></label><br>
      <label>End Time: <input type="datetime-local" name="end_time"></label><br>
      <label>Start Password: <input name="start_password"></label><br>
      <label>CSV File: <input type="file" name="csv" accept=".csv"></label><br>
      <button type="submit">Upload</button>
    </form>
    """
