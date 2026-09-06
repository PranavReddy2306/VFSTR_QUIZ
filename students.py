import csv
from flask import Blueprint, request, redirect, url_for, flash, render_template
from werkzeug.utils import secure_filename
import os

from app import db
from models import Student  # Student model must be defined in models.py

students_bp = Blueprint("students", __name__)

UPLOAD_FOLDER = "uploads/students"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@students_bp.route("/upload_students", methods=["GET", "POST"])
def upload_students():
    if request.method == "POST":
        file = request.files.get("csv_file")
        if not file or file.filename == "":
            flash("No file selected", "danger")
            return redirect(request.url)

        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)

        # Parse CSV
        with open(filepath, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            required_cols = {"Student Name", "Student Email", "Branch", "Section", "Roll Number"}
            if not required_cols.issubset(reader.fieldnames):
                flash("CSV format is invalid. Must contain: Student Name, Student Email, Branch, Section, Roll Number", "danger")
                return redirect(request.url)

            for row in reader:
                student = Student(
                    name=row["Student Name"].strip(),
                    email=row["Student Email"].strip(),
                    branch=row["Branch"].strip(),
                    section=row["Section"].strip(),
                    roll_number=row["Roll Number"].strip()
                )
                db.session.add(student)
            db.session.commit()

        flash("Students uploaded successfully!", "success")
        return redirect(url_for("students.upload_students"))

    return render_template("upload_students.html")
