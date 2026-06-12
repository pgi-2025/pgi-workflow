from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt_identity
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import os
import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import urllib.request
import urllib.error
import io
import random

# ── APScheduler for daily motivation quotes ──────────────────
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    print("[WARN] APScheduler not installed — daily quotes will not auto-send. Install with: pip install apscheduler")


# ─────────────────────────────────────────────
# LOAD ENV
# ─────────────────────────────────────────────
load_dotenv()

# ─────────────────────────────────────────────
# APP CONFIG
# ─────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

app.config["JWT_SECRET_KEY"]            = os.getenv("JWT_SECRET", "pgi-secret-key-2025")
app.config["JWT_ACCESS_TOKEN_EXPIRES"]  = datetime.timedelta(hours=24)

db_url = os.getenv("DATABASE_URL", "sqlite:///taskflow.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"]   = db_url

if db_url.startswith("postgresql"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"sslmode": "require"},
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

jwt = JWTManager(app)
db  = SQLAlchemy(app)


# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────

class User(db.Model):
    __tablename__ = "users"
    id            = db.Column(db.String(100), primary_key=True)
    email         = db.Column(db.String(200), unique=True, nullable=False)
    password      = db.Column(db.String(300), nullable=False)
    role          = db.Column(db.String(50),  nullable=False)
    name          = db.Column(db.String(200), nullable=False)
    initials      = db.Column(db.String(10))
    team          = db.Column(db.String(100))
    specialty     = db.Column(db.String(100))
    phone         = db.Column(db.String(30))
    department    = db.Column(db.String(100))
    domain        = db.Column(db.String(100))
    joining_date  = db.Column(db.String(50))
    # NEW: Date of birth (YYYY-MM-DD) and profile photo path
    date_of_birth = db.Column(db.String(20))
    profile_photo = db.Column(db.Text)


class Task(db.Model):
    __tablename__ = "tasks"
    id               = db.Column(db.String(100), primary_key=True)
    title            = db.Column(db.String(300), nullable=False)
    desc             = db.Column(db.Text)
    assignedTo       = db.Column(db.String(100))
    assignedBy       = db.Column(db.String(100))
    status           = db.Column(db.String(50))
    priority         = db.Column(db.String(50))
    due              = db.Column(db.String(50))
    msg              = db.Column(db.Text)
    createdAt        = db.Column(db.String(100))
    proof_text       = db.Column(db.Text)
    proof_link       = db.Column(db.Text)
    rejection_reason = db.Column(db.Text)
    # NEW: work completion percentage (0–100), set by founder
    work_completion_percentage = db.Column(db.Float, default=0.0)


class Message(db.Model):
    __tablename__ = "messages"
    id        = db.Column(db.String(100), primary_key=True)
    from_name = db.Column(db.String(200))
    fromId    = db.Column(db.String(100))
    text      = db.Column(db.Text)
    time      = db.Column(db.String(100))        # kept for legacy display
    timestamp = db.Column(db.String(50))          # NEW: ISO datetime e.g. "2026-06-10T09:14:00"
    channel   = db.Column(db.String(100), nullable=False, server_default='all')


class DailyQuote(db.Model):
    """Stores each day's selected motivational quote."""
    __tablename__ = "daily_quotes"
    id         = db.Column(db.Integer, primary_key=True)
    date       = db.Column(db.String(20),  nullable=False, unique=True)  # "YYYY-MM-DD"
    quote_text = db.Column(db.Text,        nullable=False)
    author     = db.Column(db.String(200), default="Unknown")
    sent_at    = db.Column(db.String(100))  # timestamp when quote was dispatched


class QuoteDeliveryLog(db.Model):
    """Tracks per-user delivery to prevent duplicates."""
    __tablename__ = "quote_delivery_log"
    id      = db.Column(db.Integer, primary_key=True)
    date    = db.Column(db.String(20),  nullable=False)
    userId  = db.Column(db.String(100), nullable=False)
    channel = db.Column(db.String(20),  nullable=False)  # 'email' | 'whatsapp' | 'chat'
    status  = db.Column(db.String(20),  default='sent')  # 'sent' | 'failed'
    sent_at = db.Column(db.String(100))


class Attendance(db.Model):
    __tablename__ = "attendance"
    id     = db.Column(db.Integer, primary_key=True)
    userId = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(50),  nullable=False)
    date   = db.Column(db.String(20),  nullable=False)


class MorningMessage(db.Model):
    __tablename__ = "morning_messages"
    id        = db.Column(db.Integer, primary_key=True)
    text      = db.Column(db.Text)
    from_name = db.Column(db.String(200))
    time      = db.Column(db.String(100))
    date      = db.Column(db.String(20))


class Notification(db.Model):
    __tablename__ = "notifications"
    id        = db.Column(db.Integer, primary_key=True)
    userId    = db.Column(db.String(100), nullable=False)
    ntype     = db.Column("type",  db.String(50))
    title     = db.Column(db.String(300))
    body      = db.Column(db.Text)
    is_read   = db.Column("read",  db.Boolean, default=False)
    createdAt = db.Column(db.String(100))


class BirthdayAlert(db.Model):
    """
    Deduplication table — one row per (employee_id, alert_year).
    Prevents the scheduler from creating duplicate birthday notifications
    if it fires more than once on the same day (e.g. restart, misfire retry).
    """
    __tablename__ = "birthday_alerts"
    id          = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(100), nullable=False)
    alert_year  = db.Column(db.Integer,     nullable=False)   # calendar year the alert was sent
    sent_at     = db.Column(db.String(100))
    __table_args__ = (
        db.UniqueConstraint("employee_id", "alert_year", name="uq_birthday_alert"),
    )


class Todo(db.Model):
    __tablename__ = "todos"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.String(100), nullable=False, index=True)
    title      = db.Column(db.Text, nullable=False)
    completed  = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.String(100))


class Rating(db.Model):
    __tablename__ = "ratings"
    id     = db.Column(db.Integer, primary_key=True)
    userId = db.Column(db.String(100), nullable=False)
    score  = db.Column(db.Integer,     nullable=False)
    date   = db.Column(db.String(20),  nullable=False)
    note   = db.Column(db.Text)


class Project(db.Model):
    __tablename__ = "projects"
    id                  = db.Column(db.Integer, primary_key=True)
    name                = db.Column(db.String(300), nullable=False)
    category            = db.Column(db.String(50),  nullable=False)
    client_name         = db.Column(db.String(200))
    status              = db.Column(db.String(50))
    start_date          = db.Column(db.String(50))
    team_members        = db.Column(db.Text)
    description         = db.Column(db.Text)
    created_at          = db.Column(db.String(100))
    progress_percentage = db.Column(db.Float, default=0.0)  # Source of truth — set from Excel Progress column


class Intern(db.Model):
    __tablename__ = "interns_roster"
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(200), nullable=False)
    domain       = db.Column(db.String(100))
    joining_date = db.Column(db.String(50))
    status       = db.Column(db.String(50))
    created_at   = db.Column(db.String(100))


class ProjectTask(db.Model):
    __tablename__ = "project_tasks"
    id           = db.Column(db.Integer, primary_key=True)
    project_name = db.Column(db.String(300), nullable=False, index=True)
    task_name    = db.Column(db.String(300), nullable=False)
    assigned_to  = db.Column(db.String(200))
    status       = db.Column(db.String(50), nullable=False)
    due_date     = db.Column(db.String(50))
    created_at   = db.Column(db.String(100))


# ─────────────────────────────────────────────
# AUDIT LOG MODEL  ← MOVED HERE before db.create_all()
# ─────────────────────────────────────────────

class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id           = db.Column(db.Integer, primary_key=True)
    action       = db.Column(db.String(400), nullable=False)
    performed_by = db.Column(db.String(200))
    timestamp    = db.Column(db.String(100))


class DashboardGoal(db.Model):
    """
    Persistent key-value store for VisionBoard goals.
    One row per key; values stored as strings for flexibility.
    Keys: emp_goal, intern_goal, intship_goal, intship_cur, cert_goal, cert_cur
    """
    __tablename__ = "dashboard_goals"
    key   = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(100), nullable=False, default="0")


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def now_str():
    return datetime.datetime.now().strftime("%I:%M %p")

def today_str():
    return datetime.datetime.now().strftime("%Y-%m-%d")

def make_notif(userId, ntype, title, body):
    n = Notification(
        userId=userId, ntype=ntype,
        title=title, body=body,
        is_read=False,
        createdAt=datetime.datetime.now().strftime("%b %d %I:%M %p")
    )
    db.session.add(n)


def is_founder_like(user):
    """Returns True for both 'founder' and 'founder_assistant' roles."""
    return user and user.role in ("founder", "founder_assistant")


# ─────────────────────────────────────────────
# CREATE / MIGRATE TABLES
# ─────────────────────────────────────────────

with app.app_context():
    db.create_all()

    # Auto-migrate: add new columns to existing tables without losing data
    with db.engine.connect() as conn:
        migrations = [
            "ALTER TABLE messages         ADD COLUMN channel    VARCHAR(100) DEFAULT 'all'",
            "ALTER TABLE attendance       ADD COLUMN date       VARCHAR(20)",
            "ALTER TABLE morning_messages ADD COLUMN date       VARCHAR(20)",
            "ALTER TABLE users            ADD COLUMN department VARCHAR(100)",
            "ALTER TABLE users            ADD COLUMN domain     VARCHAR(100)",
            "ALTER TABLE users            ADD COLUMN joining_date VARCHAR(50)",
            # NEW columns
            "ALTER TABLE tasks            ADD COLUMN work_completion_percentage FLOAT DEFAULT 0",
            "ALTER TABLE messages         ADD COLUMN timestamp  VARCHAR(50)",
            "ALTER TABLE projects         ADD COLUMN progress_percentage FLOAT DEFAULT 0",
            # Date of birth and profile photo (safe migration — existing data untouched)
            "ALTER TABLE users            ADD COLUMN date_of_birth VARCHAR(20)",
            "ALTER TABLE users            ADD COLUMN profile_photo TEXT",
            # Birthday alert dedup table (created by SQLAlchemy — migration only needed for new columns)
            "ALTER TABLE birthday_alerts  ADD COLUMN sent_at VARCHAR(100)",
            # dashboard_goals uses SQLAlchemy create_all — no column migration needed
        ]
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                conn.rollback()

    # Ensure profile photos upload directory exists
    os.makedirs(os.path.join(os.path.dirname(__file__), "uploads", "profile_photos"), exist_ok=True)


# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "message": "TaskFlow Backend Running"})


# ─────────────────────────────────────────────
# SEED
# ─────────────────────────────────────────────

@app.route("/api/seed-update")
def seed_update():

    founder = db.session.get(User, "founder")

    if not founder:
        founder = User(
            id="founder",
            email="pgiworkflow@gmail.com",
            password=generate_password_hash("founder2025"),
            role="founder",
            name="Laven Lokesh B",
            initials="LL"
        )
        db.session.add(founder)
    else:
        founder.email    = "pgiworkflow@gmail.com"
        founder.password = generate_password_hash("founder2025")
        founder.name     = "Laven Lokesh B"
        founder.initials = "LL"

    employees = [
        dict(id="u_abinash",   email="abinashbolt@gmail.com",     name="Abinash R",          initials="AR", role="employee", team="technical"),
        dict(id="u_rahul",     email="mail2rahul.mk@gmail.com",   name="Rahul M",             initials="RM", role="employee", team="technical"),
        dict(id="u_amitesh",   email="amitesh4122005@gmail.com",  name="Amitesh M",           initials="AM", role="employee", team="technical"),
        dict(id="u_sadhana",   email="trainings.pgi@gmail.com",   name="Sadhana M",           initials="SM", role="employee", team="bizdev"),
        dict(id="u_prassanna", email="kpkkumar1619@gmail.com",    name="Prassanna Kumar K",   initials="PK", role="employee", team="content"),
    ]

    created = []
    for emp in employees:
        existing = db.session.get(User, emp["id"])
        if not existing:
            new_user = User(
                id=emp["id"], email=emp["email"],
                password=generate_password_hash("emp2025"),
                role=emp["role"], name=emp["name"],
                initials=emp["initials"], team=emp["team"]
            )
            db.session.add(new_user)
            created.append(emp["name"])

    db.session.commit()
    return jsonify({"success": True, "message": "Database seeded successfully", "created_employees": created})


# ─────────────────────────────────────────────
# AUTH — LOGIN
# ─────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def login():
    data     = request.get_json()
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "")
    role     = data.get("role")

    # founder_assistant members log in through the Employee tab.
    # When role=='employee', also accept founder_assistant accounts.
    if role == "employee":
        user = User.query.filter(
            db.func.lower(User.email) == email,
            User.role.in_(["employee", "founder_assistant"])
        ).first()
    else:
        user = User.query.filter(
            db.func.lower(User.email) == email,
            User.role == role
        ).first()

    if not user:
        return jsonify({"error": "User not found"}), 404

    if not check_password_hash(user.password, password):
        return jsonify({"error": "Invalid password"}), 401

    token = create_access_token(identity=user.id)
    return jsonify({
        "token": token,
        "user": {
            "id": user.id, "email": user.email, "role": user.role,
            "name": user.name, "initials": user.initials,
            "team": user.team, "specialty": user.specialty
        }
    })


# ─────────────────────────────────────────────
# CURRENT USER
# ─────────────────────────────────────────────

@app.route("/api/me")
@jwt_required()
def me():
    user = db.session.get(User, get_jwt_identity())
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "id": user.id, "email": user.email, "role": user.role,
        "name": user.name, "initials": user.initials,
        "team": user.team, "specialty": user.specialty
    })


# ─────────────────────────────────────────────
# USERS — LIST, CREATE, DELETE, EDIT
# ─────────────────────────────────────────────

@app.route("/api/users")
@jwt_required()
def get_users():
    return jsonify([{
        "id": u.id, "email": u.email, "role": u.role,
        "name": u.name, "initials": u.initials,
        "team": u.team, "specialty": u.specialty,
        "phone": u.phone, "department": u.department,
        "domain": u.domain, "joining_date": u.joining_date,
        "date_of_birth": u.date_of_birth,
        "profile_photo": u.profile_photo
    } for u in User.query.all()])


@app.route("/api/users", methods=["POST"])
@jwt_required()
def create_user():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Only founder can create users"}), 403

    data  = request.get_json()
    email = data.get("email", "").strip().lower()

    if User.query.filter(db.func.lower(User.email) == email).first():
        return jsonify({"error": "Email already exists"}), 400

    name     = data.get("name", "").strip()
    initials = data.get("initials") or "".join(w[0].upper() for w in name.split()[:2])

    # Date of birth validation: must not be in the future
    dob = (data.get("date_of_birth") or "").strip() or None
    if dob:
        try:
            dob_date = datetime.datetime.strptime(dob, "%Y-%m-%d").date()
            if dob_date > datetime.date.today():
                return jsonify({"error": "Date of birth cannot be a future date"}), 400
        except ValueError:
            return jsonify({"error": "Invalid date_of_birth format. Use YYYY-MM-DD"}), 400

    user = User(
        id=data.get("id") or ("u" + str(int(datetime.datetime.now().timestamp() * 1000))),
        email=email,
        password=generate_password_hash(data.get("password", "emp2025")),
        role=data.get("role", "employee"),
        name=name,
        initials=initials,
        team=data.get("team"),
        specialty=data.get("specialty"),
        phone=(data.get("phone") or "").strip() or None,
        department=(data.get("department") or "").strip() or None,
        domain=(data.get("domain") or "").strip() or None,
        joining_date=(data.get("joining_date") or "").strip() or None,
        date_of_birth=dob,
        profile_photo=(data.get("profile_photo") or "").strip() or None
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({"success": True, "user": {
        "id": user.id, "name": user.name, "email": user.email,
        "role": user.role, "initials": user.initials,
        "team": user.team, "specialty": user.specialty,
        "phone": user.phone, "department": user.department,
        "domain": user.domain, "joining_date": user.joining_date,
        "date_of_birth": user.date_of_birth,
        "profile_photo": user.profile_photo
    }})


@app.route("/api/users/<user_id>", methods=["DELETE"])
@jwt_required()
def delete_user(user_id):
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Only founder can delete users"}), 403
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    db.session.delete(user)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/users/<user_id>", methods=["PUT"])
@jwt_required()
def update_user(user_id):
    caller = db.session.get(User, get_jwt_identity())
    if not caller or not is_founder_like(caller):
        return jsonify({"error": "Only founder can edit users"}), 403

    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    data = request.get_json() or {}

    # Email validation and duplicate check
    new_email = (data.get("email") or "").strip().lower()
    if new_email and new_email != user.email.lower():
        import re
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", new_email):
            return jsonify({"error": "Invalid email format"}), 400
        duplicate = User.query.filter(
            db.func.lower(User.email) == new_email,
            User.id != user_id
        ).first()
        if duplicate:
            return jsonify({"error": "Email already in use by another user"}), 400
        user.email = new_email

    # Name validation
    new_name = (data.get("name") or "").strip()
    if new_name:
        user.name = new_name
        words = new_name.split()
        user.initials = "".join(w[0].upper() for w in words[:2])

    # Safe field updates
    if "role"         in data: user.role         = (data["role"]         or "").strip() or user.role
    if "team"         in data: user.team         = (data["team"]         or "").strip() or None
    if "specialty"    in data: user.specialty    = (data["specialty"]    or "").strip() or None
    if "phone"        in data: user.phone        = (data["phone"]        or "").strip() or None
    if "department"   in data: user.department   = (data["department"]   or "").strip() or None
    if "domain"       in data: user.domain       = (data["domain"]       or "").strip() or None
    if "joining_date" in data: user.joining_date = (data["joining_date"] or "").strip() or None

    # Date of birth: validate and update
    if "date_of_birth" in data:
        dob = (data["date_of_birth"] or "").strip() or None
        if dob:
            try:
                dob_date = datetime.datetime.strptime(dob, "%Y-%m-%d").date()
                if dob_date > datetime.date.today():
                    return jsonify({"error": "Date of birth cannot be a future date"}), 400
            except ValueError:
                return jsonify({"error": "Invalid date_of_birth format. Use YYYY-MM-DD"}), 400
        user.date_of_birth = dob

    # Profile photo: allow update (path stored; upload handled by separate endpoint)
    if "profile_photo" in data:
        user.profile_photo = (data["profile_photo"] or "").strip() or None

    # Audit log — now works because AuditLog table exists
    log_entry = AuditLog(
        action       = f"Founder updated {user.name}'s profile",
        performed_by = caller.name,
        timestamp    = datetime.datetime.now().strftime("%b %d, %Y %I:%M %p")
    )
    db.session.add(log_entry)
    db.session.commit()

    return jsonify({"success": True, "user": {
        "id":           user.id,
        "name":         user.name,
        "email":        user.email,
        "role":         user.role,
        "initials":     user.initials,
        "team":         user.team,
        "specialty":    user.specialty,
        "phone":        user.phone,
        "department":   user.department,
        "domain":       user.domain,
        "joining_date": user.joining_date,
        "date_of_birth": user.date_of_birth,
        "profile_photo": user.profile_photo
    }})



@app.route("/api/users/<user_id>/upload-photo", methods=["POST"])
@jwt_required()
def upload_profile_photo(user_id):
    """Upload a profile photo for a user. Stores file in uploads/profile_photos/."""
    caller = db.session.get(User, get_jwt_identity())
    if not caller or not is_founder_like(caller):
        return jsonify({"error": "Only founder can upload profile photos"}), 403
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    if "photo" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    photo = request.files["photo"]
    if not photo or photo.filename == "":
        return jsonify({"error": "Empty file"}), 400
    ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
    MAX_SIZE_BYTES = 5 * 1024 * 1024
    ext = photo.filename.rsplit(".", 1)[-1].lower() if "." in photo.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Invalid file type '.{ext}'. Allowed: jpg, jpeg, png, webp"}), 400
    photo.seek(0, 2)
    file_size = photo.tell()
    photo.seek(0)
    if file_size > MAX_SIZE_BYTES:
        return jsonify({"error": "File too large. Maximum allowed size is 5 MB"}), 400
    safe_filename = secure_filename(f"{user_id}.{ext}")
    upload_dir = os.path.join(os.path.dirname(__file__), "uploads", "profile_photos")
    os.makedirs(upload_dir, exist_ok=True)
    save_path = os.path.join(upload_dir, safe_filename)
    photo.save(save_path)
    photo_url = f"/uploads/profile_photos/{safe_filename}"
    user.profile_photo = photo_url
    db.session.commit()
    return jsonify({"success": True, "profile_photo": photo_url})


@app.route("/uploads/profile_photos/<filename>")
def serve_profile_photo(filename):
    """Serve uploaded profile photos."""
    safe = secure_filename(filename)
    path = os.path.join(os.path.dirname(__file__), "uploads", "profile_photos", safe)
    if not os.path.exists(path):
        return jsonify({"error": "Not found"}), 404
    return send_file(path)


@app.route("/api/audit-logs")
@jwt_required()
def get_audit_logs():
    caller = db.session.get(User, get_jwt_identity())
    if not caller or not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    logs = AuditLog.query.order_by(AuditLog.id.desc()).limit(50).all()
    return jsonify([{
        "id": l.id, "action": l.action,
        "performed_by": l.performed_by, "timestamp": l.timestamp
    } for l in logs])


# ─────────────────────────────────────────────
# TASKS — GET, CREATE, UPDATE, DELETE
# ─────────────────────────────────────────────

def task_to_dict(t):
    proof = {"text": t.proof_text, "link": t.proof_link} if (t.proof_text or t.proof_link) else None
    return {
        "id": t.id, "title": t.title, "desc": t.desc,
        "assignedTo": t.assignedTo, "assignedBy": t.assignedBy,
        "status": t.status, "priority": t.priority,
        "due": t.due, "msg": t.msg, "createdAt": t.createdAt,
        "proof": proof,
        "rejection_reason": t.rejection_reason,
        "work_completion_percentage": t.work_completion_percentage or 0
    }


@app.route("/api/tasks")
@jwt_required()
def get_tasks():
    uid  = get_jwt_identity()
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "User not found"}), 404
    tasks = Task.query.all() if is_founder_like(user) else Task.query.filter_by(assignedTo=uid).all()
    return jsonify([task_to_dict(t) for t in tasks])


@app.route("/api/tasks", methods=["POST"])
@jwt_required()
def create_task():
    db.session.rollback()

    uid     = get_jwt_identity()
    founder = db.session.get(User, uid)
    if not founder:
        return jsonify({"error": "Founder account not found"}), 404
    if not is_founder_like(founder):
        return jsonify({"error": "Only founder can assign tasks"}), 403

    data        = request.get_json() or {}
    assignee_id = data.get("assignedTo", "").strip()

    if not assignee_id:
        return jsonify({"error": "Please select a team member to assign the task to"}), 400

    assignee = db.session.get(User, assignee_id)
    if not assignee:
        return jsonify({"error": "Selected team member not found. Please refresh and try again."}), 404

    try:
        # Validate and clamp work_completion_percentage
        wcp_raw = data.get("work_completion_percentage", 0)
        try:
            wcp = float(wcp_raw)
        except (TypeError, ValueError):
            wcp = 0.0
        wcp = max(0.0, min(100.0, wcp))

        task = Task(
            id        = "t" + str(int(datetime.datetime.now().timestamp() * 1000)),
            title     = (data.get("title") or "").strip(),
            desc      = (data.get("desc")  or "").strip(),
            assignedTo= assignee_id,
            assignedBy= uid,
            status    = "pending",
            priority  = data.get("priority", "medium"),
            due       = data.get("due") or "TBD",
            msg       = (data.get("msg") or "").strip(),
            createdAt = datetime.datetime.now().strftime("%b %d, %Y %I:%M %p"),
            work_completion_percentage = wcp
        )
        db.session.add(task)

        make_notif(
            userId=assignee_id, ntype="task",
            title="New Task Assigned",
            body='"' + task.title + '" was assigned to you by ' + founder.name + '. Due: ' + (task.due or "TBD")
        )

        db.session.commit()
        return jsonify({"success": True, "task": task_to_dict(task)})

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Database error while creating task: " + str(e)}), 500


@app.route("/api/tasks/<task_id>/status", methods=["PATCH"])
@jwt_required()
def update_task_status(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    data = request.get_json()
    task.status = data.get("status")
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/tasks/<task_id>/proof", methods=["POST"])
@jwt_required()
def submit_proof(task_id):
    uid  = get_jwt_identity()
    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    data             = request.get_json()
    task.status      = "submitted"
    task.proof_text  = data.get("text")
    task.proof_link  = data.get("link")

    submitter = db.session.get(User, uid)
    founder   = User.query.filter_by(role="founder").first()
    if founder:
        make_notif(
            userId=founder.id, ntype="proof",
            title="Proof Submitted",
            body=submitter.name + ' submitted proof for "' + task.title + '"'
        )

    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/tasks/<task_id>/verify", methods=["POST"])
@jwt_required()
def verify_task(task_id):
    uid  = get_jwt_identity()
    user = db.session.get(User, uid)
    if not is_founder_like(user):
        return jsonify({"error": "Only founder can verify"}), 403

    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    action = request.get_json().get("action")
    reason = request.get_json().get("reason", "").strip()

    if action == "approve":
        task.status = "completed"
        task.rejection_reason = None
        make_notif(
            userId=task.assignedTo, ntype="task",
            title="Task Completed!",
            body='Your task "' + task.title + '" was approved and marked complete!'
        )
    elif action == "reject":
        task.status     = "rejected"
        task.proof_text = None
        task.proof_link = None
        task.rejection_reason = reason if reason else None
        notif_body = 'Your proof for "' + task.title + '" was rejected. Please resubmit.'
        if reason:
            notif_body += ' Reason: ' + reason
        make_notif(
            userId=task.assignedTo, ntype="task",
            title="Proof Rejected",
            body=notif_body
        )

    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/tasks/<task_id>", methods=["DELETE"])
@jwt_required()
def delete_task(task_id):
    user = db.session.get(User, get_jwt_identity())
    if not is_founder_like(user):
        return jsonify({"error": "Only founder can delete tasks"}), 403
    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    db.session.delete(task)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/tasks/<task_id>/proof", methods=["DELETE"])
@jwt_required()
def delete_proof(task_id):
    """Delete submitted proof. Allowed for: task owner OR founder/founder_assistant."""
    uid  = get_jwt_identity()
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    # Access control: only the assigned user or a founder-like user may delete proof
    if not is_founder_like(user) and task.assignedTo != uid:
        return jsonify({"error": "Forbidden — you can only delete your own proof"}), 403

    # Delete any uploaded proof file from disk
    if task.proof_link:
        try:
            # If proof_link is a local /uploads/ path (not an external URL), delete the file
            if task.proof_link.startswith("/uploads/"):
                file_path = os.path.join(os.path.dirname(__file__), task.proof_link.lstrip("/"))
                if os.path.isfile(file_path):
                    os.remove(file_path)
        except Exception as e:
            print(f"[WARN] Could not delete proof file: {e}")

    task.proof_text = None
    task.proof_link = None
    task.status     = "pending"
    db.session.commit()
    return jsonify({"success": True, "task": task_to_dict(task)})


@app.route("/api/tasks/<task_id>/work-percentage", methods=["PATCH"])
@jwt_required()
def update_work_percentage(task_id):
    """Founder-only: update work_completion_percentage for a task."""
    user = db.session.get(User, get_jwt_identity())
    if not is_founder_like(user):
        return jsonify({"error": "Only founder can update work percentage"}), 403

    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    data = request.get_json() or {}
    try:
        pct = float(data.get("work_completion_percentage", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid percentage value"}), 400

    if pct < 0 or pct > 100:
        return jsonify({"error": "Percentage must be between 0 and 100"}), 400

    task.work_completion_percentage = pct
    db.session.commit()
    return jsonify({"success": True, "task": task_to_dict(task)})


@app.route("/api/tasks/reset-completed", methods=["DELETE"])
@jwt_required()
def reset_completed_tasks():
    user = db.session.get(User, get_jwt_identity())
    if not is_founder_like(user):
        return jsonify({"error": "Only founder can reset tasks"}), 403
    completed = Task.query.filter_by(status="completed").all()
    count = len(completed)
    for task in completed:
        db.session.delete(task)
    db.session.commit()
    return jsonify({"success": True, "deleted": count})


# ─────────────────────────────────────────────
# MESSAGES — GET BY CHANNEL, SEND
# ─────────────────────────────────────────────

@app.route("/api/messages")
@jwt_required()
def get_messages():
    channel = request.args.get("channel", "all")
    msgs    = Message.query.filter_by(channel=channel).order_by(Message.id.asc()).all()
    return jsonify([{
        "id": m.id, "from": m.from_name, "fromId": m.fromId,
        "text": m.text, "time": m.time, "channel": m.channel,
        "timestamp": m.timestamp or ""   # ISO datetime for WhatsApp-style grouping
    } for m in msgs])


@app.route("/api/messages", methods=["POST"])
@jwt_required()
def send_message():
    uid  = get_jwt_identity()
    user = db.session.get(User, uid)
    data = request.get_json()
    channel = data.get("channel", "all")

    now = datetime.datetime.now()
    msg = Message(
        id        = "m" + str(int(now.timestamp() * 1000)),
        from_name = user.name,
        fromId    = uid,
        text      = data.get("text", "").strip(),
        time      = now.strftime("%I:%M %p"),
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S"),   # ISO for grouping
        channel   = channel
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({"success": True, "message": {
        "id": msg.id, "from": msg.from_name, "fromId": msg.fromId,
        "text": msg.text, "time": msg.time, "channel": msg.channel,
        "timestamp": msg.timestamp or ""
    }})


@app.route("/api/messages/count")
@jwt_required()
def message_counts():
    channels_param = request.args.get("channels", "all")
    channels = channels_param.split(",")
    result = {}
    for ch in channels:
        result[ch.strip()] = Message.query.filter_by(channel=ch.strip()).count()
    return jsonify(result)


# ─────────────────────────────────────────────
# MORNING MESSAGE
# ─────────────────────────────────────────────

def send_morning_email(sender_name, message_text, recipients):
    if not recipients:
        return False, "No recipient email addresses found."

    brevo_key     = os.getenv("BREVO_API_KEY", "").strip()
    smtp_email    = os.getenv("SMTP_EMAIL", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()

    plain_body = (
        f"Good morning, {sender_name} here\n\n"
        f"{message_text}\n\n"
        f"Have a productive day!\n-- {sender_name}, Plant Green Inertia"
    )
    html_body = (
        '''<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:24px;'''
        '''background:#fffdf5;border:1px solid #e5d98b;border-radius:8px">'''
        f'''<h2 style="color:#b8860b;margin-top:0">Good morning, {sender_name} here</h2>'''
        f'''<p style="color:#333;font-size:15px;line-height:1.7">{message_text}</p>'''
        '''<hr style="border:none;border-top:1px solid #e5d98b;margin:20px 0">'''
        f'''<p style="color:#888;font-size:13px">Have a productive day! &mdash;'''
        f''' <strong>{sender_name}</strong>, Plant Green Inertia</p>'''
        '''</div>'''
    )

    if brevo_key:
        sent   = 0
        failed = []
        for recipient in recipients:
            payload = json.dumps({
                "sender":      {"name": "Plant Green Inertia", "email": smtp_email or "pgiworkflow@gmail.com"},
                "to":          [{"email": recipient}],
                "subject":     f"Morning Message from {sender_name} - Plant Green Inertia",
                "textContent": plain_body,
                "htmlContent": html_body,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.brevo.com/v3/smtp/email",
                data    = payload,
                headers = {
                    "api-key":      brevo_key,
                    "Content-Type": "application/json",
                    "Accept":       "application/json",
                },
                method = "POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp.read()
                    sent += 1
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                failed.append(recipient)
                print(f"[EMAIL/Brevo] HTTPError {e.code} for {recipient}: {body}")
            except Exception as exc:
                failed.append(recipient)
                print(f"[EMAIL/Brevo] Error for {recipient}: {exc}")

        if sent:
            return True, f"Sent via Brevo to {sent} recipient(s). Failed: {len(failed)}."
        return False, f"Brevo: all {len(failed)} sends failed."

    if not smtp_email or not smtp_password:
        return False, "No email service configured."

    sent   = []
    failed = []
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(smtp_email, smtp_password)
            for recipient in recipients:
                try:
                    mime_msg = MIMEMultipart("alternative")
                    mime_msg["Subject"] = f"Morning Message from {sender_name} - Plant Green Inertia"
                    mime_msg["From"]    = f"Plant Green Inertia <{smtp_email}>"
                    mime_msg["To"]      = recipient
                    mime_msg.attach(MIMEText(plain_body, "plain", "utf-8"))
                    mime_msg.attach(MIMEText(html_body,  "html",  "utf-8"))
                    server.sendmail(smtp_email, recipient, mime_msg.as_string())
                    sent.append(recipient)
                except Exception as per_exc:
                    failed.append(recipient)
                    print(f"[EMAIL/SMTP] Failed for {recipient}: {per_exc}")
    except smtplib.SMTPAuthenticationError as auth_err:
        return False, f"Gmail auth failed: {auth_err}."
    except Exception as conn_err:
        return False, f"SMTP connection error: {conn_err}"

    if sent:
        return True, f"Sent via SMTP to {len(sent)} recipient(s). Failed: {len(failed)}."
    return False, f"SMTP: all {len(failed)} sends failed."


@app.route("/api/morning-message", methods=["GET"])
@jwt_required()
def get_morning_message():
    msg = MorningMessage.query.filter_by(date=today_str()).order_by(MorningMessage.id.desc()).first()
    if not msg:
        return jsonify({})
    return jsonify({"text": msg.text, "from": msg.from_name, "time": msg.time})


@app.route("/api/morning-message", methods=["POST"])
@jwt_required()
def post_morning_message():
    uid  = get_jwt_identity()
    user = db.session.get(User, uid)
    data = request.get_json()

    msg = MorningMessage(
        text=data.get("text"), from_name=user.name,
        time=now_str(), date=today_str()
    )
    db.session.add(msg)

    message_text = str(data.get("text", ""))
    non_founders = User.query.filter(User.role != "founder").all()
    for u in non_founders:
        make_notif(
            userId=u.id, ntype="morning",
            title="Morning Message",
            body=message_text[:120]
        )

    db.session.commit()

    recipient_emails = [u.email for u in non_founders if u.email]
    email_sent, email_detail = send_morning_email(user.name, message_text, recipient_emails)

    return jsonify({"success": True, "email_sent": email_sent, "email_detail": email_detail, "recipients": recipient_emails})


@app.route("/api/test-email")
@jwt_required()
def test_email():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403

    smtp_email    = os.getenv("SMTP_EMAIL", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    brevo_key     = os.getenv("BREVO_API_KEY", "").strip()

    success, detail = send_morning_email(
        sender_name  = caller.name,
        message_text = "TEST: This is a TaskFlow test email confirming that email delivery is working correctly on Render.",
        recipients   = [smtp_email]
    )

    return jsonify({
        "success":       success,
        "detail":        detail,
        "smtp_email":    smtp_email,
        "smtp_pass_set": bool(smtp_password),
        "brevo_key_set": bool(brevo_key),
        "email_service": "Brevo (HTTP)" if brevo_key else "SMTP (Gmail)"
    })


# ─────────────────────────────────────────────
# RATINGS
# ─────────────────────────────────────────────

@app.route("/api/ratings", methods=["GET"])
@jwt_required()
def get_ratings():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403

    today_ratings = {
        r.userId: {"score": r.score, "note": r.note}
        for r in Rating.query.filter_by(date=today_str()).all()
    }

    from sqlalchemy import desc as sa_desc
    thirty_days_ago = (datetime.datetime.utcnow() - datetime.timedelta(days=29)).strftime("%Y-%m-%d")
    history = [
        {"date": r.date, "userId": r.userId, "score": r.score}
        for r in Rating.query
            .filter(Rating.date >= thirty_days_ago)
            .order_by(Rating.date)
            .all()
    ]

    return jsonify({"today": today_ratings, "history": history})


@app.route("/api/ratings", methods=["POST"])
@jwt_required()
def save_rating():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403

    data  = request.get_json()
    uid   = data.get("userId")
    score = int(data.get("score", 0))
    note  = data.get("note", "").strip()

    if not uid or score < 1 or score > 10:
        return jsonify({"error": "Invalid data"}), 400

    existing = Rating.query.filter_by(userId=uid, date=today_str()).first()
    if existing:
        existing.score = score
        existing.note  = note or existing.note
    else:
        db.session.add(Rating(userId=uid, score=score, note=note, date=today_str()))

    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/ratings/reset", methods=["POST"])
@jwt_required()
def reset_ratings():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    count = Rating.query.count()
    Rating.query.delete()
    db.session.commit()
    return jsonify({"success": True, "deleted": count})


# ─────────────────────────────────────────────
# ATTENDANCE
# ─────────────────────────────────────────────

@app.route("/api/attendance", methods=["GET"])
@jwt_required()
def get_attendance():
    today   = today_str()
    records = {a.userId: a.status for a in Attendance.query.filter_by(date=today).all()}
    users   = User.query.filter(User.role != "founder").all()
    return jsonify([{
        "userId": u.id, "name": u.name, "initials": u.initials,
        "role": u.role, "team": u.team,
        "status": records.get(u.id, "absent"), "date": today
    } for u in users])


@app.route("/api/attendance", methods=["PATCH"])
@jwt_required()
def mark_attendance():
    data   = request.get_json()
    uid    = data.get("userId")
    status = data.get("status")
    today  = today_str()

    att = Attendance.query.filter_by(userId=uid, date=today).first()
    if att:
        att.status = status
    else:
        db.session.add(Attendance(userId=uid, status=status, date=today))

    db.session.commit()
    return jsonify({"success": True, "status": status, "date": today})


@app.route("/api/attendance/history")
@jwt_required()
def attendance_history():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    records = Attendance.query.all()
    return jsonify([{
        "userId": a.userId, "status": a.status, "date": a.date
    } for a in records])


# ─────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────

@app.route("/api/notifications")
@jwt_required()
def get_notifications():
    uid    = get_jwt_identity()
    notifs = Notification.query.filter_by(userId=uid, is_read=False)\
                               .order_by(Notification.id.desc())\
                               .limit(30).all()
    return jsonify([{
        "id": n.id, "type": n.ntype,
        "title": n.title, "body": n.body,
        "createdAt": n.createdAt
    } for n in notifs])


@app.route("/api/notifications/read-all", methods=["POST"])
@jwt_required()
def mark_all_read():
    uid = get_jwt_identity()
    Notification.query.filter_by(userId=uid, is_read=False).update({"is_read": True})
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/notifications/<int:nid>/read", methods=["POST"])
@jwt_required()
def mark_one_read(nid):
    uid   = get_jwt_identity()
    notif = db.session.get(Notification, nid)
    if notif and notif.userId == uid:
        notif.is_read = True
        db.session.commit()
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# PROJECTS — CRUD
# ─────────────────────────────────────────────

def project_to_dict(p):
    return {
        "id": p.id, "name": p.name, "category": p.category,
        "client_name": p.client_name, "status": p.status,
        "start_date": p.start_date, "team_members": p.team_members,
        "description": p.description, "created_at": p.created_at,
        "progress_percentage": float(p.progress_percentage or 0)
    }


@app.route("/api/projects")
@jwt_required()
def get_projects():
    category = request.args.get("category")
    q = Project.query
    if category:
        q = q.filter_by(category=category)
    return jsonify([project_to_dict(p) for p in q.order_by(Project.id.desc()).all()])


@app.route("/api/projects", methods=["POST"])
@jwt_required()
def create_project():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json() or {}
    cat  = data.get("category", "").strip()
    if cat not in ("Government", "Private", "B2C"):
        return jsonify({"error": "category must be Government, Private or B2C"}), 400

    # Parse progress_percentage from form input
    try:
        pct = float(str(data.get("progress_percentage", 0)).replace("%", "").strip())
    except (TypeError, ValueError):
        pct = 0.0
    if not (0 <= pct <= 100):
        return jsonify({"error": "Progress must be between 0 and 100."}), 400

    p = Project(
        name=data.get("name", "").strip(), category=cat,
        client_name=data.get("client_name", "").strip(),
        status=data.get("status", "Active").strip(),
        start_date=data.get("start_date", "").strip(),
        team_members=data.get("team_members", "").strip(),
        description=data.get("description", "").strip(),
        progress_percentage=pct,
        created_at=datetime.datetime.now().strftime("%b %d, %Y")
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({"success": True, "project": project_to_dict(p)})


@app.route("/api/projects/bulk", methods=["POST"])
@jwt_required()
def bulk_projects():
    import logging
    log = logging.getLogger("pgi.projects.bulk")

    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    rows = data.get("projects", [])

    if not isinstance(rows, list):
        return jsonify({"error": "projects must be a list"}), 400

    try:
        Project.query.delete()
        created = 0
        skipped = 0
        for row in rows:
            cat  = (row.get("category") or "").strip()
            name = (row.get("name") or "").strip()
            if not name:
                skipped += 1
                continue
            valid_cats = {"government": "Government", "private": "Private", "b2c": "B2C"}
            cat_norm   = valid_cats.get(cat.lower(), "Government")
            # Parse progress_percentage
            try:
                pct = float(str(row.get("progress_percentage", row.get("progress", 0))).replace("%", "").strip())
                pct = max(0.0, min(100.0, pct))
            except (TypeError, ValueError):
                pct = 0.0
            db.session.add(Project(
                name=name, category=cat_norm,
                client_name=(row.get("client_name") or "").strip(),
                status=(row.get("status") or "Active").strip(),
                start_date=(row.get("start_date") or "").strip(),
                team_members=(row.get("team_members") or "").strip(),
                description=(row.get("description") or "").strip(),
                progress_percentage=pct,
                created_at=datetime.datetime.now().strftime("%b %d, %Y")
            ))
            created += 1

        db.session.commit()
        return jsonify({"success": True, "created": created, "skipped": skipped})

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Database error: {str(e)}"}), 500


@app.route("/api/projects/upload-xlsx", methods=["POST"])
@jwt_required()
def upload_projects_xlsx():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    f = request.files["file"]
    if not f or f.filename == "":
        return jsonify({"error": "Empty file received"}), 400

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ("xlsx", "xls", "csv"):
        return jsonify({"error": f"Unsupported file type: .{ext}"}), 400

    try:
        import pandas as pd

        file_bytes = f.read()
        if not file_bytes:
            return jsonify({"error": "File is empty"}), 400

        if ext == "csv":
            df = pd.read_csv(io.BytesIO(file_bytes))
        else:
            df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl" if ext == "xlsx" else "xlrd")

        df.columns = [str(c).strip() for c in df.columns]

        if "Project Name" not in df.columns and "Name" not in df.columns:
            return jsonify({"error": f"Missing 'Project Name' column. Found: {', '.join(df.columns)}"}), 400

        VALID_CATS = {"government": "Government", "private": "Private", "b2c": "B2C"}

        def col(row, *names):
            for n in names:
                v = row.get(n)
                if v and str(v).strip() and str(v).strip().lower() not in ("nan", ""):
                    return str(v).strip()
            return ""

        rows    = []
        skipped = 0
        for _, row in df.iterrows():
            row  = row.to_dict()
            name = col(row, "Project Name", "project name", "Name")
            if not name:
                skipped += 1
                continue
            cat_raw = col(row, "Category", "category") or "Government"
            cat     = VALID_CATS.get(cat_raw.lower(), "Government")

            # ── Read Progress column — the single source of truth ──
            raw_pct = row.get("Progress", row.get("progress", 0))
            try:
                pct = float(str(raw_pct).replace("%", "").strip())
                # Handle fraction style: 0.5 → 50
                if 0 < pct <= 1.0:
                    pct = pct * 100
                pct = max(0.0, min(100.0, pct))
            except (TypeError, ValueError):
                pct = 0.0

            rows.append({
                "name": name, "category": cat,
                "client_name":       col(row, "Client Name",  "client name",  "Client"),
                "status":            col(row, "Status",       "status")       or "Active",
                "start_date":        col(row, "Start Date",   "start date",   "Date"),
                "team_members":      col(row, "Team Members", "team members", "Team"),
                "description":       col(row, "Description",  "description"),
                "progress_percentage": pct,
            })

        if not rows:
            return jsonify({"error": "No valid project rows found"}), 400

        Project.query.delete()
        created = 0
        for row in rows:
            db.session.add(Project(
                name=row["name"], category=row["category"],
                client_name=row["client_name"], status=row["status"],
                start_date=row["start_date"], team_members=row["team_members"],
                description=row["description"],
                progress_percentage=row["progress_percentage"],
                created_at=datetime.datetime.now().strftime("%b %d, %Y")
            ))
            created += 1
            print(f"[IMPORT] {row['name']} -> {row['progress_percentage']}")

        db.session.commit()
        return jsonify({"success": True, "created": created, "skipped": skipped})

    except ImportError as e:
        return jsonify({"error": f"Server missing library: {e}"}), 500
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"File processing error: {str(e)}"}), 500


@app.route("/api/projects/<int:pid>", methods=["PUT"])
@jwt_required()
def update_project(pid):
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    p = db.session.get(Project, pid)
    if not p:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    for field in ("name", "category", "client_name", "status", "start_date", "team_members", "description"):
        if field in data:
            setattr(p, field, (data[field] or "").strip())
    if "progress_percentage" in data:
        try:
            pct = float(str(data["progress_percentage"]).replace("%", "").strip())
            pct = max(0.0, min(100.0, pct))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid progress value"}), 400
        if not (0 <= pct <= 100):
            return jsonify({"error": "Progress must be between 0 and 100."}), 400
        p.progress_percentage = pct
    db.session.commit()
    return jsonify({"success": True, "project": project_to_dict(p)})


@app.route("/api/projects/<int:pid>", methods=["DELETE"])
@jwt_required()
def delete_project(pid):
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    p = db.session.get(Project, pid)
    if not p:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(p)
    db.session.commit()
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# INTERNS ROSTER — CRUD
# ─────────────────────────────────────────────

def intern_to_dict(i):
    return {
        "id": i.id, "name": i.name, "domain": i.domain,
        "joining_date": i.joining_date, "status": i.status,
        "created_at": i.created_at
    }


@app.route("/api/interns-roster")
@jwt_required()
def get_interns_roster():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    interns = Intern.query.order_by(Intern.id.desc()).all()
    domain_counts = {}
    for i in interns:
        d = i.domain or "Unknown"
        domain_counts[d] = domain_counts.get(d, 0) + 1
    return jsonify({
        "interns": [intern_to_dict(i) for i in interns],
        "total": len(interns),
        "domain_counts": domain_counts
    })


@app.route("/api/interns-roster/bulk", methods=["POST"])
@jwt_required()
def bulk_interns():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json() or {}
    rows = data.get("interns", [])

    if not isinstance(rows, list):
        return jsonify({"error": "interns must be a list"}), 400

    try:
        Intern.query.delete()
        created = 0
        skipped = 0
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name:
                skipped += 1
                continue
            db.session.add(Intern(
                name=name,
                domain=(row.get("domain") or "").strip(),
                joining_date=(row.get("joining_date") or "").strip(),
                status=(row.get("status") or "Active").strip(),
                created_at=datetime.datetime.now().strftime("%b %d, %Y")
            ))
            created += 1
        db.session.commit()
        return jsonify({"success": True, "created": created, "skipped": skipped})
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Database error: {str(e)}"}), 500


@app.route("/api/interns-roster", methods=["POST"])
@jwt_required()
def add_intern():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    i = Intern(
        name=name,
        domain=(data.get("domain") or "").strip(),
        joining_date=(data.get("joining_date") or "").strip(),
        status=(data.get("status") or "Active").strip(),
        created_at=datetime.datetime.now().strftime("%b %d, %Y")
    )
    db.session.add(i)
    db.session.commit()
    return jsonify({"success": True, "intern": intern_to_dict(i)})


@app.route("/api/interns-roster/<int:iid>", methods=["DELETE"])
@jwt_required()
def delete_intern_roster(iid):
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    i = db.session.get(Intern, iid)
    if not i:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(i)
    db.session.commit()
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# DASHBOARD STATS
# ─────────────────────────────────────────────

@app.route("/api/dashboard-stats")
@jwt_required()
def dashboard_stats():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403

    gov_count     = Project.query.filter_by(category="Government").count()
    private_count = Project.query.filter_by(category="Private").count()
    b2c_count     = Project.query.filter_by(category="B2C").count()

    employees = User.query.filter_by(role="employee").all()
    interns   = User.query.filter_by(role="intern").all()

    intern_roster = Intern.query.all()
    domain_counts = {}
    for i in intern_roster:
        d = i.domain or "Unknown"
        domain_counts[d] = domain_counts.get(d, 0) + 1

    today         = today_str()
    today_ratings = Rating.query.filter_by(date=today).all()
    emp_ids       = {u.id for u in employees}
    intern_ids    = {u.id for u in interns}

    emp_ratings    = [r for r in today_ratings if r.userId in emp_ids]
    intern_ratings = [r for r in today_ratings if r.userId in intern_ids]

    def avg(lst): return round(sum(r.score for r in lst) / len(lst), 1) if lst else 0

    return jsonify({
        "projects": {
            "government": gov_count, "private": private_count,
            "b2c": b2c_count, "total": gov_count + private_count + b2c_count
        },
        "users": {
            "total_employees": len(employees), "total_interns": len(interns)
        },
        "interns_roster": {
            "total": len(intern_roster), "domain_counts": domain_counts
        },
        "ratings": {
            "emp_avg": avg(emp_ratings), "intern_avg": avg(intern_ratings),
            "emp_rated_today": len(emp_ratings), "intern_rated_today": len(intern_ratings)
        }
    })


# ─────────────────────────────────────────────
# DASHBOARD GOALS  (persistent settings)
# ─────────────────────────────────────────────

_GOAL_DEFAULTS = {
    "emp_goal":     15,
    "intern_goal":  40,
    "intship_goal": 200,
    "intship_cur":  0,
    "cert_goal":    150,
    "cert_cur":     0,
}

def _load_goals():
    """Return goals dict merged with defaults; reads from DB."""
    rows = {r.key: r.value for r in DashboardGoal.query.all()}
    result = {}
    for k, default in _GOAL_DEFAULTS.items():
        try:
            result[k] = int(rows[k]) if k in rows else default
        except (ValueError, TypeError):
            result[k] = default
    return result


# ─────────────────────────────────────────────
# UPCOMING BIRTHDAYS  (alias for birthday-alerts)
# ─────────────────────────────────────────────

@app.route("/api/upcoming-birthdays")
@jwt_required()
def get_upcoming_birthdays():
    """
    Alias for /api/birthday-alerts — returns employees whose birthday
    falls within the next 7 days. Founder-like accounts only.
    """
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403

    today   = datetime.date.today()
    results = []

    candidates = User.query.filter(
        User.role.notin_(["founder", "founder_assistant"]),
        User.date_of_birth != None,
        User.date_of_birth != ""
    ).all()

    for emp in candidates:
        try:
            dob = datetime.datetime.strptime(emp.date_of_birth, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        for delta in range(0, 8):
            check_date = today + datetime.timedelta(days=delta)
            try:
                this_year_bday = dob.replace(year=check_date.year)
            except ValueError:
                continue
            if this_year_bday == check_date:
                results.append({
                    "id":         emp.id,
                    "name":       emp.name or emp.id,
                    "role":       emp.role,
                    "department": emp.department or "",
                    "days_away":  delta,
                    "birthday":   this_year_bday.strftime("%B %d"),
                })
                break

    results.sort(key=lambda x: x["days_away"])
    return jsonify({"alerts": results})



# ─────────────────────────────────────────────
# PROJECT TASKS — EXCEL UPLOAD & PROGRESS API
# ─────────────────────────────────────────────

@app.route("/api/project-tasks/upload", methods=["POST"])
@jwt_required()
def upload_project_tasks():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403

    if "file" not in request.files:
        return jsonify({"error": "No file part in request."}), 400

    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("xlsx", "xls", "csv"):
        return jsonify({"error": "Unsupported file type. Upload .xlsx, .xls, or .csv"}), 400

    try:
        import pandas as pd

        file_bytes = file.read()

        if ext == "csv":
            df = pd.read_csv(io.BytesIO(file_bytes))
        else:
            df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")

        df.columns = [str(c).strip() for c in df.columns]

        cols_lower = [c.lower() for c in df.columns]
        has_project = any("project" in c for c in cols_lower)
        has_task    = any("task" in c for c in cols_lower)
        if not has_project or not has_task:
            return jsonify({
                "error": (
                    "Missing required columns. Expected: 'Project Name', 'Task Name'. "
                    f"Found: {', '.join(df.columns)}"
                )
            }), 400

        def col(row, *names):
            for n in names:
                v = row.get(n)
                if v and str(v).strip() and str(v).strip().lower() not in ("nan", "none", ""):
                    return str(v).strip()
            return ""

        VALID_STATUSES = {
            "pending":     "Pending",
            "in progress": "In Progress",
            "completed":   "Completed"
        }

        new_rows = []
        skipped  = 0

        for _, row in df.iterrows():
            row_dict     = row.to_dict()
            project_name = col(row_dict, "Project Name", "project name", "Project", "project")
            task_name    = col(row_dict, "Task Name",    "task name",    "Task",    "task")

            if not project_name or not task_name:
                skipped += 1
                continue

            status_raw = col(row_dict, "Status", "status", "STATUS") or "Pending"
            status     = VALID_STATUSES.get(status_raw.lower(), "Pending")

            # Read work_completion_percentage from the LAST column
            wcp = 0.0
            cols_list = list(df.columns)
            if cols_list:
                last_col_name = cols_list[-1]
                last_val = row_dict.get(last_col_name, "")
                try:
                    parsed = float(str(last_val).strip())
                    if 0 <= parsed <= 100:
                        wcp = parsed
                except (ValueError, TypeError):
                    wcp = 0.0

            new_rows.append(ProjectTask(
                project_name = project_name,
                task_name    = task_name,
                assigned_to  = col(row_dict, "Assigned To", "assigned to", "AssignedTo", "Assignee"),
                status       = status,
                due_date     = col(row_dict, "Due Date", "due date", "DueDate", "Due"),
                created_at   = datetime.datetime.now().strftime("%b %d, %Y %I:%M %p")
            ))

        if not new_rows:
            return jsonify({"error": "No valid task rows found."}), 400

        ProjectTask.query.delete()
        db.session.bulk_save_objects(new_rows)
        db.session.commit()

        return jsonify({"success": True, "created": len(new_rows), "skipped": skipped})

    except ImportError as e:
        return jsonify({"error": f"Server missing library: {e}"}), 500
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"File processing error: {str(e)}"}), 500


@app.route("/api/project-progress")
@jwt_required()
def get_project_progress():
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403

    from sqlalchemy import func, case

    # Task counts — for informational display in the metric boxes only.
    # They NEVER affect progress_percentage.
    task_rows = (
        db.session.query(
            ProjectTask.project_name,
            func.count(ProjectTask.id).label("total_tasks"),
            func.sum(case((ProjectTask.status == "Completed",   1), else_=0)).label("completed_tasks"),
            func.sum(case((ProjectTask.status == "In Progress", 1), else_=0)).label("in_progress_tasks"),
            func.sum(case((ProjectTask.status == "Pending",     1), else_=0)).label("pending_tasks"),
        )
        .group_by(ProjectTask.project_name)
        .all()
    )
    task_map = {r.project_name: r for r in task_rows}

    # Progress comes exclusively from Project.progress_percentage (set by Excel import).
    projects = Project.query.order_by(Project.name).all()
    result = []
    for p in projects:
        tr = task_map.get(p.name)
        pct = float(p.progress_percentage or 0)
        print(f"[API] {p.name} -> progress_percentage={pct}")
        result.append({
            "project_name":        p.name,
            "progress_percentage": pct,           # ← ring uses this field
            "total_tasks":         int(tr.total_tasks      or 0) if tr else 0,
            "completed_tasks":     int(tr.completed_tasks  or 0) if tr else 0,
            "in_progress_tasks":   int(tr.in_progress_tasks or 0) if tr else 0,
            "pending_tasks":       int(tr.pending_tasks     or 0) if tr else 0,
        })

    return jsonify(result)


# ─────────────────────────────────────────────
# DAILY MOTIVATION QUOTES — Internal Library (100 quotes, expandable)
# ─────────────────────────────────────────────

MOTIVATION_QUOTES = [
    {"text": "The secret of getting ahead is getting started.", "author": "Mark Twain"},
    {"text": "It always seems impossible until it's done.", "author": "Nelson Mandela"},
    {"text": "Don't watch the clock; do what it does. Keep going.", "author": "Sam Levenson"},
    {"text": "The future depends on what you do today.", "author": "Mahatma Gandhi"},
    {"text": "Success is not final, failure is not fatal: it is the courage to continue that counts.", "author": "Winston Churchill"},
    {"text": "Believe you can and you're halfway there.", "author": "Theodore Roosevelt"},
    {"text": "You are never too old to set another goal or to dream a new dream.", "author": "C.S. Lewis"},
    {"text": "The harder you work for something, the greater you'll feel when you achieve it.", "author": "Unknown"},
    {"text": "Dream big and dare to fail.", "author": "Norman Vaughan"},
    {"text": "Work hard, be kind, and amazing things will happen.", "author": "Conan O'Brien"},
    {"text": "Push yourself, because no one else is going to do it for you.", "author": "Unknown"},
    {"text": "Great things never come from comfort zones.", "author": "Unknown"},
    {"text": "Do something today that your future self will thank you for.", "author": "Sean Patrick Flanery"},
    {"text": "Little things make big days.", "author": "Unknown"},
    {"text": "It's going to be hard, but hard does not mean impossible.", "author": "Unknown"},
    {"text": "Don't stop when you're tired. Stop when you're done.", "author": "Unknown"},
    {"text": "Wake up with determination. Go to bed with satisfaction.", "author": "Unknown"},
    {"text": "Do what you can with all you have, wherever you are.", "author": "Theodore Roosevelt"},
    {"text": "Success usually comes to those who are too busy to be looking for it.", "author": "Henry David Thoreau"},
    {"text": "Opportunities don't happen. You create them.", "author": "Chris Grosser"},
    {"text": "Don't be afraid to give up the good to go for the great.", "author": "John D. Rockefeller"},
    {"text": "I find that the harder I work, the more luck I seem to have.", "author": "Thomas Jefferson"},
    {"text": "There are no secrets to success. It is the result of preparation, hard work, and learning from failure.", "author": "Colin Powell"},
    {"text": "Success is not the key to happiness. Happiness is the key to success.", "author": "Albert Schweitzer"},
    {"text": "The only place where success comes before work is in the dictionary.", "author": "Vidal Sassoon"},
    {"text": "The road to success and the road to failure are almost exactly the same.", "author": "Colin R. Davis"},
    {"text": "I attribute my success to this: I never gave or took any excuse.", "author": "Florence Nightingale"},
    {"text": "If you are not willing to risk the usual, you will have to settle for the ordinary.", "author": "Jim Rohn"},
    {"text": "In order to succeed, we must first believe that we can.", "author": "Nikos Kazantzakis"},
    {"text": "The secret to success is to know something nobody else knows.", "author": "Aristotle Onassis"},
    {"text": "If you can dream it, you can do it.", "author": "Walt Disney"},
    {"text": "The best time to plant a tree was 20 years ago. The second best time is now.", "author": "Chinese Proverb"},
    {"text": "An unexamined life is not worth living.", "author": "Socrates"},
    {"text": "Spread love everywhere you go. Let no one ever come to you without leaving happier.", "author": "Mother Teresa"},
    {"text": "When you reach the end of your rope, tie a knot in it and hang on.", "author": "Franklin D. Roosevelt"},
    {"text": "Always remember that you are absolutely unique. Just like everyone else.", "author": "Margaret Mead"},
    {"text": "Don't judge each day by the harvest you reap but by the seeds that you plant.", "author": "Robert Louis Stevenson"},
    {"text": "The best and most beautiful things in the world cannot be seen or even touched — they must be felt with the heart.", "author": "Helen Keller"},
    {"text": "It is during our darkest moments that we must focus to see the light.", "author": "Aristotle"},
    {"text": "Whoever is happy will make others happy too.", "author": "Anne Frank"},
    {"text": "Do not go where the path may lead, go instead where there is no path and leave a trail.", "author": "Ralph Waldo Emerson"},
    {"text": "You will face many defeats in life, but never let yourself be defeated.", "author": "Maya Angelou"},
    {"text": "The greatest glory in living lies not in never falling, but in rising every time we fall.", "author": "Nelson Mandela"},
    {"text": "In the end, it's not the years in your life that count. It's the life in your years.", "author": "Abraham Lincoln"},
    {"text": "Never let the fear of striking out keep you from playing the game.", "author": "Babe Ruth"},
    {"text": "Life is either a daring adventure or nothing at all.", "author": "Helen Keller"},
    {"text": "Many of life's failures are people who did not realize how close they were to success when they gave up.", "author": "Thomas A. Edison"},
    {"text": "You have brains in your head. You have feet in your shoes. You can steer yourself any direction you choose.", "author": "Dr. Seuss"},
    {"text": "If life were predictable it would cease to be life, and be without flavor.", "author": "Eleanor Roosevelt"},
    {"text": "If you look at what you have in life, you'll always have more.", "author": "Oprah Winfrey"},
    {"text": "If you want your life to be a magnificent story, then begin by realizing that you are the author.", "author": "Mark Houlahan"},
    {"text": "You don't have to be great to start, but you have to start to be great.", "author": "Zig Ziglar"},
    {"text": "The only limit to our realization of tomorrow will be our doubts of today.", "author": "Franklin D. Roosevelt"},
    {"text": "It is never too late to be what you might have been.", "author": "George Eliot"},
    {"text": "Life is what happens when you're busy making other plans.", "author": "John Lennon"},
    {"text": "The way to get started is to quit talking and begin doing.", "author": "Walt Disney"},
    {"text": "You miss 100% of the shots you don't take.", "author": "Wayne Gretzky"},
    {"text": "Whether you think you can or think you can't, you're right.", "author": "Henry Ford"},
    {"text": "I have not failed. I've just found 10,000 ways that won't work.", "author": "Thomas A. Edison"},
    {"text": "A person who never made a mistake never tried anything new.", "author": "Albert Einstein"},
    {"text": "The real test is not whether you avoid this failure, because you won't. It's whether you let it harden or shame you into inaction, or whether you learn from it.", "author": "Barack Obama"},
    {"text": "Knowing is not enough; we must apply. Wishing is not enough; we must do.", "author": "Johann Wolfgang Von Goethe"},
    {"text": "Imagine your life is perfect in every respect; what would it look like?", "author": "Brian Tracy"},
    {"text": "We generate fears while we sit. We overcome them by action.", "author": "Dr. Henry Link"},
    {"text": "Whether you want to call it grit, mental toughness or resilience, the ability to keep moving forward when you want to quit is perhaps the most important factor in achieving your goals.", "author": "Jack Canfield"},
    {"text": "The man who has confidence in himself gains the confidence of others.", "author": "Hasidic Proverb"},
    {"text": "The only way to do great work is to love what you do.", "author": "Steve Jobs"},
    {"text": "If you can't explain it simply, you don't understand it well enough.", "author": "Albert Einstein"},
    {"text": "Definiteness of purpose is the starting point of all achievement.", "author": "W. Clement Stone"},
    {"text": "If you're going through hell, keep going.", "author": "Winston Churchill"},
    {"text": "We must balance conspicuous consumption with conscious capitalism.", "author": "Kevin Kruse"},
    {"text": "Leadership is the capacity to translate vision into reality.", "author": "Warren Bennis"},
    {"text": "Always do your best. What you plant now, you will harvest later.", "author": "Og Mandino"},
    {"text": "You've got to get up every morning with determination if you're going to go to bed with satisfaction.", "author": "George Lorimer"},
    {"text": "To see what is right and not to do it is want of courage, or of principle.", "author": "Confucius"},
    {"text": "Reading is to the mind, as exercise is to the body.", "author": "Brian Tracy"},
    {"text": "Fake it until you make it! Act as if you had all the confidence you require until it becomes your reality.", "author": "Brian Tracy"},
    {"text": "The most common way people give up their power is by thinking they don't have any.", "author": "Alice Walker"},
    {"text": "The mind is everything. What you think you become.", "author": "Buddha"},
    {"text": "The best time for new beginnings is now.", "author": "Unknown"},
    {"text": "Start where you are. Use what you have. Do what you can.", "author": "Arthur Ashe"},
    {"text": "When the going gets tough, the tough get going.", "author": "Joe Kennedy"},
    {"text": "It does not matter how slowly you go as long as you do not stop.", "author": "Confucius"},
    {"text": "If you want to lift yourself up, lift up someone else.", "author": "Booker T. Washington"},
    {"text": "A goal is not always meant to be reached, it often serves simply as something to aim at.", "author": "Bruce Lee"},
    {"text": "We must accept finite disappointment, but never lose infinite hope.", "author": "Martin Luther King Jr."},
    {"text": "Once you choose hope, anything's possible.", "author": "Christopher Reeve"},
    {"text": "Every day is a new beginning. Take a deep breath and start again.", "author": "Unknown"},
    {"text": "Motivation is what gets you started. Habit is what keeps you going.", "author": "Jim Ryun"},
    {"text": "The difference between ordinary and extraordinary is that little extra.", "author": "Jimmy Johnson"},
    {"text": "What lies behind you and what lies in front of you, pales in comparison to what lies inside of you.", "author": "Ralph Waldo Emerson"},
    {"text": "With the new day comes new strength and new thoughts.", "author": "Eleanor Roosevelt"},
    {"text": "Energy and persistence conquer all things.", "author": "Benjamin Franklin"},
    {"text": "How wonderful it is that nobody need wait a single moment before starting to improve the world.", "author": "Anne Frank"},
    {"text": "Success comes from consistency, focus and never giving up.", "author": "Unknown"},
    {"text": "Your limitation—it's only your imagination.", "author": "Unknown"},
    {"text": "Sometimes later becomes never. Do it now.", "author": "Unknown"},
    {"text": "Great things never come from comfort zones.", "author": "Unknown"},
    {"text": "Dream it. Wish it. Do it.", "author": "Unknown"},
    {"text": "Stay foolish to stay sane.", "author": "Maxime Lagacé"},
    {"text": "When nothing goes right, go left.", "author": "Unknown"},
]


def get_or_create_daily_quote():
    """
    Return today's quote (from DB if already set, else pick a new one).
    Ensures no repeat from yesterday.
    """
    today = today_str()
    existing = DailyQuote.query.filter_by(date=today).first()
    if existing:
        return existing

    # Find yesterday's quote to avoid repeating
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_q = DailyQuote.query.filter_by(date=yesterday).first()
    avoid_text  = yesterday_q.quote_text if yesterday_q else None

    candidates = [q for q in MOTIVATION_QUOTES if q["text"] != avoid_text]
    if not candidates:
        candidates = MOTIVATION_QUOTES

    chosen = random.choice(candidates)
    dq = DailyQuote(
        date       = today,
        quote_text = chosen["text"],
        author     = chosen["author"],
        sent_at    = None
    )
    db.session.add(dq)
    db.session.commit()
    return dq


def send_daily_quote_email(recipient_email, quote_text, author):
    """Send the daily motivational quote via email to a single recipient."""
    brevo_key  = os.getenv("BREVO_API_KEY", "").strip()
    smtp_email = os.getenv("SMTP_EMAIL", "").strip()
    smtp_pass  = os.getenv("SMTP_PASSWORD", "").strip()

    today_display = datetime.datetime.now().strftime("%d %B %Y")
    subject  = f"🌞 Your Daily Motivation | PGI TaskFlow — {today_display}"
    plain    = (
        f"Good Morning,\n\nToday's motivation:\n\n"
        f"\"{quote_text}\"\n— {author}\n\n"
        f"Wishing you a productive day.\n\nRegards,\nPGI TaskFlow"
    )
    html = (
        f'<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:28px;'
        f'background:#fffdf5;border:1px solid #e5d98b;border-radius:10px">'
        f'<h2 style="color:#15803D;margin-top:0">🌞 Good Morning!</h2>'
        f'<div style="background:#f0fff4;border-left:4px solid #22C55E;padding:16px 20px;'
        f'border-radius:6px;margin:18px 0">'
        f'<p style="font-size:17px;font-style:italic;color:#1a3a2a;margin:0 0 8px">"{quote_text}"</p>'
        f'<p style="font-size:13px;color:#5a7a6a;margin:0">— {author}</p></div>'
        f'<p style="color:#555;font-size:14px;line-height:1.7">Wishing you a productive day.</p>'
        f'<hr style="border:none;border-top:1px solid #e5d98b;margin:20px 0">'
        f'<p style="color:#888;font-size:12px">PGI TaskFlow &mdash; Plant Green Inertia</p></div>'
    )

    if brevo_key:
        payload = json.dumps({
            "sender": {"name": "PGI TaskFlow", "email": smtp_email or "pgiworkflow@gmail.com"},
            "to": [{"email": recipient_email}],
            "subject": subject,
            "textContent": plain,
            "htmlContent": html,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=payload,
            headers={"api-key": brevo_key, "Content-Type": "application/json", "Accept": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
            return True
        except Exception as e:
            print(f"[QUOTE EMAIL/Brevo] Failed for {recipient_email}: {e}")
            return False

    if not smtp_email or not smtp_pass:
        return False
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(smtp_email, smtp_pass)
            mime = MIMEMultipart("alternative")
            mime["Subject"] = subject
            mime["From"]    = f"PGI TaskFlow <{smtp_email}>"
            mime["To"]      = recipient_email
            mime.attach(MIMEText(plain, "plain", "utf-8"))
            mime.attach(MIMEText(html,  "html",  "utf-8"))
            server.sendmail(smtp_email, recipient_email, mime.as_string())
        return True
    except Exception as e:
        print(f"[QUOTE EMAIL/SMTP] Failed for {recipient_email}: {e}")
        return False


def send_daily_quote_whatsapp(phone, quote_text, author):
    """
    Send quote via WhatsApp Business API.
    Requires WHATSAPP_API_URL and WHATSAPP_API_TOKEN in .env.
    """
    wa_url   = os.getenv("WHATSAPP_API_URL", "").strip()
    wa_token = os.getenv("WHATSAPP_API_TOKEN", "").strip()
    if not wa_url or not wa_token or not phone:
        return False

    body_text = f"🌞 Good Morning!\n\n\"{quote_text}\"\n— {author}\n\nHave a productive day!\n— PGI TaskFlow"
    payload   = json.dumps({
        "to":   phone,
        "type": "text",
        "text": {"body": body_text}
    }).encode("utf-8")
    req = urllib.request.Request(
        wa_url,
        data=payload,
        headers={"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except Exception as e:
        print(f"[QUOTE WA] Failed for {phone}: {e}")
        return False


def dispatch_daily_quote():
    """
    Main scheduler job: pick today's quote, send to all users,
    insert system message into all channels, create notifications.
    """
    with app.app_context():
        today = today_str()
        now   = datetime.datetime.now()
        now_str_val = now.strftime("%I:%M %p")
        now_iso     = now.strftime("%Y-%m-%dT%H:%M:%S")

        dq = get_or_create_daily_quote()
        quote_text = dq.quote_text
        author     = dq.author

        users = User.query.all()

        # ── 1. Insert system message into 'all' channel (once per day) ──
        already_in_chat = QuoteDeliveryLog.query.filter_by(
            date=today, userId="__system__", channel="chat"
        ).first()
        if not already_in_chat:
            today_display = now.strftime("%d %B %Y")
            chat_text = (
                f"🌞 Daily Motivation\n\n"
                f"📅 {today_display}\n\n"
                f"\"{quote_text}\"\n"
                f"— {author}"
            )
            sys_msg = Message(
                id        = "qm" + str(int(now.timestamp() * 1000)),
                from_name = "🌱 PGI TaskFlow",
                fromId    = "system",
                text      = chat_text,
                time      = now_str_val,
                timestamp = now_iso,
                channel   = "all"
            )
            db.session.add(sys_msg)
            db.session.add(QuoteDeliveryLog(
                date=today, userId="__system__", channel="chat",
                status="sent", sent_at=now_iso
            ))

        # ── 2. Create notification for each user ──
        for user in users:
            already_notif = QuoteDeliveryLog.query.filter_by(
                date=today, userId=user.id, channel="notification"
            ).first()
            if not already_notif:
                make_notif(
                    userId=user.id,
                    ntype="motivation",
                    title="✨ New Daily Motivation Available",
                    body=f'"{quote_text}" — {author}'
                )
                db.session.add(QuoteDeliveryLog(
                    date=today, userId=user.id, channel="notification",
                    status="sent", sent_at=now_iso
                ))

        # ── 3. Send email ──
        for user in users:
            if not user.email:
                continue
            already_email = QuoteDeliveryLog.query.filter_by(
                date=today, userId=user.id, channel="email"
            ).first()
            if already_email:
                continue
            ok = send_daily_quote_email(user.email, quote_text, author)
            db.session.add(QuoteDeliveryLog(
                date=today, userId=user.id, channel="email",
                status="sent" if ok else "failed", sent_at=now_iso
            ))

        # ── 4. Send WhatsApp ──
        for user in users:
            if not user.phone:
                continue
            already_wa = QuoteDeliveryLog.query.filter_by(
                date=today, userId=user.id, channel="whatsapp"
            ).first()
            if already_wa:
                continue
            ok = send_daily_quote_whatsapp(user.phone, quote_text, author)
            db.session.add(QuoteDeliveryLog(
                date=today, userId=user.id, channel="whatsapp",
                status="sent" if ok else "failed", sent_at=now_iso
            ))

        # Mark dispatch time on the DailyQuote record
        dq.sent_at = now_iso
        db.session.commit()
        print(f"[DAILY QUOTE] Dispatched quote for {today} to {len(users)} users")


# ─────────────────────────────────────────────
# DAILY QUOTE APIs
# ─────────────────────────────────────────────

@app.route("/api/daily-quote")
@jwt_required()
def get_daily_quote():
    """Return today's motivational quote."""
    dq = get_or_create_daily_quote()
    return jsonify({
        "date": dq.date,
        "quote": dq.quote_text,
        "author": dq.author,
        "sent_at": dq.sent_at
    })


@app.route("/api/daily-quote/send-now", methods=["POST"])
@jwt_required()
def send_quote_now():
    """Founder can manually trigger the daily quote dispatch."""
    caller = db.session.get(User, get_jwt_identity())
    if not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    try:
        dispatch_daily_quote()
        return jsonify({"success": True, "message": "Daily quote dispatched to all users."})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# DASHBOARD GOALS API
# ─────────────────────────────────────────────

GOAL_KEYS    = ["emp_goal", "intern_goal", "intship_goal", "intship_cur", "cert_goal", "cert_cur"]
GOAL_DEFAULTS = {"emp_goal": "15", "intern_goal": "40", "intship_goal": "200",
                 "intship_cur": "0", "cert_goal": "150", "cert_cur": "0"}


def _load_goals_dict():
    rows = {r.key: r.value for r in DashboardGoal.query.all()}
    result = {}
    for k in GOAL_KEYS:
        try:
            result[k] = int(rows.get(k, GOAL_DEFAULTS.get(k, "0")))
        except (ValueError, TypeError):
            result[k] = int(GOAL_DEFAULTS.get(k, "0"))
    return result


@app.route("/api/dashboard/goals", methods=["GET"])
@jwt_required()
def get_dashboard_goals():
    caller = db.session.get(User, get_jwt_identity())
    if not caller or not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    return jsonify(_load_goals_dict())


@app.route("/api/dashboard/goals", methods=["PUT"])
@jwt_required()
def put_dashboard_goals():
    caller = db.session.get(User, get_jwt_identity())
    if not caller or not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json() or {}
    for k in GOAL_KEYS:
        if k in data:
            try:
                val = str(int(data[k]))
            except (ValueError, TypeError):
                continue
            row = db.session.get(DashboardGoal, k)
            if row:
                row.value = val
            else:
                db.session.add(DashboardGoal(key=k, value=val))
    db.session.commit()
    return jsonify({"success": True, "goals": _load_goals_dict()})


# ─────────────────────────────────────────────
# TODOS — account-based, JWT authenticated
# ─────────────────────────────────────────────

@app.route("/api/todos", methods=["GET"])
@jwt_required()
def get_todos():
    uid = get_jwt_identity()
    todos = Todo.query.filter_by(user_id=uid).order_by(Todo.id.desc()).all()
    return jsonify([{
        "id": t.id, "title": t.title,
        "completed": t.completed, "created_at": t.created_at
    } for t in todos])


@app.route("/api/todos", methods=["POST"])
@jwt_required()
def create_todo():
    uid  = get_jwt_identity()
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400
    todo = Todo(
        user_id    = uid,
        title      = title,
        completed  = False,
        created_at = datetime.datetime.now().strftime("%b %d, %Y %I:%M %p")
    )
    db.session.add(todo)
    db.session.commit()
    return jsonify({
        "id": todo.id, "title": todo.title,
        "completed": todo.completed, "created_at": todo.created_at
    }), 201


@app.route("/api/todos/<int:todo_id>", methods=["PUT"])
@jwt_required()
def update_todo(todo_id):
    uid  = get_jwt_identity()
    todo = Todo.query.filter_by(id=todo_id, user_id=uid).first()
    if not todo:
        return jsonify({"error": "Todo not found"}), 404
    data = request.get_json() or {}
    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            return jsonify({"error": "Title cannot be empty"}), 400
        todo.title = title
    if "completed" in data:
        todo.completed = bool(data["completed"])
    db.session.commit()
    return jsonify({
        "id": todo.id, "title": todo.title,
        "completed": todo.completed, "created_at": todo.created_at
    })


@app.route("/api/todos/<int:todo_id>", methods=["DELETE"])
@jwt_required()
def delete_todo(todo_id):
    uid  = get_jwt_identity()
    todo = Todo.query.filter_by(id=todo_id, user_id=uid).first()
    if not todo:
        return jsonify({"error": "Todo not found"}), 404
    db.session.delete(todo)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/todos/clear-completed", methods=["DELETE"])
@jwt_required()
def clear_completed_todos():
    uid = get_jwt_identity()
    deleted = Todo.query.filter_by(user_id=uid, completed=True).all()
    count   = len(deleted)
    for t in deleted:
        db.session.delete(t)
    db.session.commit()
    return jsonify({"success": True, "deleted": count})


# ─────────────────────────────────────────────
# BIRTHDAY ALERT SYSTEM
# ─────────────────────────────────────────────

def check_birthday_alerts():
    """
    Scheduler job — runs daily at 08:00 IST.
    Finds every employee/intern/trainer whose birthday falls exactly 7 days
    from today. If no BirthdayAlert row exists for (employee_id, this_year),
    creates a Notification for every founder and writes the dedup row.
    One alert per person per calendar year — immune to duplicate scheduler fires.
    """
    with app.app_context():
        today     = datetime.date.today()
        target    = today + datetime.timedelta(days=7)
        now_str   = datetime.datetime.now().strftime("%b %d %I:%M %p")
        year      = today.year

        # All non-founder-like users with a stored DOB
        candidates = User.query.filter(
            User.role.notin_(["founder", "founder_assistant"]),
            User.date_of_birth != None,
            User.date_of_birth != ""
        ).all()

        # Notify all founder-like users (founder + founder_assistant)
        founders = User.query.filter(
            User.role.in_(["founder", "founder_assistant"])
        ).all()
        if not founders:
            return

        alerts_created = 0
        for emp in candidates:
            try:
                dob = datetime.datetime.strptime(emp.date_of_birth, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            # Replace year with current year to compare month/day
            try:
                this_year_bday = dob.replace(year=target.year)
            except ValueError:
                # Feb 29 on a non-leap year — skip gracefully
                continue

            if this_year_bday != target:
                continue

            # Dedup check — one row per employee per alert_year
            already = BirthdayAlert.query.filter_by(
                employee_id=emp.id,
                alert_year=year
            ).first()
            if already:
                continue

            # First name for a friendly message
            first_name = (emp.name or emp.id).split()[0]
            bday_display = this_year_bday.strftime("%B %d")

            title = f"🎂 Upcoming Birthday — {first_name}"
            body  = (
                f"{first_name}'s birthday is in 7 days ({bday_display}). "
                f"Wish preparation reminder."
            )

            # Create notification for every founder
            for founder in founders:
                make_notif(
                    userId=founder.id,
                    ntype="birthday",
                    title=title,
                    body=body
                )

            # Write dedup record
            db.session.add(BirthdayAlert(
                employee_id=emp.id,
                alert_year=year,
                sent_at=now_str
            ))
            alerts_created += 1
            print(f"[BIRTHDAY] Alert created for {emp.name} (birthday {bday_display})")

        if alerts_created:
            db.session.commit()
            print(f"[BIRTHDAY] {alerts_created} birthday alert(s) committed.")
        else:
            print("[BIRTHDAY] No upcoming birthdays in 7 days.")


@app.route("/api/birthday-alerts")
@jwt_required()
def get_birthday_alerts():
    """
    Returns employees whose birthday falls within the next 7 days.
    Used by the VisionBoard card. Founder-only.
    """
    caller = db.session.get(User, get_jwt_identity())
    if not caller or not is_founder_like(caller):
        return jsonify({"error": "Forbidden"}), 403

    today   = datetime.date.today()
    results = []

    candidates = User.query.filter(
        User.role.notin_(["founder", "founder_assistant"]),
        User.date_of_birth != None,
        User.date_of_birth != ""
    ).all()

    for emp in candidates:
        try:
            dob = datetime.datetime.strptime(emp.date_of_birth, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

        for delta in range(0, 8):          # today through +7
            check_date = today + datetime.timedelta(days=delta)
            try:
                this_year_bday = dob.replace(year=check_date.year)
            except ValueError:
                continue
            if this_year_bday == check_date:
                results.append({
                    "id":         emp.id,
                    "name":       emp.name or emp.id,
                    "role":       emp.role,
                    "department": emp.department or "",
                    "days_away":  delta,
                    "birthday":   this_year_bday.strftime("%B %d"),
                })
                break

    results.sort(key=lambda x: x["days_away"])
    return jsonify({"alerts": results})


# ─────────────────────────────────────────────
# DAILY TASK ASSIGNMENT (12:00 AM IST)
# ─────────────────────────────────────────────

# Template definitions: (team_or_role_key, match_field, title, description)
_DAILY_TASK_TEMPLATES = [
    # Content Production Team
    {
        "match_field": "team",
        "match_value": "content",
        "title":       "Full video edit and post on YouTube",
        "desc":        "Avoid mistakes that occurred previously.\nEnsure quality check before publishing.",
    },
    # Business Development Team
    {
        "match_field": "team",
        "match_value": "bizdev",
        "title":       "Manage the sales work",
        "desc":        "Follow up leads, customer communication and sales activities.",
    },
    # Technical Team
    {
        "match_field": "team",
        "match_value": "technical",
        "title":       "Manage the technical works",
        "desc":        "Complete assigned development and maintenance work.",
    },
    # Founder Assistant — Task 1
    {
        "match_field": "founder_assistant",  # special: role OR team
        "match_value": "founder_assistant",
        "title":       "Report work done by the team by EOD to Sir",
        "desc":        "Prepare and submit the daily work summary before end of day.",
    },
    # Founder Assistant — Task 2
    {
        "match_field": "founder_assistant",
        "match_value": "founder_assistant",
        "title":       "Corporate leads follow-up daily for MOU",
        "desc":        "Follow up all corporate leads and maintain MOU progress updates.",
    },
]


def assign_daily_tasks():
    """
    Runs at 12:00 AM IST every day.
    Creates permanent daily tasks for Content, BizDev, Technical, and
    Founder Assistant team members.  Skips duplicates (same assignedTo +
    title + today's date).  Sends an in-app notification per new task.
    """
    with app.app_context():
        today    = today_str()
        now_iso  = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        created  = 0

        for tpl in _DAILY_TASK_TEMPLATES:
            mf = tpl["match_field"]
            mv = tpl["match_value"]

            # Resolve target users
            if mf == "founder_assistant":
                targets = User.query.filter(
                    db.or_(
                        User.role == "founder_assistant",
                        User.team == "founder_assistant"
                    )
                ).all()
            else:
                # team-based match; only non-founder roles
                targets = User.query.filter(
                    User.team == mv,
                    User.role.notin_(["founder", "founder_assistant"])
                ).all()

            for user in targets:
                # Duplicate check: same user + same title + same due date
                exists = Task.query.filter_by(
                    assignedTo=user.id,
                    title=tpl["title"],
                    due=today
                ).first()
                if exists:
                    continue

                task_id = "dt_" + user.id + "_" + today.replace("-", "") + \
                          "_" + str(abs(hash(tpl["title"])))[-6:]
                task = Task(
                    id         = task_id,
                    title      = tpl["title"],
                    desc       = tpl["desc"],
                    assignedTo = user.id,
                    assignedBy = "system",
                    status     = "Pending",
                    priority   = "High",
                    due        = today,
                    createdAt  = now_iso,
                )
                db.session.add(task)

                # In-app notification
                make_notif(
                    userId = user.id,
                    ntype  = "task",
                    title  = "📋 Daily Task Assigned",
                    body   = f"Your daily task has been assigned: {tpl['title']}"
                )
                created += 1

        try:
            db.session.commit()
            print(f"[SCHEDULER] assign_daily_tasks: created {created} task(s) for {today}")
        except Exception as exc:
            db.session.rollback()
            print(f"[SCHEDULER] assign_daily_tasks ERROR: {exc}")


# ─────────────────────────────────────────────
# START APSCHEDULER
# ─────────────────────────────────────────────
if APSCHEDULER_AVAILABLE:
    _scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    _scheduler.add_job(
        dispatch_daily_quote,
        trigger="cron",
        hour=7,
        minute=0,
        id="daily_quote_job",
        replace_existing=True,
        misfire_grace_time=3600  # retry up to 1 hour after missed trigger
    )
    _scheduler.add_job(
        check_birthday_alerts,
        trigger="cron",
        hour=8,
        minute=0,
        id="birthday_alert_job",
        replace_existing=True,
        misfire_grace_time=3600
    )
    _scheduler.add_job(
        assign_daily_tasks,
        trigger="cron",
        hour=0,
        minute=0,
        id="daily_task_assignment_job",
        replace_existing=True,
        misfire_grace_time=3600
    )
    _scheduler.start()
    print("[SCHEDULER] Daily quote scheduler started — fires at 07:00 AM IST")
    print("[SCHEDULER] Birthday alert scheduler started — fires at 08:00 AM IST")
    print("[SCHEDULER] Daily task assignment scheduler started — fires at 12:00 AM IST")


# ─────────────────────────────────────────────

@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()
    return jsonify({"error": "Internal server error: " + str(e)}), 500

@app.errorhandler(Exception)
def unhandled_exception(e):
    db.session.rollback()
    import traceback
    traceback.print_exc()
    return jsonify({"error": "Unexpected error: " + str(e)}), 500

@app.route("/manifest.json")
def manifest():
    return send_file("manifest.json", mimetype="application/manifest+json")

@app.route("/sw.js")
def service_worker():
    resp = send_file("sw.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_file(f"static/{filename}")

@app.route("/")
def home():
    return send_file("taskflow_v3.html")


# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  TaskFlow — Plant Green Inertia")
    print("  http://localhost:5000")
    print("=" * 50)
    app.run(debug=True, host="0.0.0.0", port=5000)
