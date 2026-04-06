"""
DexVitals Medical Portal
A Flask application for patient and doctor access, vitals tracking, analytics, and PDF reporting.
"""

import io
from datetime import datetime
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_file,
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    login_user,
    login_required,
    logout_user,
    current_user,
    UserMixin,
)
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from home import (
    calculate_bmi,
    get_bmi_category,
    calculate_bmr,
    calculate_target_heart_rate_zones,
    calculate_tdee,
    build_health_recommendations,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = "replace-this-with-a-secure-secret"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///dexvitals.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"

ACTIVITY_LEVELS = [
    "Sedentary",
    "Lightly active",
    "Moderately active",
    "Very active",
    "Extra active",
]


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(140), nullable=False)
    email = db.Column(db.String(140), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="Patient")
    age = db.Column(db.Integer, nullable=False)
    sex = db.Column(db.String(16), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    vitals = db.relationship("Vital", backref="owner", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Vital(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    weight = db.Column(db.Float, nullable=False)
    height = db.Column(db.Float, nullable=False)
    resting_hr = db.Column(db.Integer, nullable=False)
    systolic = db.Column(db.Integer)
    diastolic = db.Column(db.Integer)
    activity_level = db.Column(db.String(40), nullable=False)
    notes = db.Column(db.String(400))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    bmi = db.Column(db.Float)
    bmi_category = db.Column(db.String(40))
    bmr = db.Column(db.Float)
    max_hr = db.Column(db.Integer)
    zone_60 = db.Column(db.Integer)
    zone_80 = db.Column(db.Integer)

    def update_metrics(self, user):
        self.bmi = calculate_bmi(self.weight, self.height)
        self.bmi_category = get_bmi_category(self.bmi)
        self.bmr = calculate_bmr(user.age, self.weight, self.height, user.sex)
        hr_zones = calculate_target_heart_rate_zones(user.age, self.resting_hr)
        self.max_hr = hr_zones["max_hr"]
        self.zone_60 = hr_zones["zone_60"]
        self.zone_80 = hr_zones["zone_80"]


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))



@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "Patient")
        age = request.form.get("age", "")
        sex = request.form.get("sex", "").strip().lower()

        if not all([name, email, password, role, age, sex]):
            flash("Please complete every registration field.", "warning")
            return render_template("register.html")

        try:
            age = int(age)
        except ValueError:
            flash("Please enter a valid numeric age.", "warning")
            return render_template("register.html")

        if User.query.filter_by(email=email).first():
            flash("That email is already registered.", "warning")
            return render_template("register.html")

        if sex not in {"male", "female"}:
            flash("Sex must be 'male' or 'female'.", "warning")
            return render_template("register.html")

        user = User(name=name, email=email, role=role.title(), age=age, sex=sex)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("Registration successful. Please sign in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            login_user(user)
            flash("Welcome back to DexVitals.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    patient_vitals = []
    latest_vital = None
    health_recommendations = {}
    chart_labels = []
    chart_weights = []
    chart_resting_hr = []
    raw_vitals = []

    if current_user.role == "Doctor":
        patient_vitals = User.query.filter_by(role="Patient").order_by(User.created_at.desc()).all()
    else:
        latest_vital = current_user.vitals.order_by(Vital.created_at.desc()).first()
        raw_vitals = (
            current_user.vitals.order_by(Vital.created_at.desc()).limit(30).all()
        )
        raw_vitals = list(reversed(raw_vitals))
        for entry in raw_vitals:
            chart_labels.append(entry.created_at.strftime("%b %d"))
            chart_weights.append(entry.weight)
            chart_resting_hr.append(entry.resting_hr)

        if latest_vital:
            health_recommendations = build_health_recommendations(
                latest_vital.bmi,
                latest_vital.bmr,
                current_user.age,
                current_user.sex,
                latest_vital.resting_hr,
                latest_vital.activity_level,
            )

    return render_template(
        "dashboard.html",
        activity_levels=ACTIVITY_LEVELS,
        latest_vital=latest_vital,
        vitals_history=raw_vitals if current_user.role != "Doctor" else None,
        health_recommendations=health_recommendations,
        chart_labels=chart_labels,
        chart_weights=chart_weights,
        chart_resting_hr=chart_resting_hr,
        patient_vitals=patient_vitals,
    )


@app.route("/submit_vitals", methods=["POST"])
@login_required
def submit_vitals():
    weight = request.form.get("weight", "")
    height = request.form.get("height", "")
    resting_hr = request.form.get("resting_hr", "")
    systolic = request.form.get("systolic", "")
    diastolic = request.form.get("diastolic", "")
    activity_level = request.form.get("activity_level", "")
    notes = request.form.get("notes", "")

    if not all([weight, height, resting_hr, activity_level]):
        flash("Please complete all required vitals fields.", "warning")
        return redirect(url_for("dashboard") + "#vitals")

    try:
        weight = float(weight)
        height = float(height)
        resting_hr = int(resting_hr)
        systolic = int(systolic) if systolic else None
        diastolic = int(diastolic) if diastolic else None
    except ValueError:
        flash("Please enter valid numeric values for your vitals.", "warning")
        return redirect(url_for("dashboard") + "#vitals")

    vital = Vital(
        owner=current_user,
        weight=weight,
        height=height,
        resting_hr=resting_hr,
        systolic=systolic,
        diastolic=diastolic,
        activity_level=activity_level,
        notes=notes,
    )
    vital.update_metrics(current_user)
    db.session.add(vital)
    db.session.commit()

    flash("Vitals saved successfully. Review your dashboard for updated insights.", "success")
    return redirect(url_for("dashboard") + "#overview")


@app.route("/report/<int:vital_id>")
@login_required
def report(vital_id):
    vital = Vital.query.get_or_404(vital_id)

    if current_user.role != "Doctor" and vital.user_id != current_user.id:
        flash("You do not have permission to access this report.", "danger")
        return redirect(url_for("dashboard"))

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle("DexVitals Patient Report")

    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(40, 750, "DexVitals Clinical Summary")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(40, 730, f"Patient: {vital.owner.name}")
    pdf.drawString(40, 715, f"Role: {vital.owner.role}")
    pdf.drawString(40, 700, f"Report Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    pdf.line(40, 690, 560, 690)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(40, 672, "Vitals Snapshot")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(40, 652, f"Weight: {vital.weight:.1f} kg")
    pdf.drawString(240, 652, f"Height: {vital.height:.1f} cm")
    pdf.drawString(40, 632, f"Resting HR: {vital.resting_hr} bpm")
    pdf.drawString(240, 632, f"Blood Pressure: {vital.systolic or 'N/A'}/{vital.diastolic or 'N/A'} mmHg")
    pdf.drawString(40, 612, f"Activity Level: {vital.activity_level}")
    pdf.drawString(40, 592, f"BMI: {vital.bmi:.1f} ({vital.bmi_category})")
    pdf.drawString(240, 592, f"BMR: {vital.bmr:.0f} kcal/day")
    pdf.drawString(40, 572, f"Target HR Zones: {vital.zone_60:.0f} - {vital.zone_80:.0f} bpm")

    if vital.notes:
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(40, 540, "Clinical Notes")
        pdf.setFont("Helvetica", 12)
        pdf.drawString(40, 520, vital.notes[:80])
        if len(vital.notes) > 80:
            pdf.drawString(40, 500, vital.notes[80:160])

    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    filename = f"DexVitals_Report_{vital.owner.name.replace(' ', '_')}_{vital.id}.pdf"
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


if __name__ == "__main__":
       with app.app_context():
        db.create_all() 
    
       app.run(debug=True)
