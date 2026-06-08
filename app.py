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

import os
import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import urllib.request
import urllib.error
import io


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
    id           = db.Column(db.String(100), primary_key=True)
    email        = db.Column(db.String(200), unique=True, nullable=False)
    password     = db.Column(db.String(300), nullable=False)
    role         = db.Column(db.String(50),  nullable=False)
    name         = db.Column(db.String(200), nullable=False)
    initials     = db.Column(db.String(10))
    team         = db.Column(db.String(100))
    specialty    = db.Column(db.String(100))
    phone        = db.Column(db.String(30))
    department   = db.Column(db.String(100))
    domain       = db.Column(db.String(100))
    joining_date = db.Column(db.String(50))


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


class Message(db.Model):
    __tablename__ = "messages"
    id        = db.Column(db.String(100), primary_key=True)
    from_name = db.Column(db.String(200))
    fromId    = db.Column(db.String(100))
    text      = db.Column(db.Text)
    time      = db.Column(db.String(100))
    channel   = db.Column(db.String(100), nullable=False, server_default='all')


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


class Rating(db.Model):
    __tablename__ = "ratings"
    id     = db.Column(db.Integer, primary_key=True)
    userId = db.Column(db.String(100), nullable=False)
    score  = db.Column(db.Integer,     nullable=False)
    date   = db.Column(db.String(20),  nullable=False)
    note   = db.Column(db.Text)


class Project(db.Model):
    __tablename__ = "projects"
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(300), nullable=False)
    category     = db.Column(db.String(50),  nullable=False)
    client_name  = db.Column(db.String(200))
    status       = db.Column(db.String(50))
    start_date   = db.Column(db.String(50))
    team_members = db.Column(db.Text)
    description  = db.Column(db.Text)
    created_at   = db.Column(db.String(100))


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
        ]
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                conn.rollback()


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
        "domain": u.domain, "joining_date": u.joining_date
    } for u in User.query.all()])


@app.route("/api/users", methods=["POST"])
@jwt_required()
def create_user():
    caller = db.session.get(User, get_jwt_identity())
    if caller.role != "founder":
        return jsonify({"error": "Only founder can create users"}), 403

    data  = request.get_json()
    email = data.get("email", "").strip().lower()

    if User.query.filter(db.func.lower(User.email) == email).first():
        return jsonify({"error": "Email already exists"}), 400

    name     = data.get("name", "").strip()
    initials = data.get("initials") or "".join(w[0].upper() for w in name.split()[:2])

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
        joining_date=(data.get("joining_date") or "").strip() or None
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({"success": True, "user": {
        "id": user.id, "name": user.name, "email": user.email,
        "role": user.role, "initials": user.initials,
        "team": user.team, "specialty": user.specialty,
        "phone": user.phone, "department": user.department,
        "domain": user.domain, "joining_date": user.joining_date
    }})


@app.route("/api/users/<user_id>", methods=["DELETE"])
@jwt_required()
def delete_user(user_id):
    caller = db.session.get(User, get_jwt_identity())
    if caller.role != "founder":
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
    if not caller or caller.role != "founder":
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
        "joining_date": user.joining_date
    }})


@app.route("/api/audit-logs")
@jwt_required()
def get_audit_logs():
    caller = db.session.get(User, get_jwt_identity())
    if not caller or caller.role != "founder":
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
        "rejection_reason": t.rejection_reason
    }


@app.route("/api/tasks")
@jwt_required()
def get_tasks():
    uid  = get_jwt_identity()
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "User not found"}), 404
    tasks = Task.query.all() if user.role == "founder" else Task.query.filter_by(assignedTo=uid).all()
    return jsonify([task_to_dict(t) for t in tasks])


@app.route("/api/tasks", methods=["POST"])
@jwt_required()
def create_task():
    db.session.rollback()

    uid     = get_jwt_identity()
    founder = db.session.get(User, uid)
    if not founder:
        return jsonify({"error": "Founder account not found"}), 404
    if founder.role != "founder":
        return jsonify({"error": "Only founder can assign tasks"}), 403

    data        = request.get_json() or {}
    assignee_id = data.get("assignedTo", "").strip()

    if not assignee_id:
        return jsonify({"error": "Please select a team member to assign the task to"}), 400

    assignee = db.session.get(User, assignee_id)
    if not assignee:
        return jsonify({"error": "Selected team member not found. Please refresh and try again."}), 404

    try:
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
            createdAt = datetime.datetime.now().strftime("%b %d, %Y %I:%M %p")
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
    if user.role != "founder":
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
    if user.role != "founder":
        return jsonify({"error": "Only founder can delete tasks"}), 403
    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    db.session.delete(task)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/tasks/reset-completed", methods=["DELETE"])
@jwt_required()
def reset_completed_tasks():
    user = db.session.get(User, get_jwt_identity())
    if user.role != "founder":
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
        "text": m.text, "time": m.time, "channel": m.channel
    } for m in msgs])


@app.route("/api/messages", methods=["POST"])
@jwt_required()
def send_message():
    uid  = get_jwt_identity()
    user = db.session.get(User, uid)
    data = request.get_json()
    channel = data.get("channel", "all")

    msg = Message(
        id        = "m" + str(int(datetime.datetime.now().timestamp() * 1000)),
        from_name = user.name,
        fromId    = uid,
        text      = data.get("text", "").strip(),
        time      = now_str(),
        channel   = channel
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({"success": True, "message": {
        "id": msg.id, "from": msg.from_name, "fromId": msg.fromId,
        "text": msg.text, "time": msg.time, "channel": msg.channel
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
    if caller.role != "founder":
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
    if caller.role != "founder":
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
    if caller.role != "founder":
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
    if caller.role != "founder":
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
    if caller.role != "founder":
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
        "description": p.description, "created_at": p.created_at
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
    if caller.role != "founder":
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json() or {}
    cat  = data.get("category", "").strip()
    if cat not in ("Government", "Private", "B2C"):
        return jsonify({"error": "category must be Government, Private or B2C"}), 400
    p = Project(
        name=data.get("name", "").strip(), category=cat,
        client_name=data.get("client_name", "").strip(),
        status=data.get("status", "Active").strip(),
        start_date=data.get("start_date", "").strip(),
        team_members=data.get("team_members", "").strip(),
        description=data.get("description", "").strip(),
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
    if caller.role != "founder":
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
            db.session.add(Project(
                name=name, category=cat_norm,
                client_name=(row.get("client_name") or "").strip(),
                status=(row.get("status") or "Active").strip(),
                start_date=(row.get("start_date") or "").strip(),
                team_members=(row.get("team_members") or "").strip(),
                description=(row.get("description") or "").strip(),
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
    if caller.role != "founder":
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
            rows.append({
                "name": name, "category": cat,
                "client_name":  col(row, "Client Name",  "client name",  "Client"),
                "status":       col(row, "Status",        "status")        or "Active",
                "start_date":   col(row, "Start Date",    "start date",   "Date"),
                "team_members": col(row, "Team Members",  "team members", "Team"),
                "description":  col(row, "Description",   "description"),
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
                created_at=datetime.datetime.now().strftime("%b %d, %Y")
            ))
            created += 1

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
    if caller.role != "founder":
        return jsonify({"error": "Forbidden"}), 403
    p = db.session.get(Project, pid)
    if not p:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    for field in ("name", "category", "client_name", "status", "start_date", "team_members", "description"):
        if field in data:
            setattr(p, field, (data[field] or "").strip())
    db.session.commit()
    return jsonify({"success": True, "project": project_to_dict(p)})


@app.route("/api/projects/<int:pid>", methods=["DELETE"])
@jwt_required()
def delete_project(pid):
    caller = db.session.get(User, get_jwt_identity())
    if caller.role != "founder":
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
    if caller.role != "founder":
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
    if caller.role != "founder":
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
    if caller.role != "founder":
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
    if caller.role != "founder":
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
    if caller.role != "founder":
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
# PROJECT TASKS — EXCEL UPLOAD & PROGRESS API
# ─────────────────────────────────────────────

@app.route("/api/project-tasks/upload", methods=["POST"])
@jwt_required()
def upload_project_tasks():
    caller = db.session.get(User, get_jwt_identity())
    if caller.role != "founder":
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
    if caller.role != "founder":
        return jsonify({"error": "Forbidden"}), 403

    from sqlalchemy import func, case

    rows = (
        db.session.query(
            ProjectTask.project_name,
            func.count(ProjectTask.id).label("total_tasks"),
            func.sum(case((ProjectTask.status == "Completed",   1), else_=0)).label("completed_tasks"),
            func.sum(case((ProjectTask.status == "In Progress", 1), else_=0)).label("in_progress_tasks"),
            func.sum(case((ProjectTask.status == "Pending",     1), else_=0)).label("pending_tasks"),
        )
        .group_by(ProjectTask.project_name)
        .order_by(ProjectTask.project_name)
        .all()
    )

    result = []
    for r in rows:
        total     = r.total_tasks     or 0
        completed = int(r.completed_tasks  or 0)
        progress  = round((completed / total) * 100) if total > 0 else 0
        result.append({
            "project_name":      r.project_name,
            "total_tasks":       total,
            "completed_tasks":   completed,
            "in_progress_tasks": int(r.in_progress_tasks or 0),
            "pending_tasks":     int(r.pending_tasks     or 0),
            "progress":          progress,
        })

    return jsonify(result)


# ─────────────────────────────────────────────
# ERROR HANDLERS & STATIC FILES
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
