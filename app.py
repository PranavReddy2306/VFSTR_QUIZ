from flask import Flask, render_template, redirect, url_for, request, flash, abort, send_file
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import random
import io, csv, os, time
from io import StringIO
from sqlalchemy import or_

# Local imports
from models import db, User, Quiz, Question, Attempt, Response, StudentQuizOverride
from utils import parse_csv_questions, parse_csv_students, parse_csv_faculty
from config import DevConfig

# Define IST timezone (UTC+5:30)
IST = ZoneInfo("Asia/Kolkata")
SERVER_TZ=ZoneInfo(os.environ.get("TZ","UTC"))
try:
    time.tzset()
except Exception:
    pass
def convert_to_server_time(dt):
    """Convert a datetime to server's local timezone."""
    if dt is None:
        return None
    # If a string was passed (from form inputs), try to parse it.
    if isinstance(dt, str):
        # Common HTML datetime-local format: 'YYYY-MM-DDTHH:MM' or ISO variants
        try:
            # datetime.fromisoformat handles 'T' separator and optional seconds
            dt = datetime.fromisoformat(dt)
        except Exception:
            # Fallback to a couple of common formats
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
                try:
                    dt = datetime.strptime(dt, fmt)
                    break
                except Exception:
                    dt = None
            if dt is None:
                raise ValueError(f"Unrecognized datetime format: {dt}")
    # At this point dt should be a datetime object
    if dt.tzinfo is None:
        # Assume incoming times are in IST (application/user timezone)
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(SERVER_TZ)
def convert_to_ist(dt):
    """Convert a datetime to IST timezone."""
    if dt is None:
        return None
    # If a string was passed, try to parse it first
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
                try:
                    dt = datetime.strptime(dt, fmt)
                    break
                except Exception:
                    dt = None
            if dt is None:
                raise ValueError(f"Unrecognized datetime format: {dt}")
    # Treat naive datetimes as IST (app is single-timezone IST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)

def ensure_ist(dt):
    """Ensure datetime is timezone-aware in IST."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def get_effective_quiz_end_time(quiz, student_id):
    """Returns the effective IST end time for a student (checking StudentQuizOverride)."""
    if not quiz:
        return None
    if student_id:
        override = StudentQuizOverride.query.filter_by(quiz_id=quiz.id, student_id=student_id).first()
        if override and override.extended_end_time:
            return convert_to_ist(override.extended_end_time)
    return convert_to_ist(quiz.end_time)



def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(DevConfig)
    db.init_app(app)

    # --- Auth ---
    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    def resolve_image_url(url):
        if not url or not str(url).strip():
            return None
        url = str(url).strip()
        if url.startswith(("http://", "https://", "/static/")):
            return url
        if url.startswith("/"):
            return url
        return url_for("serve_diagram", filename=url)

    @app.route("/diagrams/<path:filename>")
    def serve_diagram(filename):
        from flask import send_from_directory
        clean_name = os.path.basename(filename).lower()
        
        # 1. Search in static/uploads/quiz_diagrams recursively
        diagrams_dir = os.path.join(app.static_folder, "uploads", "quiz_diagrams")
        if os.path.exists(diagrams_dir):
            for root, dirs, files in os.walk(diagrams_dir):
                for f in files:
                    if f.lower() == clean_name:
                        return send_from_directory(root, f)
                        
        # 2. Search directly in static/ or static subfolders
        if os.path.exists(os.path.join(app.static_folder, filename)):
            return send_from_directory(app.static_folder, filename)
        for root, dirs, files in os.walk(app.static_folder):
            for f in files:
                if f.lower() == clean_name:
                    return send_from_directory(root, f)
                    
        # 3. Search in project root directory (e.g. Math.png)
        if os.path.exists(os.path.join(app.root_path, filename)):
            return send_from_directory(app.root_path, filename)
        for f in os.listdir(app.root_path):
            if f.lower() == clean_name:
                return send_from_directory(app.root_path, f)
                
        abort(404)

    app.jinja_env.globals['resolve_image_url'] = resolve_image_url

    # Helper: Get assigned questions for attempt (or generate randomized subset if not set)
    def get_attempt_questions(quiz, attempt=None):
        all_questions = Question.query.filter_by(quiz_id=quiz.id).all()
        if not all_questions:
            return []

        if attempt and attempt.question_ids:
            id_strings = [i.strip() for i in attempt.question_ids.split(",") if i.strip()]
            try:
                stored_ids = [int(i) for i in id_strings]
                q_map = {q.id: q for q in all_questions}
                assigned_questions = [q_map[qid] for qid in stored_ids if qid in q_map]
                if assigned_questions:
                    return assigned_questions
            except Exception:
                pass

        # If question_ids not set or invalid, select questions now
        q_count = quiz.questions_per_student
        if q_count and 0 < q_count < len(all_questions):
            selected_questions = random.sample(all_questions, q_count)
        else:
            selected_questions = list(all_questions)
            random.shuffle(selected_questions)

        if attempt:
            attempt.question_ids = ",".join(str(q.id) for q in selected_questions)
            attempt.max_score = len(selected_questions) * (quiz.marks_per_question or 1)
            db.session.add(attempt)
            db.session.commit()

        return selected_questions

    # Helper: finalize expired attempts for a student (mark in_progress -> submitted)
    def finalize_expired_attempts_for_student(student_id):
        now_ist = datetime.now(IST)
        in_progress_attempts = Attempt.query.filter_by(student_id=student_id, status="in_progress").all()
        updated = False
        for attempt in in_progress_attempts:
            quiz = db.session.get(Quiz, attempt.quiz_id)
            if not quiz:
                continue
            quiz_end_ist = get_effective_quiz_end_time(quiz, student_id)
            if quiz_end_ist and quiz_end_ist <= now_ist:
                # grade and finalize the attempt using any saved responses
                questions = get_attempt_questions(quiz, attempt)
                responses = {r.question_id: r for r in Response.query.filter_by(attempt_id=attempt.id).all()}
                score = 0
                for q in questions:
                    r = responses.get(q.id)
                    if r and r.selected and r.selected.strip().upper() == (q.correct or "").strip().upper():
                        score += 1
                attempt.score = score * quiz.marks_per_question
                attempt.max_score = len(questions) * quiz.marks_per_question
                attempt.status = "submitted"
                # store submitted_at as naive datetime (DB uses naive datetimes);
                # prefer quiz end time (naive) when available else use now
                submitted_dt = (quiz_end_ist or now_ist)
                # remove tzinfo before storing to keep consistency with existing DB rows
                try:
                    attempt.submitted_at = submitted_dt.replace(tzinfo=None)
                except Exception:
                    attempt.submitted_at = submitted_dt
                db.session.add(attempt)
                updated = True
        if updated:
            db.session.commit()

    # Helper: finalize expired attempts for a specific quiz (used before exporting results)
    def finalize_expired_attempts_for_quiz(quiz_id):
        now_ist = datetime.now(IST)
        quiz = db.session.get(Quiz, quiz_id)
        if not quiz:
            return
        in_progress_attempts = Attempt.query.filter_by(quiz_id=quiz.id, status="in_progress").all()
        updated = False
        for attempt in in_progress_attempts:
            quiz_end_ist = get_effective_quiz_end_time(quiz, attempt.student_id)
            if not quiz_end_ist or quiz_end_ist > now_ist:
                continue
            questions = get_attempt_questions(quiz, attempt)
            responses = {r.question_id: r for r in Response.query.filter_by(attempt_id=attempt.id).all()}
            score = 0
            for q in questions:
                r = responses.get(q.id)
                if r and r.selected and r.selected.strip().upper() == (q.correct or "").strip().upper():
                    score += 1
            attempt.score = score * quiz.marks_per_question
            attempt.max_score = len(questions) * quiz.marks_per_question
            attempt.status = "submitted"
            # store naive submitted_at consistent with DB
            try:
                attempt.submitted_at = quiz_end_ist.replace(tzinfo=None)
            except Exception:
                attempt.submitted_at = quiz_end_ist
            db.session.add(attempt)
            updated = True
        if updated:
            db.session.commit()

    # ================================
    # Authentication
    # ================================
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            identifier = request.form.get("identifier")
            password = request.form.get("password")

            user = User.query.filter_by(email=identifier).first()
            if not user:
                user = User.query.filter_by(roll_number=identifier, role="student").first()

            if user and user.check_password(password):
                login_user(user)
                flash("✅ Logged in successfully.", "success")
                if user.role == "manager":
                    return redirect(url_for("manager_dashboard"))
                elif user.role == "faculty":
                    return redirect(url_for("faculty_dashboard"))
                else:
                    return redirect(url_for("student_dashboard"))
            flash("❌ Invalid credentials", "danger")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("Logged out.", "info")
        return redirect(url_for("login"))

    @app.route("/auth/firebase-login", methods=["POST"])
    def firebase_login():
        from firebase_config import verify_firebase_id_token
        from flask import jsonify

        data = request.get_json() or {}
        id_token = data.get("id_token")

        if not id_token:
            return jsonify({"success": False, "message": "Missing ID token"}), 400

        try:
            decoded = verify_firebase_id_token(id_token)
            email = decoded.get("email")
            name = decoded.get("name") or (email.split("@")[0] if email else "Firebase User")

            if not email:
                return jsonify({"success": False, "message": "Email not found in Firebase token"}), 400

            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(
                    name=name,
                    email=email,
                    role="student"
                )
                user.set_password("FirebaseAuthUser123!")
                db.session.add(user)
                db.session.commit()

            login_user(user)

            if user.role == "manager":
                target_url = url_for("manager_dashboard")
            elif user.role == "faculty":
                target_url = url_for("faculty_dashboard")
            else:
                target_url = url_for("student_dashboard")

            return jsonify({"success": True, "redirect_url": target_url})

        except Exception as e:
            return jsonify({"success": False, "message": f"Firebase auth error: {str(e)}"}), 401

    @app.route("/")
    def home():
        if current_user.is_authenticated:
            if current_user.role == "manager":
                return redirect(url_for("manager_dashboard"))
            elif current_user.role == "faculty":
                return redirect(url_for("faculty_dashboard"))
            return redirect(url_for("student_dashboard"))
        return redirect(url_for("login"))

    # ================================
    # Manager Dashboard & Uploads
    # ================================
    @app.route("/manager")
    @login_required
    def manager_dashboard():
        if current_user.role != "manager":
            abort(403)
        faculty_members = User.query.filter_by(role="faculty").all()
        students = User.query.filter_by(role="student").all()
        
        # Unique attributes for promotion filtering
        departments = [r[0] for r in db.session.query(User.department).filter(User.role == 'student', User.department != None, User.department != '').distinct().all()]
        branches = [r[0] for r in db.session.query(User.branch).filter(User.role == 'student', User.branch != None, User.branch != '').distinct().all()]
        sections = [r[0] for r in db.session.query(User.section).filter(User.role == 'student', User.section != None, User.section != '').distinct().all()]
        years = [r[0] for r in db.session.query(User.year).filter(User.role == 'student', User.year != None, User.year != '').distinct().all()]
        
        departments.sort()
        branches.sort()
        sections.sort()
        
        # Sort years numerically/logically
        custom_year_order = {"1": 1, "2": 2, "3": 3, "4": 4, "Graduated": 5}
        years.sort(key=lambda y: custom_year_order.get(y, 99))        # Query distinct year and branch counts for students
        from sqlalchemy import func
        count_results = db.session.query(
            User.year, User.branch, func.count(User.id)
        ).filter(User.role == "student").group_by(User.year, User.branch).all()
        
        matrix = {}
        matrix_years = set()
        matrix_branches = set()
        for yr, br, count in count_results:
            y = yr or "N/A"
            b = br or "N/A"
            matrix_years.add(y)
            matrix_branches.add(b)
            if y not in matrix:
                matrix[y] = {}
            matrix[y][b] = count

        matrix_branches_list = sorted(list(matrix_branches))
        matrix_years_list = sorted(list(matrix_years), key=lambda y: custom_year_order.get(y, 99))

        return render_template(
            "manager_dashboard.html",
            faculty=faculty_members,
            student_matrix=matrix,
            matrix_years=matrix_years_list,
            matrix_branches=matrix_branches_list,
            total_students_count=len(students),
            departments=departments,
            branches=branches,
            sections=sections,
            years=years
        )

    @app.route("/manager/clear_faculty")
    @login_required
    def clear_faculty():
        if current_user.role != "manager":
            abort(403)
        count = User.query.filter_by(role="faculty").delete()
        db.session.commit()
        flash(f"✅ Deleted {count} faculty accounts.", "success")
        return redirect(url_for("manager_dashboard"))

    @app.route("/manager/clear_quizzes")
    @login_required
    def clear_quizzes():
        if current_user.role != "manager":
            abort(403)
        Response.query.delete()
        Attempt.query.delete()
        Question.query.delete()
        Quiz.query.delete()
        db.session.commit()
        flash("🧹 All quizzes cleared successfully!", "success")
        return redirect(url_for("manager_dashboard"))

    @app.route("/manager/clear_old_quizzes")
    @login_required
    def clear_old_quizzes():
        if current_user.role != "manager":
            abort(403)
        now = datetime.now(IST)
        expired_quizzes = Quiz.query.filter(Quiz.end_time < now).all()
        count = 0
        for quiz in expired_quizzes:
            Attempt.query.filter_by(quiz_id=quiz.id).delete()
            Question.query.filter_by(quiz_id=quiz.id).delete()
            db.session.delete(quiz)
            count += 1
        db.session.commit()
        flash(f"🧹 {count} expired quizzes deleted successfully!", "success")
        return redirect(url_for("manager_dashboard"))
    
    @app.route("/manager/create_faculty", methods=["GET", "POST"])
    @login_required
    def manager_create_faculty():
        if current_user.role != "manager":
            abort(403)
        if request.method == "POST":
            name = request.form.get("name")
            email = request.form.get("email")
            department = request.form.get("department")
            branch = request.form.get("branch")
            section = request.form.get("section")
            year = request.form.get("year")
            password = request.form.get("password")
            
            if not all([name, email, password]):
                flash("❌ Name, Email, and Password are required", "danger")
                return redirect(request.url)
                
            existing = User.query.filter_by(email=email).first()
            if existing:
                flash("❌ A user with this email already exists", "danger")
                return redirect(request.url)
                
            u = User(
                name=name,
                email=email,
                role="faculty",
                department=department or None,
                branch=branch or None,
                section=section or None,
                year=year or None
            )
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            flash("✅ Faculty member added successfully!", "success")
            return redirect(url_for("manager_dashboard"))
            
        return render_template("create_faculty.html")

    @app.route("/manager/upload_faculty", methods=["GET", "POST"])
    @login_required
    def manager_upload_faculty():
        if request.method == "POST":
            if current_user.role != "manager":
                abort(403)

            file = request.files.get("file")
            if not file or file.filename == "":
                flash("❌ Please upload a CSV file", "danger")
                return redirect(url_for('manager_dashboard'))
            try:
                faculty_list = parse_csv_faculty(file)
                flash('Faculty data uploaded successfully.', 'success')
            except Exception as e:
                flash(f"❌ Failed to parse CSV: {e}", "danger")
                return redirect(request.url)

            added, updated, skipped = 0, 0, 0
            for f in faculty_list:
                email = (f.get("email") or "").strip()
                if not email:
                    skipped += 1
                    continue

                existing = User.query.filter_by(email=email).first()
                if existing:
                    existing.name = f.get("name", "").strip() or existing.name
                    existing.department = f.get("department", "").strip() or existing.department
                    existing.branch = f.get("branch", "").strip() or existing.branch
                    existing.section = f.get("section", "").strip() or existing.section
                    existing.set_password(f.get("password", "password123"))
                    updated += 1
                else:
                    u = User(
                        name=f.get("name", "").strip() or "Faculty",
                        email=email,
                        role="faculty",
                        department=f.get("department", "") or None,
                        branch=f.get("branch", "") or None,
                        section=f.get("section", "") or None,
                    )
                    u.set_password(f.get("password", "password123"))
                    db.session.add(u)
                    added += 1

            db.session.commit()
            flash(f"✅ {added} faculty added, 🔁 {updated} updated, ❌ {skipped} skipped", "success")
            return redirect(url_for("manager_dashboard"))
        return render_template("upload_faculty.html")


    @app.route('/quiz/template')
    @login_required
    def download_quiz_template():
        """Provide a simple CSV template for quiz uploads: Question,A,B,C,D,Correct"""
        if current_user.role not in ("faculty", "manager"):
            abort(403)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Question", "A", "B", "C", "D", "Correct"])
        # Add an example row
        writer.writerow(["Which is the capital of India?", "Mumbai", "Delhi", "Bengaluru", "Kolkata", "B"])
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            as_attachment=True,
            download_name="quiz_template.csv",
            mimetype="text/csv",
        )

    @app.route('/students/template')
    @login_required
    def download_students_template():
        """Provide a simple CSV template for student uploads"""
        if current_user.role not in ("faculty", "manager"):
            abort(403)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Roll Number", "Name", "Email", "Department", "Branch", "Section", "Year", "Password"])
        # Add sample records
        writer.writerow(["22FA04001", "Gadde Pranav", "22fa04001@student.vignan.ac.in", "CSE", "CSE", "A", "3", "password123"])
        writer.writerow(["23FA04002", "Bolla Sai", "", "ECE", "ECE", "B", "", ""])
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            as_attachment=True,
            download_name="students_template.csv",
            mimetype="text/csv",
        )

    @app.route("/manager/create_student", methods=["GET", "POST"])
    @login_required
    def manager_create_student():
        if current_user.role != "manager":
            abort(403)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            roll_number = request.form.get("roll_number", "").strip()
            email = request.form.get("email", "").strip()
            department = request.form.get("department", "").strip()
            branch = request.form.get("branch", "").strip()
            section = request.form.get("section", "").strip()
            year = request.form.get("year", "").strip()
            password = request.form.get("password", "").strip()
            
            if not name or not roll_number:
                flash("❌ Name and Roll Number are required", "danger")
                return redirect(request.url)
                
            # Auto-generate email if left blank
            if not email:
                email = f"{roll_number.lower()}@student.vignan.ac.in"
                
            # Auto-generate year if left blank
            if not year:
                import re
                match = re.match(r'^(\d{2})', roll_number)
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
                    
            # Default password if left blank
            if not password:
                password = "password123"
                
            # Check unique email and roll number
            existing_email = User.query.filter_by(email=email).first()
            if existing_email:
                flash("❌ A user with this email already exists", "danger")
                return redirect(request.url)
                
            existing_roll = User.query.filter_by(roll_number=roll_number).first()
            if existing_roll:
                flash("❌ A user with this roll number already exists", "danger")
                return redirect(request.url)
                
            u = User(
                name=name,
                roll_number=roll_number,
                email=email,
                role="student",
                department=department or None,
                branch=branch or None,
                section=section or None,
                year=year
            )
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            flash("✅ Student account added successfully!", "success")
            return redirect(url_for("manager_dashboard"))
            
        return render_template("create_student.html")

    @app.route("/manager/students")
    @login_required
    def manager_students_hub():
        if current_user.role != "manager":
            abort(403)
        # Find first student to redirect to their year/branch
        first_student = User.query.filter(User.role == "student", User.year != None, User.branch != None).first()
        if first_student:
            return redirect(url_for("manager_students_list", year=first_student.year, branch=first_student.branch))
        # Fallback to any student if year or branch is missing
        fallback_student = User.query.filter_by(role="student").first()
        if fallback_student:
            year = fallback_student.year or "1"
            branch = fallback_student.branch or "N/A"
            return redirect(url_for("manager_students_list", year=year, branch=branch))
            
        flash("ℹ️ No students found in the database. Please add or upload students first.", "info")
        return redirect(url_for("manager_dashboard"))

    @app.route("/manager/students/<year>/<branch>")
    @login_required
    def manager_students_list(year, branch):
        if current_user.role != "manager":
            abort(403)
            
        # Standardize branch 'N/A' query
        if branch == "N/A":
            students = User.query.filter(User.role == "student", User.year == year, (User.branch == None) | (User.branch == '')).all()
        else:
            students = User.query.filter_by(role="student", year=year, branch=branch).all()
            
        # Group students by section
        from collections import defaultdict
        grouped = defaultdict(list)
        for s in students:
            sec = s.section or 'N/A'
            grouped[sec].append(s)
            
        # Sort sections and their student lists
        sorted_grouped = {}
        for sec in sorted(grouped.keys()):
            sorted_grouped[sec] = sorted(grouped[sec], key=lambda x: x.roll_number or '')
            
        # Get all unique years and branches in the DB for the navigation tabs
        raw_branches = db.session.query(User.branch).filter(User.role == 'student').distinct().all()
        raw_years = db.session.query(User.year).filter(User.role == 'student').distinct().all()
        
        all_branches = [b[0] for b in raw_branches if b[0]]
        if any(not b[0] for b in raw_branches):
            all_branches.append("N/A")
        all_branches.sort()
        
        all_years = [y[0] for y in raw_years if y[0]]
        custom_year_order = {"1": 1, "2": 2, "3": 3, "4": 4, "Graduated": 5}
        all_years.sort(key=lambda y: custom_year_order.get(y, 99))
        
        # We also need a helper list to check which branches are actually available for active year
        year_branches = [b[0] for b in db.session.query(User.branch).filter(User.role == 'student', User.year == year).distinct().all() if b[0]]
        if any(not b[0] for b in db.session.query(User.branch).filter(User.role == 'student', User.year == year).distinct().all()):
            year_branches.append("N/A")
        year_branches.sort()
        
        # Get first branch for each year for default year-level tab navigation
        year_defaults = {}
        for yr in all_years:
            first_br = db.session.query(User.branch).filter(User.role == 'student', User.year == yr, User.branch != None, User.branch != '').first()
            year_defaults[yr] = first_br[0] if first_br else "N/A"
        
        return render_template(
            "manager_students_list.html",
            active_year=year,
            active_branch=branch,
            grouped_students=sorted_grouped,
            all_branches=all_branches,
            all_years=all_years,
            year_branches=year_branches,
            year_defaults=year_defaults,
            total_students=len(students)
        )

    @app.route("/manager/upload_students", methods=["GET", "POST"])
    @login_required
    def manager_upload_students():
     if current_user.role != "manager":
        abort(403)

     if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("❌ Please upload a CSV file", "danger")
            return redirect(request.url)
        try:
            students = parse_csv_students(file)
        except Exception as e:
            flash(f"❌ Failed to parse CSV: {e}", "danger")
            return redirect(request.url)

        added, updated, skipped = 0, 0, 0
        for s in students:
            email = (s.get("email") or "").strip()
            roll = (s.get("roll_number") or "").strip()
            if not email or not roll:
                skipped += 1
                continue

            existing = User.query.filter((User.email == email) | (User.roll_number == roll)).first()
            if existing:
                existing.name = s.get("name", "").strip() or existing.name
                existing.department = s.get("department", "").strip() or existing.department
                existing.branch = s.get("branch", "").strip() or existing.branch
                existing.section = s.get("section", "").strip() or existing.section
                existing.year = s.get("year", "").strip() or existing.year
                existing.set_password(s.get("password", "password123"))
                updated += 1
            else:
                u = User(
                    name=s.get("name", "").strip() or "Student",
                    email=email,
                    role="student",
                    department=s.get("department", "") or None,
                    branch=s.get("branch", "") or None,
                    section=s.get("section", "") or None,
                    year=s.get("year", "").strip() or None,
                    roll_number=roll,
                )
                u.set_password(s.get("password", "password123"))
                db.session.add(u)
                added += 1

        db.session.commit()
        flash(f"✅ {added} students added, 🔁 {updated} updated, ❌ {skipped} skipped", "success")
        return redirect(url_for("manager_dashboard"))

     return render_template("upload_students.html")

    @app.route("/manager/promote_students", methods=["POST"])
    @login_required
    def manager_promote_students():
        if current_user.role != "manager":
            abort(403)
        
        dept = request.form.get("department")
        branch = request.form.get("branch")
        section = request.form.get("section")
        year_filter = request.form.get("year")
        
        query = User.query.filter_by(role="student")
        
        if dept and dept != "All":
            query = query.filter_by(department=dept)
        if branch and branch != "All":
            query = query.filter_by(branch=branch)
        if section and section != "All":
            query = query.filter_by(section=section)
        if year_filter and year_filter != "All":
            query = query.filter_by(year=year_filter)
            
        students = query.all()
        promoted = 0
        for s in students:
            if s.year == "1":
                s.year = "2"
            elif s.year == "2":
                s.year = "3"
            elif s.year == "3":
                s.year = "4"
            elif s.year == "4" or s.year == "Graduated":
                s.year = "Graduated"
            else:
                s.year = "1"
            promoted += 1
            
        db.session.commit()
        
        filter_desc = []
        if dept and dept != "All": filter_desc.append(f"Dept: {dept}")
        if branch and branch != "All": filter_desc.append(f"Branch: {branch}")
        if section and section != "All": filter_desc.append(f"Sec: {section}")
        if year_filter and year_filter != "All": filter_desc.append(f"Year: {year_filter}")
        filter_str = f" ({', '.join(filter_desc)})" if filter_desc else " (All Students)"
        
        flash(f"✅ Successfully promoted {promoted} students to the next academic year{filter_str}!", "success")
        return redirect(url_for("manager_dashboard"))

    @app.route("/manager/delete_students_by_year", methods=["POST"])
    @login_required
    def manager_delete_students_by_year():
        if current_user.role != "manager":
            abort(403)
        
        year_filter = request.form.get("year")
        dept = request.form.get("department")
        branch = request.form.get("branch")
        section = request.form.get("section")
        
        query = User.query.filter_by(role="student")
        
        filter_desc = []
        if year_filter and year_filter != "All":
            query = query.filter_by(year=year_filter)
            filter_desc.append(f"Year: {year_filter}")
        elif year_filter == "All":
            filter_desc.append("All Years")
            
        if dept and dept != "All":
            query = query.filter_by(department=dept)
            filter_desc.append(f"Dept: {dept}")
        if branch and branch != "All":
            query = query.filter_by(branch=branch)
            filter_desc.append(f"Branch: {branch}")
        if section and section != "All":
            query = query.filter_by(section=section)
            filter_desc.append(f"Sec: {section}")
            
        students = query.all()
        student_ids = [s.id for s in students]
        
        if not student_ids:
            flash("ℹ️ No students found matching the selected filter.", "info")
            return redirect(request.referrer or url_for("manager_dashboard"))
            
        count = len(student_ids)
        
        # Cascading cleanup of attempts and responses to prevent foreign key errors
        attempts = Attempt.query.filter(Attempt.student_id.in_(student_ids)).all()
        attempt_ids = [a.id for a in attempts]
        if attempt_ids:
            Response.query.filter(Response.attempt_id.in_(attempt_ids)).delete(synchronize_session=False)
            Attempt.query.filter(Attempt.id.in_(attempt_ids)).delete(synchronize_session=False)
            
        User.query.filter(User.id.in_(student_ids)).delete(synchronize_session=False)
        db.session.commit()
        
        filter_str = f" ({', '.join(filter_desc)})" if filter_desc else ""
        flash(f"🗑️ Successfully deleted {count} student(s) and their quiz records{filter_str}.", "success")
        return redirect(request.referrer or url_for("manager_dashboard"))
    # Edit Faculty
    @app.route('/manager/edit_faculty/<int:id>', methods=['GET', 'POST'])
    @login_required
    def edit_faculty(id):
      faculty = User.query.get_or_404(id)

      if request.method == 'POST':
          faculty.name = request.form['name']
          faculty.email = request.form['email']
          faculty.department = request.form['department']
          faculty.branch = request.form['branch']
          faculty.section = request.form['section']
          faculty.year = request.form['year']
          db.session.commit()
          flash('Faculty details updated successfully!', 'success')
          return redirect(url_for('manager_dashboard'))
      return render_template('edit_faculty.html' , faculty=faculty)


# Delete Faculty
    @app.route('/manager/delete_faculty/<int:id>', methods=['POST'])
    @login_required
    def delete_faculty(id):
     faculty = User.query.get_or_404(id)
     db.session.delete(faculty)
     db.session.commit()
     flash('Faculty deleted successfully!', 'success')
     return redirect(url_for('manager_dashboard'))



    # ================================
    # Faculty Dashboard
    # ================================
    @app.route("/faculty")
    @login_required
    def faculty_dashboard():
        if current_user.role != "faculty":
            abort(403)
        quizzes = Quiz.query.filter_by(faculty_id=current_user.id).order_by(Quiz.created_at.desc()).all()
        
        now = datetime.now(IST)
        total_quizzes = len(quizzes)
        active_quizzes = 0
        for q in quizzes:
            q_start = convert_to_ist(q.start_time)
            q_end = convert_to_ist(q.end_time)
            if q_start and q_end and q_start <= now <= q_end:
                active_quizzes += 1
                
        total_attempts = db.session.query(db.func.count(Attempt.id)).join(Quiz).filter(Quiz.faculty_id == current_user.id).scalar() or 0
        
        # Show times in IST for faculty viewing
        for q in quizzes:
            q.start_time = convert_to_ist(q.start_time)
            q.end_time = convert_to_ist(q.end_time)
            
        return render_template(
            "faculty_dashboard.html",
            quizzes=quizzes,
            total_quizzes=total_quizzes,
            active_quizzes=active_quizzes,
            total_attempts=total_attempts,
            now=now
        )
    # ================================
# Faculty: Download quiz results
# ================================
    @app.route("/faculty/quiz/<int:quiz_id>/download")
    @login_required
    def download_quiz_results(quiz_id):
     if current_user.role != "faculty":
        abort(403)

     quiz = db.session.get(Quiz, quiz_id)
     if not quiz or quiz.faculty_id != current_user.id:
        abort(404)

      # Finalize any in-progress attempts for this quiz if its end time has passed
     finalize_expired_attempts_for_quiz(quiz.id)

    # Fetch all attempts
     attempts = (
        Attempt.query.join(User, User.id == Attempt.student_id)
        .filter(
            Attempt.quiz_id == quiz.id,
            User.section == quiz.section,
            User.branch == quiz.branch,
            User.department == quiz.department,
        )
        .order_by(User.roll_number.asc())
        .all()
     )

    # Generate CSV data
     output = io.StringIO()
     writer = csv.writer(output)
     writer.writerow(["Roll Number", "Name", "Score", "Max Score", "Status", "Submitted At"])
     for a in attempts:
        student = User.query.get(a.student_id)
        writer.writerow([
            student.roll_number,
            student.name,
            a.score or 0,
            a.max_score or 0,
            a.status,
            a.submitted_at.strftime("%Y-%m-%d %H:%M:%S") if a.submitted_at else "-",
        ])
     output.seek(0)

    # Send file as download
     return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        as_attachment=True,
        download_name=f"{quiz.title}_{quiz.section}_results.csv",
        mimetype="text/csv"
     )


    # ================================
    # Faculty: Quiz Submissions & Reschedule
    # ================================
    @app.route("/faculty/quiz/<int:quiz_id>/submissions")
    @login_required
    def quiz_submissions(quiz_id):
        if current_user.role not in ("faculty", "manager"):
            abort(403)

        quiz = db.session.get(Quiz, quiz_id)
        if not quiz:
            abort(404)
        if current_user.role == "faculty" and quiz.faculty_id != current_user.id:
            abort(403)

        finalize_expired_attempts_for_quiz(quiz.id)

        # Build query for target students
        students_query = User.query.filter_by(role="student")
        if quiz.department and quiz.department != "All":
            students_query = students_query.filter_by(department=quiz.department)
        if quiz.branch and quiz.branch != "All":
            students_query = students_query.filter_by(branch=quiz.branch)
        if quiz.section and quiz.section != "All":
            students_query = students_query.filter_by(section=quiz.section)
        if quiz.year and quiz.year != "All":
            students_query = students_query.filter_by(year=str(quiz.year))

        students = students_query.order_by(User.roll_number.asc(), User.name.asc()).all()

        attempts = Attempt.query.filter_by(quiz_id=quiz.id).all()
        attempts_map = {a.student_id: a for a in attempts}

        overrides = StudentQuizOverride.query.filter_by(quiz_id=quiz.id).all()
        overrides_map = {o.student_id: o for o in overrides}

        now_ist = datetime.now(IST)
        quiz_start_ist = convert_to_ist(quiz.start_time)
        quiz_end_ist = convert_to_ist(quiz.end_time)

        # Calculate max score for quiz
        total_q_count = len(quiz.questions)
        active_q_count = quiz.questions_per_student if (quiz.questions_per_student and 0 < quiz.questions_per_student < total_q_count) else total_q_count
        computed_max_score = active_q_count * (quiz.marks_per_question or 1)

        # Default suggested extension time (24 hours from now)
        default_extension = (now_ist + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")

        return render_template(
            "quiz_submissions.html",
            quiz=quiz,
            quiz_start_ist=quiz_start_ist,
            quiz_end_ist=quiz_end_ist,
            students=students,
            attempts_map=attempts_map,
            overrides_map=overrides_map,
            computed_max_score=computed_max_score,
            default_extension=default_extension,
            now=now_ist
        )

    @app.route("/faculty/quiz/<int:quiz_id>/reschedule_student/<int:student_id>", methods=["POST"])
    @login_required
    def reschedule_student_quiz(quiz_id, student_id):
        if current_user.role not in ("faculty", "manager"):
            abort(403)

        quiz = db.session.get(Quiz, quiz_id)
        if not quiz:
            abort(404)
        if current_user.role == "faculty" and quiz.faculty_id != current_user.id:
            abort(403)

        student = db.session.get(User, student_id)
        if not student:
            abort(404)

        extended_end_str = request.form.get("extended_end_time", "").strip()
        reason = request.form.get("reason", "").strip() or "Faculty rescheduled quiz"

        # Clear existing attempt & responses for this student
        existing_attempts = Attempt.query.filter_by(quiz_id=quiz.id, student_id=student.id).all()
        for att in existing_attempts:
            Response.query.filter_by(attempt_id=att.id).delete()
            db.session.delete(att)

        now_ist = datetime.now(IST)
        quiz_end_ist = convert_to_ist(quiz.end_time)

        # Determine extended end time
        extended_dt = None
        if extended_end_str:
            try:
                extended_dt = convert_to_ist(extended_end_str)
            except Exception as e:
                flash(f"⚠️ Warning: Invalid extended date format ({e}). Standard quiz window used.", "warning")
        elif quiz_end_ist and quiz_end_ist <= now_ist:
            # If quiz already expired and no end time given, grant +24h window by default
            extended_dt = now_ist + timedelta(hours=24)

        # Update or create override
        override = StudentQuizOverride.query.filter_by(quiz_id=quiz.id, student_id=student.id).first()
        if not override:
            override = StudentQuizOverride(quiz_id=quiz.id, student_id=student.id)

        if extended_dt:
            override.extended_end_time = extended_dt.replace(tzinfo=None)
        else:
            override.extended_end_time = None
        override.reason = reason
        db.session.add(override)
        db.session.commit()

        time_msg = f" until {extended_dt.strftime('%b %d, %Y %I:%M %p IST')}" if extended_dt else ""
        flash(f"✅ Quiz '{quiz.title}' successfully rescheduled for {student.name} ({student.roll_number or student.email}){time_msg}. Previous attempt cleared.", "success")

        return redirect(request.referrer or url_for("quiz_submissions", quiz_id=quiz.id))


    # ================================
    # Master Marks Sheet Logic & Routes
    # ================================
    def get_available_classes_map():
        """Returns structured dict of available years, branches, and sections."""
        student_classes = db.session.query(
            User.year, User.branch, User.section
        ).filter(
            User.role == "student",
            User.year != None,
            User.branch != None,
            User.section != None
        ).distinct().all()

        quiz_classes = db.session.query(
            Quiz.year, Quiz.branch, Quiz.section
        ).filter(
            Quiz.year != None,
            Quiz.branch != None,
            Quiz.section != None,
            Quiz.year != "All",
            Quiz.branch != "All",
            Quiz.section != "All"
        ).distinct().all()

        combined = set(student_classes).union(set(quiz_classes))
        from collections import defaultdict
        years_map = defaultdict(lambda: defaultdict(set))
        for yr, br, sec in combined:
            if yr and br and sec and str(yr).strip() and str(br).strip() and str(sec).strip():
                years_map[str(yr).strip()][str(br).strip()].add(str(sec).strip())

        result = {}
        custom_year_order = {"1": 1, "2": 2, "3": 3, "4": 4, "Graduated": 5}
        sorted_years = sorted(years_map.keys(), key=lambda y: custom_year_order.get(y, 99))
        for yr in sorted_years:
            result[yr] = {}
            for br in sorted(years_map[yr].keys()):
                result[yr][br] = sorted(list(years_map[yr][br]))
        return result

    def get_master_marks_context(year, branch, section, filter_status="conducted"):
        """
        Builds consolidated master marks matrix for a specific class (year, branch, section).
        Considers quizzes targeted directly or with 'All'.
        Finalizes expired attempts so marks are completely up to date.
        """
        now_ist = datetime.now(IST)
        
        # 1. Fetch matching quizzes for this class
        quizzes_query = Quiz.query.filter(
            or_(Quiz.year == str(year), Quiz.year == "All"),
            or_(Quiz.branch == str(branch), Quiz.branch == "All"),
            or_(Quiz.section == str(section), Quiz.section == "All"),
        ).order_by(Quiz.start_time.asc(), Quiz.id.asc())
        
        raw_quizzes = quizzes_query.all()
        quizzes = []
        for q in raw_quizzes:
            q_start_ist = convert_to_ist(q.start_time)
            q_end_ist = convert_to_ist(q.end_time)
            q.start_ist = q_start_ist
            q.end_ist = q_end_ist
            
            # Calculate quiz max score
            total_q_count = len(q.questions)
            active_q_count = q.questions_per_student if (q.questions_per_student and 0 < q.questions_per_student < total_q_count) else total_q_count
            q.computed_max_score = active_q_count * (q.marks_per_question or 1)
            
            # Status
            if q_end_ist and q_end_ist < now_ist:
                q.computed_status = "Ended"
            elif q_start_ist and q_start_ist <= now_ist:
                q.computed_status = "Live"
            else:
                q.computed_status = "Upcoming"
                
            # Filter condition: conducted only (started) vs all
            if filter_status == "all" or q.computed_status in ("Ended", "Live"):
                # Pre-finalize attempts if expired
                finalize_expired_attempts_for_quiz(q.id)
                quizzes.append(q)

        # 2. Fetch all registered students in this class
        students = User.query.filter_by(
            role="student",
            year=str(year),
            branch=str(branch),
            section=str(section)
        ).order_by(User.roll_number.asc(), User.name.asc()).all()

        # 3. Fetch all attempts in bulk
        quiz_ids = [q.id for q in quizzes]
        student_ids = [s.id for s in students]
        attempts = Attempt.query.filter(
            Attempt.quiz_id.in_(quiz_ids),
            Attempt.student_id.in_(student_ids)
        ).all() if (quiz_ids and student_ids) else []

        attempts_map = {(a.student_id, a.quiz_id): a for a in attempts}

        # 4. Build per-student rows
        matrix = []
        total_class_max = sum(q.computed_max_score for q in quizzes)
        quiz_stats = {q.id: {"total_score": 0, "attended_count": 0, "max_score": 0, "min_score": 9999} for q in quizzes}

        for idx, s in enumerate(students, 1):
            s_data = {
                "sno": idx,
                "student": s,
                "scores": {},
                "total_obtained": 0,
                "total_max": total_class_max,
                "percentage": 0.0,
                "attended_count": 0,
            }

            for q in quizzes:
                att = attempts_map.get((s.id, q.id))
                q_max = q.computed_max_score
                if att and att.status == "submitted":
                    score = att.score if att.score is not None else 0
                    s_data["scores"][q.id] = {
                        "score": score,
                        "max_score": q_max,
                        "status": "submitted",
                        "is_absent": False,
                        "submitted_at": convert_to_ist(att.submitted_at) if att.submitted_at else None
                    }
                    s_data["total_obtained"] += score
                    s_data["attended_count"] += 1
                    quiz_stats[q.id]["total_score"] += score
                    quiz_stats[q.id]["attended_count"] += 1
                    if score > quiz_stats[q.id]["max_score"]:
                        quiz_stats[q.id]["max_score"] = score
                    if score < quiz_stats[q.id]["min_score"]:
                        quiz_stats[q.id]["min_score"] = score
                elif att and att.status == "in_progress":
                    score = att.score if att.score is not None else 0
                    s_data["scores"][q.id] = {
                        "score": score,
                        "max_score": q_max,
                        "status": "in_progress",
                        "is_absent": False,
                        "submitted_at": None
                    }
                    s_data["total_obtained"] += score
                    s_data["attended_count"] += 1
                    quiz_stats[q.id]["total_score"] += score
                    quiz_stats[q.id]["attended_count"] += 1
                else:
                    s_data["scores"][q.id] = {
                        "score": 0,
                        "max_score": q_max,
                        "status": "absent",
                        "is_absent": True,
                        "submitted_at": None
                    }

            if total_class_max > 0:
                s_data["percentage"] = round((s_data["total_obtained"] / total_class_max) * 100, 2)
            else:
                s_data["percentage"] = 0.0
                
            matrix.append(s_data)

        # Sort by total_obtained desc to assign rank
        sorted_by_marks = sorted(matrix, key=lambda x: x["total_obtained"], reverse=True)
        for rank_idx, item in enumerate(sorted_by_marks, 1):
            item["rank"] = rank_idx

        # Calculate per-quiz stats
        for q in quizzes:
            q_stat = quiz_stats[q.id]
            if q_stat["attended_count"] > 0:
                q_stat["average_score"] = round(q_stat["total_score"] / q_stat["attended_count"], 2)
                q_stat["attendance_pct"] = round((q_stat["attended_count"] / len(students)) * 100, 1) if students else 0
            else:
                q_stat["average_score"] = 0.0
                q_stat["attendance_pct"] = 0.0
                q_stat["min_score"] = 0

        total_students_count = len(students)
        class_avg_score = round(sum(m["total_obtained"] for m in matrix) / total_students_count, 2) if total_students_count else 0.0
        class_avg_pct = round(sum(m["percentage"] for m in matrix) / total_students_count, 2) if total_students_count else 0.0
        highest_score = max((m["total_obtained"] for m in matrix), default=0)
        lowest_score = min((m["total_obtained"] for m in matrix), default=0) if matrix else 0
        top_student = sorted_by_marks[0]["student"] if sorted_by_marks else None

        return {
            "year": str(year),
            "branch": str(branch),
            "section": str(section),
            "filter_status": filter_status,
            "quizzes": quizzes,
            "students": students,
            "matrix": matrix,
            "quiz_stats": quiz_stats,
            "total_class_max": total_class_max,
            "total_students_count": total_students_count,
            "class_avg_score": class_avg_score,
            "class_avg_pct": class_avg_pct,
            "highest_score": highest_score,
            "lowest_score": lowest_score,
            "top_student": top_student,
        }

    @app.route("/master_marks_sheet")
    @login_required
    def master_marks_sheet():
        if current_user.role not in ("faculty", "manager"):
            abort(403)

        classes_map = get_available_classes_map()
        available_years = list(classes_map.keys())

        # Read query params
        req_year = request.args.get("year")
        req_branch = request.args.get("branch")
        req_section = request.args.get("section")
        filter_status = request.args.get("filter_status", "conducted")

        # Resolve defaults if not provided
        year = req_year
        branch = req_branch
        section = req_section

        if not (year and branch and section):
            # Try from current faculty user
            if current_user.role == "faculty":
                if current_user.year and current_user.branch and current_user.section:
                    year = str(current_user.year)
                    branch = current_user.branch
                    section = current_user.section
                else:
                    # check quizzes created by this faculty
                    fq = Quiz.query.filter_by(faculty_id=current_user.id).order_by(Quiz.created_at.desc()).first()
                    if fq and fq.year != "All" and fq.branch != "All" and fq.section != "All":
                        year = str(fq.year)
                        branch = fq.branch
                        section = fq.section

            # Fallback to first available class in DB
            if not (year and branch and section) and available_years:
                for yr in available_years:
                    branches = list(classes_map[yr].keys())
                    if branches:
                        br = branches[0]
                        sections = classes_map[yr][br]
                        if sections:
                            year = yr
                            branch = br
                            section = sections[0]
                            break

        context = {
            "quizzes": [],
            "students": [],
            "matrix": [],
            "quiz_stats": {},
            "total_class_max": 0,
            "total_students_count": 0,
            "class_avg_score": 0,
            "class_avg_pct": 0,
            "highest_score": 0,
            "lowest_score": 0,
            "top_student": None,
            "filter_status": filter_status,
        }
        if year and branch and section:
            context.update(get_master_marks_context(year, branch, section, filter_status))

        import json
        return render_template(
            "master_marks_sheet.html",
            classes_map=classes_map,
            classes_map_json=json.dumps(classes_map),
            available_years=available_years,
            selected_year=year,
            selected_branch=branch,
            selected_section=section,
            **context
        )

    @app.route("/master_marks_sheet/export/excel")
    @login_required
    def export_master_marks_excel():
        if current_user.role not in ("faculty", "manager"):
            abort(403)

        year = request.args.get("year", "1")
        branch = request.args.get("branch", "AIML")
        section = request.args.get("section", "A")
        filter_status = request.args.get("filter_status", "conducted")

        data = get_master_marks_context(year, branch, section, filter_status)
        quizzes = data["quizzes"]
        matrix = data["matrix"]

        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Yr{year}_{branch}_{section}"
        ws.views.sheetView[0].showGridLines = True

        title_font = Font(name="Calibri", size=14, bold=True, color="1E3A8A")
        subtitle_font = Font(name="Calibri", size=11, bold=True, color="1E293B")
        meta_font = Font(name="Calibri", size=10, italic=True, color="64748B")

        # Top banner
        total_cols = max(7 + len(quizzes), 7)
        last_col_letter = get_column_letter(total_cols)

        ws.merge_cells(f"A1:{last_col_letter}1")
        ws["A1"] = "VIGNAN'S FOUNDATION FOR SCIENCE, TECHNOLOGY AND RESEARCH (VFSTR)"
        ws["A1"].font = title_font
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 24

        ws.merge_cells(f"A2:{last_col_letter}2")
        ws["A2"] = "CONSOLIDATED CLASS MASTER MARKS SHEET"
        ws["A2"].font = subtitle_font
        ws["A2"].alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[2].height = 20

        now_str = datetime.now(IST).strftime("%d-%b-%Y %I:%M %p")
        ws.merge_cells(f"A3:{last_col_letter}3")
        ws["A3"] = f"Academic Year: {year}   |   Branch: {branch}   |   Section: {section}   |   Quizzes Conducted: {len(quizzes)}   |   Generated: {now_str} IST"
        ws["A3"].font = meta_font
        ws["A3"].alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[3].height = 18

        # Headers
        headers = ["S.No", "Roll Number", "Student Name"]
        for q in quizzes:
            headers.append(f"{q.title}\n(Max: {q.computed_max_score})")
        headers.extend(["Total Obtained", "Max Marks", "Percentage (%)", "Exams Attended", "Class Rank"])

        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        thin_border = Border(
            left=Side(style='thin', color='CBD5E1'),
            right=Side(style='thin', color='CBD5E1'),
            top=Side(style='thin', color='CBD5E1'),
            bottom=Side(style='thin', color='CBD5E1')
        )

        header_row = 5
        ws.row_dimensions[header_row].height = 32
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=header_row, column=col_idx, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border

        # Student data rows
        absent_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
        absent_font = Font(name="Calibri", size=10, bold=True, color="991B1B")
        zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
        regular_font = Font(name="Calibri", size=10)
        bold_font = Font(name="Calibri", size=10, bold=True)

        current_row = header_row
        for idx, row_data in enumerate(matrix, 1):
            current_row += 1
            ws.row_dimensions[current_row].height = 20
            fill = zebra_fill if (idx % 2 == 0) else None

            # S.No
            c = ws.cell(row=current_row, column=1, value=row_data["sno"])
            c.alignment = Alignment(horizontal="center")
            
            # Roll Number
            c = ws.cell(row=current_row, column=2, value=row_data["student"].roll_number)
            c.alignment = Alignment(horizontal="center")

            # Student Name
            c = ws.cell(row=current_row, column=3, value=row_data["student"].name)
            c.alignment = Alignment(horizontal="left")

            # Quiz scores
            col_pos = 4
            for q in quizzes:
                q_info = row_data["scores"].get(q.id, {})
                cell = ws.cell(row=current_row, column=col_pos)
                if q_info.get("is_absent", True):
                    cell.value = "ABSENT"
                    cell.fill = absent_fill
                    cell.font = absent_font
                    cell.alignment = Alignment(horizontal="center")
                else:
                    cell.value = q_info.get("score", 0)
                    cell.alignment = Alignment(horizontal="center")
                    cell.font = regular_font
                    if fill:
                        cell.fill = fill
                cell.border = thin_border
                col_pos += 1

            # Totals
            # Total Obtained
            c = ws.cell(row=current_row, column=col_pos, value=row_data["total_obtained"])
            c.alignment = Alignment(horizontal="center")
            c.font = bold_font
            c.border = thin_border
            if fill: c.fill = fill
            col_pos += 1

            # Max Marks
            c = ws.cell(row=current_row, column=col_pos, value=row_data["total_max"])
            c.alignment = Alignment(horizontal="center")
            c.font = regular_font
            c.border = thin_border
            if fill: c.fill = fill
            col_pos += 1

            # Percentage
            c = ws.cell(row=current_row, column=col_pos, value=row_data["percentage"])
            c.alignment = Alignment(horizontal="center")
            c.font = bold_font
            c.border = thin_border
            if fill: c.fill = fill
            col_pos += 1

            # Exams Attended
            c = ws.cell(row=current_row, column=col_pos, value=f"{row_data['attended_count']}/{len(quizzes)}")
            c.alignment = Alignment(horizontal="center")
            c.font = regular_font
            c.border = thin_border
            if fill: c.fill = fill
            col_pos += 1

            # Rank
            c = ws.cell(row=current_row, column=col_pos, value=row_data.get("rank", "-"))
            c.alignment = Alignment(horizontal="center")
            c.font = regular_font
            c.border = thin_border
            if fill: c.fill = fill

            for col_i in (1, 2, 3):
                c = ws.cell(row=current_row, column=col_i)
                c.border = thin_border
                c.font = regular_font if col_i != 2 else bold_font
                if fill: c.fill = fill

        # Summary Average Row at the bottom
        if matrix and quizzes:
            current_row += 1
            ws.row_dimensions[current_row].height = 22
            summary_fill = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
            summary_font = Font(name="Calibri", size=10, bold=True, color="1E293B")
            
            c = ws.cell(row=current_row, column=1, value="")
            c.fill = summary_fill; c.border = thin_border
            c = ws.cell(row=current_row, column=2, value="")
            c.fill = summary_fill; c.border = thin_border
            c = ws.cell(row=current_row, column=3, value="CLASS AVERAGE")
            c.fill = summary_fill; c.font = summary_font; c.border = thin_border; c.alignment = Alignment(horizontal="right")

            col_pos = 4
            for q in quizzes:
                avg = data["quiz_stats"].get(q.id, {}).get("average_score", 0.0)
                c = ws.cell(row=current_row, column=col_pos, value=avg)
                c.fill = summary_fill; c.font = summary_font; c.border = thin_border; c.alignment = Alignment(horizontal="center")
                col_pos += 1

            c = ws.cell(row=current_row, column=col_pos, value=data["class_avg_score"])
            c.fill = summary_fill; c.font = summary_font; c.border = thin_border; c.alignment = Alignment(horizontal="center"); col_pos += 1
            c = ws.cell(row=current_row, column=col_pos, value=data["total_class_max"])
            c.fill = summary_fill; c.font = summary_font; c.border = thin_border; c.alignment = Alignment(horizontal="center"); col_pos += 1
            c = ws.cell(row=current_row, column=col_pos, value=f"{data['class_avg_pct']}%")
            c.fill = summary_fill; c.font = summary_font; c.border = thin_border; c.alignment = Alignment(horizontal="center"); col_pos += 1
            c = ws.cell(row=current_row, column=col_pos, value="")
            c.fill = summary_fill; c.border = thin_border; col_pos += 1
            c = ws.cell(row=current_row, column=col_pos, value="")
            c.fill = summary_fill; c.border = thin_border

        # Auto-fit columns
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                # ignore merged title banner cells
                if cell.row in (1, 2, 3):
                    continue
                val_str = str(cell.value or "")
                first_line = val_str.split("\n")[0]
                if len(first_line) > max_len:
                    max_len = len(first_line)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 11)

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        filename = f"Master_Marks_{branch}_Sec_{section}_Year_{year}.xlsx"
        return send_file(
            buf,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    @app.route("/master_marks_sheet/export/csv")
    @login_required
    def export_master_marks_csv():
        if current_user.role not in ("faculty", "manager"):
            abort(403)

        year = request.args.get("year", "1")
        branch = request.args.get("branch", "AIML")
        section = request.args.get("section", "A")
        filter_status = request.args.get("filter_status", "conducted")

        data = get_master_marks_context(year, branch, section, filter_status)
        quizzes = data["quizzes"]
        matrix = data["matrix"]

        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header row
        header = ["S.No", "Roll Number", "Student Name"]
        for q in quizzes:
            header.append(f"{q.title} (Max: {q.computed_max_score})")
        header.extend(["Total Obtained", "Max Marks", "Percentage (%)", "Exams Attended", "Class Rank"])
        writer.writerow(header)

        for row in matrix:
            r = [row["sno"], row["student"].roll_number, row["student"].name]
            for q in quizzes:
                q_info = row["scores"].get(q.id, {})
                if q_info.get("is_absent", True):
                    r.append("ABSENT")
                else:
                    r.append(q_info.get("score", 0))
            r.extend([
                row["total_obtained"],
                row["total_max"],
                row["percentage"],
                f"{row['attended_count']}/{len(quizzes)}",
                row.get("rank", "-")
            ])
            writer.writerow(r)

        output.seek(0)
        filename = f"Master_Marks_{branch}_Sec_{section}_Year_{year}.csv"
        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            as_attachment=True,
            download_name=filename,
            mimetype="text/csv"
        )


    @app.route('/faculty/delete_quiz/<int:id>', methods=['POST'])
    @login_required
    def delete_quiz(id):
        if current_user.role != 'faculty':
            abort(403)
        quiz = db.session.get(Quiz, id)
        if not quiz or quiz.faculty_id != current_user.id:
            abort(404)
        # Delete related attempts/responses/questions
        Attempt.query.filter_by(quiz_id=quiz.id).delete()
        Question.query.filter_by(quiz_id=quiz.id).delete()
        db.session.delete(quiz)
        db.session.commit()
        flash('✅ Quiz deleted successfully.', 'success')
        return redirect(url_for('faculty_dashboard'))

    @app.route("/faculty/quiz/<int:quiz_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_quiz(quiz_id):
        if current_user.role != "faculty":
            abort(403)
        
        quiz = db.session.get(Quiz, quiz_id)
        if not quiz or quiz.faculty_id != current_user.id:
            abort(404)
            
        if request.method == "POST":
            title = request.form.get("title")
            start_time = request.form.get("start_time")
            end_time = request.form.get("end_time")
            start_password = request.form.get("start_password")
            marks_per_question_val = request.form.get("marks_per_question")
            questions_per_student_val = request.form.get("questions_per_student")
            
            if not all([title, start_time, end_time, start_password]):
                flash("❌ All fields are required", "danger")
                return redirect(request.url)
                
            try:
                start_dt = convert_to_ist(start_time)
                end_dt = convert_to_ist(end_time)
                if start_dt is None or end_dt is None:
                    raise ValueError("start or end time is missing")
                if end_dt <= start_dt:
                    raise ValueError("end must be after start")
            except Exception as e:
                flash(f"❌ Invalid start/end time: {e}", "danger")
                return redirect(request.url)
                
            try:
                marks_per_question = int(marks_per_question_val) if marks_per_question_val else 1
                if marks_per_question < 1:
                    marks_per_question = 1
            except Exception:
                marks_per_question = 1

            try:
                questions_per_student = int(questions_per_student_val) if questions_per_student_val and questions_per_student_val.strip() else None
                if questions_per_student is not None and questions_per_student <= 0:
                    questions_per_student = None
            except Exception:
                questions_per_student = None
                
            quiz.title = title
            quiz.start_time = start_dt.replace(tzinfo=None) if getattr(start_dt, 'tzinfo', None) else start_dt
            quiz.end_time = end_dt.replace(tzinfo=None) if getattr(end_dt, 'tzinfo', None) else end_dt
            quiz.start_password = start_password
            quiz.marks_per_question = marks_per_question
            quiz.questions_per_student = questions_per_student
            
            # Recalculate score/max_score for existing attempts of this quiz
            attempts = Attempt.query.filter_by(quiz_id=quiz.id).all()
            for attempt in attempts:
                questions = get_attempt_questions(quiz, attempt)
                if attempt.status == "submitted" and questions:
                    responses = {r.question_id: r for r in Response.query.filter_by(attempt_id=attempt.id).all()}
                    if responses:
                        correct_count = 0
                        for q in questions:
                            r = responses.get(q.id)
                            if r and r.selected and r.selected.strip().upper() == (q.correct or "").strip().upper():
                                correct_count += 1
                        attempt.score = correct_count * marks_per_question
                    else:
                        if attempt.max_score > 0:
                            raw_correct = int(round(attempt.score / (attempt.max_score / len(questions))))
                            attempt.score = raw_correct * marks_per_question
                    attempt.max_score = len(questions) * marks_per_question
                    db.session.add(attempt)
                    
            db.session.commit()
            flash("✅ Quiz details updated successfully!", "success")
            return redirect(url_for("faculty_dashboard"))
            
        start_ist = convert_to_ist(quiz.start_time)
        end_ist = convert_to_ist(quiz.end_time)
        start_val = start_ist.strftime("%Y-%m-%dT%H:%M") if start_ist else ""
        end_val = end_ist.strftime("%Y-%m-%dT%H:%M") if end_ist else ""
        
        return render_template(
            "edit_quiz.html",
            quiz=quiz,
            start_val=start_val,
            end_val=end_val
        )


    # ================================
# Faculty: Upload Quiz
# ================================
    
    @app.route("/faculty/upload_quiz", methods=["GET", "POST"])
    @login_required
    def upload_quiz():
        if current_user.role != "faculty":
            abort(403)

        if request.method == "POST":
            title = request.form.get("title")
            department = request.form.get("department")
            branch = request.form.get("branch")
            section = request.form.get("section")
            year = request.form.get("year")
            start_time = request.form.get("start_time")
            end_time = request.form.get("end_time")
            start_password = request.form.get("start_password")
            file = request.files.get("csv")
            marks_per_question_val = request.form.get("marks_per_question")
            questions_per_student_val = request.form.get("questions_per_student")

            if not all([title, department, branch, section, year, start_time, end_time, start_password, file]):
                flash("❌ All fields are required", "danger")
                return redirect(request.url)

            try:
                marks_per_question = int(marks_per_question_val) if marks_per_question_val else 1
                if marks_per_question < 1:
                    marks_per_question = 1
            except Exception:
                marks_per_question = 1

            try:
                questions_per_student = int(questions_per_student_val) if questions_per_student_val and questions_per_student_val.strip() else None
                if questions_per_student is not None and questions_per_student <= 0:
                    questions_per_student = None
            except Exception:
                questions_per_student = None

            # Time validation
            try:
                start_dt = convert_to_ist(start_time)
                end_dt = convert_to_ist(end_time)
                if start_dt is None or end_dt is None:
                    raise ValueError("start or end time is missing")
                if end_dt <= start_dt:
                    raise ValueError("end must be after start")
            except Exception as e:
                flash(f"❌ Invalid start/end time: {e}", "danger")
                return redirect(request.url)

            filename_lower = (file.filename or "").lower()
            content = ""
            extracted_images = {}  # {filename_lower: static_url}

            # Check if user uploaded a ZIP archive
            import zipfile
            import uuid

            if filename_lower.endswith(".zip"):
                try:
                    upload_folder_id = str(uuid.uuid4())[:8]
                    target_dir = os.path.join(app.static_folder, "uploads", "quiz_diagrams", upload_folder_id)
                    os.makedirs(target_dir, exist_ok=True)

                    with zipfile.ZipFile(file.stream) as z:
                        csv_name = None
                        for z_info in z.infolist():
                            if z_info.is_dir():
                                continue
                            fname = os.path.basename(z_info.filename)
                            if not fname or fname.startswith("."):
                                continue
                            
                            fname_lower = fname.lower()
                            if fname_lower.endswith(".csv") and not csv_name:
                                csv_name = z_info.filename
                                raw_bytes = z.read(z_info)
                                try:
                                    content = raw_bytes.decode("utf-8-sig")
                                except Exception:
                                    content = raw_bytes.decode("latin-1", errors="replace")
                            elif any(fname_lower.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"]):
                                img_bytes = z.read(z_info)
                                out_path = os.path.join(target_dir, fname)
                                with open(out_path, "wb") as f_out:
                                    f_out.write(img_bytes)
                                rel_url = f"/static/uploads/quiz_diagrams/{upload_folder_id}/{fname}"

                                try:
                                    from firebase_config import is_firebase_initialized, upload_file_to_firebase_storage
                                    if is_firebase_initialized():
                                        rel_url = upload_file_to_firebase_storage(img_bytes, f"diagrams/{upload_folder_id}/{fname}")
                                except Exception:
                                    pass

                                extracted_images[fname_lower] = rel_url

                        if not content:
                            flash("❌ No CSV file found inside the ZIP archive.", "danger")
                            return redirect(request.url)
                except Exception as e:
                    flash(f"❌ Error processing ZIP file: {e}", "danger")
                    return redirect(request.url)
            else:
                # Direct CSV file upload
                try:
                    raw_bytes = file.stream.read()
                    try:
                        content = raw_bytes.decode("utf-8-sig")
                    except Exception:
                        content = raw_bytes.decode("latin-1", errors="replace")
                except Exception as e:
                    flash(f"❌ Error reading file: {e}", "danger")
                    return redirect(request.url)

            # Parse CSV Content
            try:
                delimiter = ","
                lines = [l for l in content.splitlines() if l.strip()]
                first_line = lines[0] if lines else ""
                if ";" in first_line and "," not in first_line:
                    delimiter = ";"
                elif "\t" in first_line and "," not in first_line:
                    delimiter = "\t"

                stream = StringIO(content)
                reader = csv.DictReader(stream, delimiter=delimiter)
                questions = []

                def normalize_row(row):
                    if not row:
                        return {}
                    return { (str(k or "")).strip().lower().replace(" ", "_").replace(".", "").replace("-", "_"): str(v or "").strip() for k, v in row.items() if k is not None }

                def get_from_row(r, variants):
                    for key in variants:
                        val = r.get(key)
                        if val is not None and val != "":
                            return val
                    return ""

                def normalize_correct(val, opt_a, opt_b, opt_c, opt_d):
                    v_clean = str(val or "").strip().upper()
                    if v_clean in ["A", "B", "C", "D"]:
                        return v_clean
                    if v_clean in ["1", "OPTION 1", "OPTION_1", "OPTION A", "OPTION_A"]:
                        return "A"
                    if v_clean in ["2", "OPTION 2", "OPTION_2", "OPTION B", "OPTION_B"]:
                        return "B"
                    if v_clean in ["3", "OPTION 3", "OPTION_3", "OPTION C", "OPTION_C"]:
                        return "C"
                    if v_clean in ["4", "OPTION 4", "OPTION_4", "OPTION D", "OPTION_D"]:
                        return "D"
                    v_orig = str(val or "").strip().lower()
                    if v_orig and v_orig == str(opt_a or "").strip().lower():
                        return "A"
                    if v_orig and v_orig == str(opt_b or "").strip().lower():
                        return "B"
                    if v_orig and v_orig == str(opt_c or "").strip().lower():
                        return "C"
                    if v_orig and v_orig == str(opt_d or "").strip().lower():
                        return "D"
                    return "A"

                q_variants = ["question", "questions", "q", "question_text", "questiontext", "q_text", "qtext", "item", "prompt", "problem", "text", "s_no", "sno"]
                a_variants = ["a", "option_a", "optiona", "opt_a", "opta", "choice_a", "choicea", "option_1", "option1", "opt_1", "opt1", "1"]
                b_variants = ["b", "option_b", "optionb", "opt_b", "optb", "choice_b", "choiceb", "option_2", "option2", "opt_2", "opt2", "2"]
                c_variants = ["c", "option_c", "optionc", "opt_c", "optc", "choice_c", "choicec", "option_3", "option3", "opt_3", "opt3", "3"]
                d_variants = ["d", "option_d", "optiond", "opt_d", "optd", "choice_d", "choiced", "option_4", "option4", "opt_4", "opt4", "4"]
                corr_variants = ["correct", "answer", "ans", "correct_answer", "correctanswer", "correct_option", "correctoption", "key", "right_answer", "solution"]
                img_variants = ["image_url", "image_file", "diagram", "image", "diagram_url", "img", "picture", "figure", "fig"]

                for row in reader:
                    r = normalize_row(row)
                    q_text = get_from_row(r, q_variants)
                    a = get_from_row(r, a_variants)
                    b = get_from_row(r, b_variants)
                    c = get_from_row(r, c_variants)
                    d = get_from_row(r, d_variants)
                    raw_corr = get_from_row(r, corr_variants)
                    raw_img = get_from_row(r, img_variants)

                    if not q_text:
                        continue

                    correct = normalize_correct(raw_corr, a, b, c, d)
                    
                    # Resolve image path/URL if matching ZIP extracted image
                    resolved_img_url = raw_img
                    if raw_img and raw_img.lower() in extracted_images:
                        resolved_img_url = extracted_images[raw_img.lower()]
                    elif raw_img and os.path.basename(raw_img).lower() in extracted_images:
                        resolved_img_url = extracted_images[os.path.basename(raw_img).lower()]

                    questions.append({
                        "question": q_text,
                        "A": a,
                        "B": b,
                        "C": c,
                        "D": d,
                        "correct": correct,
                        "image_url": resolved_img_url
                    })

                # Positional Fallback if DictReader produced no questions
                if not questions:
                    stream.seek(0)
                    raw_reader = csv.reader(stream, delimiter=delimiter)
                    for row_idx, row in enumerate(raw_reader):
                        if not row or len(row) < 5:
                            continue
                        first_cell = str(row[0]).strip().lower()
                        if row_idx == 0 and ("question" in first_cell or "q.no" in first_cell or "s.no" in first_cell):
                            continue
                        q_text = str(row[0]).strip()
                        a = str(row[1]).strip() if len(row) > 1 else ""
                        b = str(row[2]).strip() if len(row) > 2 else ""
                        c = str(row[3]).strip() if len(row) > 3 else ""
                        d = str(row[4]).strip() if len(row) > 4 else ""
                        raw_corr = str(row[5]).strip() if len(row) > 5 else "A"
                        raw_img = str(row[6]).strip() if len(row) > 6 else ""
                        if q_text:
                            correct = normalize_correct(raw_corr, a, b, c, d)
                            resolved_img_url = raw_img
                            if raw_img and raw_img.lower() in extracted_images:
                                resolved_img_url = extracted_images[raw_img.lower()]
                            elif raw_img and os.path.basename(raw_img).lower() in extracted_images:
                                resolved_img_url = extracted_images[os.path.basename(raw_img).lower()]
                            questions.append({
                                "question": q_text,
                                "A": a,
                                "B": b,
                                "C": c,
                                "D": d,
                                "correct": correct,
                                "image_url": resolved_img_url
                            })
            except Exception as e:
                flash(f"❌ Error parsing questions: {e}", "danger")
                return redirect(request.url)

            if not questions:
                flash("❌ No valid questions found. Please check your CSV/ZIP file formatting.", "danger")
                return redirect(request.url)

            # Create Quiz
            quiz = Quiz(
                title=title,
                department=department,
                branch=branch,
                section=section,
                year=year,
                faculty_id=current_user.id,
                start_time=start_dt.replace(tzinfo=None) if getattr(start_dt, 'tzinfo', None) else start_dt,
                end_time=end_dt.replace(tzinfo=None) if getattr(end_dt, 'tzinfo', None) else end_dt,
                start_password=start_password,
                marks_per_question=marks_per_question,
                questions_per_student=questions_per_student,
            )
            db.session.add(quiz)
            db.session.flush()

            # Add questions with image_url
            for q in questions:
                db.session.add(Question(
                    quiz_id=quiz.id,
                    text=q["question"],
                    option_a=q["A"],
                    option_b=q["B"],
                    option_c=q["C"],
                    option_d=q["D"],
                    correct=q["correct"],
                    image_url=q.get("image_url") or None
                ))

            db.session.commit()
            flash(f"✅ Quiz created successfully with {len(questions)} questions!", "success")
            return redirect(url_for("faculty_dashboard"))

        return render_template("upload_quiz.html")

    @app.route("/faculty/sample_questions_csv")
    @login_required
    def sample_questions_csv():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["question", "image_url", "A", "B", "C", "D", "correct"])
        writer.writerow(["What is the time complexity of binary search?", "", "O(N)", "O(log N)", "O(N^2)", "O(1)", "B"])
        writer.writerow(["Identify the logic gate shown in the diagram.", "nand_gate.png", "AND Gate", "NAND Gate", "OR Gate", "XOR Gate", "B"])
        writer.writerow(["Which data structure uses LIFO principle?", "", "Queue", "Stack", "Array", "Linked List", "B"])
        
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            mimetype="text/csv",
            as_attachment=True,
            download_name="sample_quiz_questions.csv"
        )



    # ================================
    # Student Quiz Routes
    # ================================
    @app.route("/student")
    @login_required
    def student_dashboard():
        if current_user.role != "student":
            abort(403)
        # Finalize any of the student's in-progress attempts that have expired
        finalize_expired_attempts_for_student(current_user.id)
        now = datetime.now(IST)
        quizzes = Quiz.query.filter(
            or_(Quiz.department == current_user.department, Quiz.department == "All"),
            or_(Quiz.branch == current_user.branch, Quiz.branch == "All"),
            or_(Quiz.section == current_user.section, Quiz.section == "All"),
            or_(Quiz.year == current_user.year, Quiz.year == "All"),
        ).order_by(Quiz.start_time.asc()).all()

        overrides = {o.quiz_id: o for o in StudentQuizOverride.query.filter_by(student_id=current_user.id).all()}
        effective_ends = {}

        # Display quiz times in IST for students
        for q in quizzes:
            q.start_time = convert_to_ist(q.start_time)
            effective_ends[q.id] = get_effective_quiz_end_time(q, current_user.id)
            q.end_time = effective_ends[q.id]

        attempts = {a.quiz_id: a for a in Attempt.query.filter_by(student_id=current_user.id).all()}
        return render_template("student_dashboard.html", quizzes=quizzes, attempts=attempts, overrides=overrides, effective_ends=effective_ends, now=now)
    
    @app.route('/manager/edit_student/<int:id>', methods=['GET', 'POST'])
    @login_required
    def edit_student(id):
        student = User.query.get_or_404(id)
        if request.method == 'POST':
          student.name = request.form['name']
          student.roll_number = request.form['roll_number']
          student.email = request.form.get('email') or student.email
          student.department = request.form['department']
          student.branch = request.form['branch']
          student.section = request.form['section']
          student.year = request.form.get('year')
          db.session.commit()
          flash('Student updated successfully!', 'success')
          return redirect(url_for('manager_dashboard'))
        return render_template('edit_student.html', student=student)


    @app.route('/manager/delete_student/<int:id>', methods=['POST'])
    @login_required
    def delete_student(id):
        if current_user.role != "manager":
            abort(403)
        student = User.query.get_or_404(id)
        name = student.name
        roll = student.roll_number or ""
        
        # Cascading cleanup of attempts & responses to avoid foreign key errors
        attempts = Attempt.query.filter_by(student_id=student.id).all()
        attempt_ids = [a.id for a in attempts]
        if attempt_ids:
            Response.query.filter(Response.attempt_id.in_(attempt_ids)).delete(synchronize_session=False)
            Attempt.query.filter(Attempt.id.in_(attempt_ids)).delete(synchronize_session=False)
            
        db.session.delete(student)
        db.session.commit()
        flash(f'Student {name} ({roll}) deleted successfully!', 'success')
        return redirect(request.referrer or url_for('manager_dashboard'))


    @app.route("/student/quiz/<int:quiz_id>/start", methods=["GET", "POST"])
    @login_required
    def start_quiz(quiz_id):
        if current_user.role != "student":
            abort(403)
        quiz = db.session.get(Quiz, quiz_id)
        if not quiz:
            abort(404)

        # Use IST only for both display and comparison per user request
        quiz_start_ist = convert_to_ist(quiz.start_time)
        quiz_end_ist = get_effective_quiz_end_time(quiz, current_user.id)
        now_ist = datetime.now(IST)

        can_start = (quiz_start_ist <= now_ist <= quiz_end_ist)

        # If POST, validate and then start if allowed
        if request.method == "POST":
            if not can_start:
                flash("❌ Quiz not open right now.", "danger")
                return render_template(
                    "start_quiz.html",
                    quiz=quiz,
                    can_start=can_start,
                    reason="Quiz not open at this time.",
                    quiz_start_ist=quiz_start_ist,
                    quiz_end_ist=quiz_end_ist,
                )

            # Ensure fullscreen was enabled from the client
            fs_flag = request.form.get("fullscreen_enabled")
            if fs_flag != '1':
                flash("❌ Please allow fullscreen and accept Terms & Conditions before starting.", "danger")
                return render_template(
                    "start_quiz.html",
                    quiz=quiz,
                    can_start=can_start,
                    reason="Please allow fullscreen and accept Terms & Conditions.",
                    quiz_start_ist=quiz_start_ist,
                    quiz_end_ist=quiz_end_ist,
                )

            pwd = request.form.get("start_password")
            if pwd != quiz.start_password:
                flash("❌ Wrong quiz password", "danger")
                return render_template(
                    "start_quiz.html",
                    quiz=quiz,
                    can_start=can_start,
                    reason="Wrong password",
                    quiz_start_ist=quiz_start_ist,
                    quiz_end_ist=quiz_end_ist,
                )

            attempt = Attempt.query.filter_by(quiz_id=quiz.id, student_id=current_user.id).first()
            if not attempt:
                attempt = Attempt(quiz_id=quiz.id, student_id=current_user.id, status="in_progress")
                db.session.add(attempt)
                db.session.commit()

            get_attempt_questions(quiz, attempt)

            flash("✅ Quiz started successfully!", "success")
            return redirect(url_for("take_quiz", quiz_id=quiz.id))

        # GET — show page with IST times only
        return render_template(
            "start_quiz.html",
            quiz=quiz,
            can_start=can_start,
            reason=None if can_start else "Quiz not open at this time.",
            quiz_start_ist=quiz_start_ist,
            quiz_end_ist=quiz_end_ist,
        )

    @app.route("/student/quiz/<int:quiz_id>/take", methods=["GET", "POST"])
    @login_required
    def take_quiz(quiz_id):
        if current_user.role != "student":
            abort(403)

        quiz = db.session.get(Quiz, quiz_id)
        if not quiz:
            abort(404)
        # Use IST for display and comparison
        quiz_start_ist = convert_to_ist(quiz.start_time)
        quiz_end_ist = get_effective_quiz_end_time(quiz, current_user.id)
        now = datetime.now(IST)

        # Handle POST (submission) first — accept submissions even if now > quiz_end_ist
        # as long as the attempt exists and is still in_progress. This ensures client-side
        # auto-submit at timeout records a score (zero when no answers selected).
        if request.method == "POST":
            # Load (or create) attempt; do not finalize before reading the POST
            attempt = Attempt.query.filter_by(quiz_id=quiz.id, student_id=current_user.id).first()
            if not attempt:
                # No attempt to submit — redirect student back
                flash("❌ No active attempt found.", "danger")
                return redirect(url_for("student_dashboard"))

            # If the attempt was already finalized, don't accept another submit
            if attempt.status == "submitted":
                flash("✅ You have already submitted this quiz.", "info")
                return redirect(url_for("quiz_result", quiz_id=quiz.id))

            # Proceed to record responses and compute score using assigned question subset
            questions = get_attempt_questions(quiz, attempt)

            # Ensure responses table reflects this submission
            Response.query.filter_by(attempt_id=attempt.id).delete()
            score = 0
            total = len(questions)
            for q in questions:
                selected = request.form.get(f"q_{q.id}")
                if selected and selected.upper() == q.correct.strip().upper():
                    score += 1
                # store even None selections so we have a record
                db.session.add(Response(attempt_id=attempt.id, question_id=q.id, selected=selected))

            attempt.score = score * quiz.marks_per_question
            attempt.max_score = total * quiz.marks_per_question
            attempt.status = "submitted"
            # Prefer to set submitted_at to the quiz end time (naive) when submission is after end
            submitted_dt = quiz_end_ist if quiz_end_ist and now >= quiz_end_ist else now
            try:
                attempt.submitted_at = submitted_dt.replace(tzinfo=None)
            except Exception:
                attempt.submitted_at = submitted_dt
            db.session.add(attempt)
            db.session.commit()

            flash(f"✅ Quiz submitted successfully! You scored {attempt.score}/{attempt.max_score}", "success")
            return redirect(url_for("quiz_result", quiz_id=quiz.id))

        # GET — perform finalization for expired attempts before showing the page
        finalize_expired_attempts_for_student(current_user.id)

        attempt = Attempt.query.filter_by(quiz_id=quiz.id, student_id=current_user.id).first()
        if not attempt:
            attempt = Attempt(quiz_id=quiz.id, student_id=current_user.id, status="in_progress")
            db.session.add(attempt)
            db.session.flush()

        if attempt.status == "submitted":
            flash("✅ You have already submitted this quiz.", "info")
            return redirect(url_for("quiz_result", quiz_id=quiz.id))

        # Fetch assigned question subset for this attempt
        questions = get_attempt_questions(quiz, attempt)

        q_payload = []
        for q in questions:
            # Keep original labels (orig) from CSV: A,B,C,D mapped to their texts
            orig_opts = [("A", q.option_a), ("B", q.option_b), ("C", q.option_c), ("D", q.option_d)]
            # Shuffle the order of option texts for presentation
            random.shuffle(orig_opts)
            # Assign display labels sequentially (A,B,C,D) but preserve orig label for grading
            display_labels = ["A", "B", "C", "D"]
            opts_for_ui = []
            for i, (orig_label, text) in enumerate(orig_opts):
                display_label = display_labels[i]
                opts_for_ui.append({"display": display_label, "orig": orig_label, "text": text})
            # If question text contains a pseudocode block (multiline), split into lead and pseudocode
            lead = q.text
            pseudocode = None
            if isinstance(q.text, str) and '\n' in q.text:
                parts = q.text.split('\n', 1)
                lead = parts[0].strip()
                pseudocode = parts[1].strip()

            q_payload.append({"id": q.id, "text": q.text, "lead": lead, "pseudocode": pseudocode, "image_url": resolve_image_url(q.image_url), "options": opts_for_ui})

        # Remaining time in seconds for client-side timer
        remaining_time_seconds = int((quiz_end_ist - now).total_seconds())
        if remaining_time_seconds < 0:
            remaining_time_seconds = 0

        return render_template(
            "take_quiz.html",
            quiz=quiz,
            q_payload=q_payload,
            remaining_time_seconds=remaining_time_seconds,
        )

    @app.route("/student/quiz/<int:quiz_id>/result")
    @login_required
    def quiz_result(quiz_id):
        if current_user.role != "student":
            abort(403)
        quiz = db.session.get(Quiz, quiz_id)
        # Finalize any expired attempts for this student so results are up-to-date
        finalize_expired_attempts_for_student(current_user.id)
        attempt = Attempt.query.filter_by(quiz_id=quiz.id, student_id=current_user.id).first()
        if not attempt or attempt.status != "submitted":
            flash("❌ You haven't submitted this quiz yet.", "danger")
            return redirect(url_for("student_dashboard"))
        return render_template("quiz_result.html", quiz=quiz, attempt=attempt)

    return app


# ================================
# App Initialization
# ================================
app = create_app()
with app.app_context():
    db.create_all()
    if not User.query.filter_by(email="admin@quiz.com").first():
        admin = User(name="Pranav Reddy", email="admin@quiz.com", role="manager")
        admin.set_password("Pranav123")
        db.session.add(admin)
        db.session.commit()
        print("✅ Default manager account created (admin@quiz.com / Pranav123)")

if __name__ == "__main__":
    # Allow configuring the host and port via environment variables.
    host = os.environ.get("FLASK_RUN_HOST", "172.25.188.177")
    port = int(os.environ.get("PORT", 5000))
    app.run(host=host, port=port, debug=True)
