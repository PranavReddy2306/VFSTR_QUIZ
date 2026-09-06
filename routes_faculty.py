from flask import Blueprint, request, redirect, url_for, flash, render_template
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import os

from app import db
from models import User
from utils import parse_csv_students

faculty_bp = Blueprint("faculty", __name__, url_prefix="/faculty")

# ✅ Upload students CSV and add to DB
@faculty_bp.route("/upload_students", methods=["GET", "POST"])
@login_required
def upload_students():
    if current_user.role != "faculty":
        flash("Access denied!", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            flash("No file uploaded", "danger")
            return redirect(request.url)

        # Ensure it's a CSV
        if not file.filename.endswith(".csv"):
            flash("Please upload a CSV file", "danger")
            return redirect(request.url)

        try:
            students = parse_csv_students(file)
            created, skipped = 0, 0

            for s in students:
                # Check if email already exists
                existing = User.query.filter_by(email=s["email"]).first()
                if existing:
                    skipped += 1
                    continue

                # Create new student
                new_student = User(
                    name=s["name"],
                    email=s["email"],
                    branch=s["branch"],
                    section=s["section"],
                    year=s.get("year", "1"),
                    role="student"
                )
                new_student.set_password(s.get("password", "password123"))
                db.session.add(new_student)
                created += 1

            db.session.commit()
            flash(f"✅ {created} students added, {skipped} skipped (already exist).", "success")

        except Exception as e:
            flash(f"Error processing file: {e}", "danger")

        return redirect(url_for("faculty.upload_students"))

    return render_template("upload_students.html")
