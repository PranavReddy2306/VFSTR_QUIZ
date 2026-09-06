
import click
from flask.cli import with_appcontext
from app import db
from models import User

@click.command("create-user")
@click.option("--email", prompt=True, help="User email")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="User password")
@click.option("--name", prompt=True, help="Full name")
@click.option("--role", prompt=True, type=click.Choice(["manager","faculty","student"]), help="Role")
@click.option("--department", default="", help="Department (e.g., CSE)")
@click.option("--branch", default="", help="Branch (optional)")
@click.option("--section", default="", help="Section (e.g., A)")
@click.option("--roll-number", default="", help="Student Roll Number (students only)")
@with_appcontext
def create_user(email, password, name, role, department, branch, section, roll_number):
    """Create a new user (manager/faculty/student)."""
    if User.query.filter_by(email=email).first():
        click.echo("❌ User with this email already exists!")
        return
    if roll_number and User.query.filter_by(roll_number=roll_number).first():
        click.echo("❌ User with this roll number already exists!")
        return
    u = User(email=email, name=name, role=role,
             department=department or None, branch=branch or None,
             section=section or None, roll_number=roll_number or None)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    click.echo(f"✅ {role.title()} {name} ({email}) created successfully.")
