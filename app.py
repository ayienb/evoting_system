import os
import random
import requests
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
import uuid
import mysql.connector
from datetime import datetime
from mysql.connector import pooling

app = Flask(__name__)
app.secret_key = "evoting_secret_key"

# Token Serializer for Forgot Password
serializer = URLSafeTimedSerializer(app.secret_key)

MFA_TRACKER = {}

FACULTY_MAP = {
    "AI": "Faculty of Computer Science and Information Technology",
    "CI": "Faculty of Computer Science and Information Technology",
    "AB": "Faculty of Technical and Vocational Education",
    "AP": "Faculty of Technology Management and Business",
    "DE": "Faculty of Electrical and Electronic Engineering",
    "AW": "Faculty of Applied Sciences and Technology",
    "CD": "Faculty of Mechanical Engineering",
    "CF": "Faculty of Civil Engineering"
}

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'ayienbb0904@gmail.com'
app.config['MAIL_PASSWORD'] = 'afowqbqlpnhneasd'
mail = Mail(app)

# Configure the database connection pool
dbconfig = {
    "host": "127.0.0.1", 
    "port": 3306,                                         
    "user": "root",                                     
    "password": "",               
    "database": "evoting_system"                           
}

# Create a pool of 20 reusable connections
db_pool = pooling.MySQLConnectionPool(
    pool_name="evoting_pool",
    pool_size=20,
    pool_reset_session=True,
    **dbconfig
)

def get_db_connection():
    return db_pool.get_connection()

def log_system_activity(description):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO activity_log (description) VALUES (%s)", (description,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print("Failed to log activity:", e)

@app.route("/api/get-activity-logs", methods=["GET"])
def get_activity_logs():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT description, created_at FROM activity_log ORDER BY created_at DESC LIMIT 50")
        logs = cursor.fetchall()
        for log in logs:
            if isinstance(log['created_at'], datetime):
                log['created_at'] = log['created_at'].strftime("%Y-%m-%d %I:%M %p")
        cursor.close()
        conn.close()
        return jsonify(logs)
    except Exception as e:
        return jsonify([])

@app.route("/api/dashboard-stats", methods=["GET"])
def dashboard_stats():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Get current election status
    cursor.execute("SELECT public_election_status FROM settings WHERE election_id = 1")
    settings = cursor.fetchone()
    status = settings['public_election_status'] if settings else 'Pending'
    
    # Total Registered Users
    cursor.execute("SELECT COUNT(*) as count FROM user")
    total_reg = cursor.fetchone()['count']
    
    # Total Voted Users
    cursor.execute("SELECT COUNT(DISTINCT user_id) as count FROM votes")
    total_voted = cursor.fetchone()['count']
        
    turnout = round((total_voted / total_reg) * 100, 1) if total_reg > 0 else 0
    
    # Faculty Turnout Analytics (Forces ALL faculties to display)
    cursor.execute("""
        SELECT u.faculty, COUNT(DISTINCT u.user_id) as registered, COUNT(DISTINCT v.user_id) as voted
        FROM user u 
        LEFT JOIN votes v ON u.user_id = v.user_id
        WHERE u.faculty != 'Unknown Faculty' AND u.faculty IS NOT NULL
        GROUP BY u.faculty
    """)
    fac_data = cursor.fetchall()
    
    db_fac_stats = {row['faculty']: {'registered': row['registered'], 'voted': row['voted']} for row in fac_data}
    all_faculties = list(set(FACULTY_MAP.values()))
        
    faculty_stats = []
    for fac_name in all_faculties:
        reg = db_fac_stats.get(fac_name, {}).get('registered', 0)
        voted = db_fac_stats.get(fac_name, {}).get('voted', 0)
        fac_turnout = round((voted / reg) * 100, 1) if reg > 0 else 0
        
        faculty_stats.append({
            "faculty": fac_name,
            "registered": reg, 
            "voted": voted, 
            "turnout_pct": fac_turnout
        })
        
    faculty_stats.sort(key=lambda x: x['turnout_pct'], reverse=True)
    
    # Live Candidate Standings
    cursor.execute("""
        SELECT c.candidate_id, c.name, c.faculty, c.avatar_path, c.manifesto, COUNT(v.vote_id) as vote_count
        FROM candidate c 
        LEFT JOIN votes v ON c.candidate_id = v.candidate_id 
        WHERE c.status = 'Active' AND c.election_year = 2026
        GROUP BY c.candidate_id ORDER BY vote_count DESC
    """)
    candidate_stats = cursor.fetchall()
    
    cursor.execute("SELECT COUNT(*) as count FROM votes")
    total_votes_cast = cursor.fetchone()['count']
        
    for c in candidate_stats:
        c['vote_pct'] = round((c['vote_count'] / total_votes_cast) * 100, 1) if total_votes_cast > 0 else 0

    cursor.close()
    conn.close()
    
    return jsonify({
        "total_registered": total_reg, "total_voted": total_voted, "turnout_pct": turnout,
        "faculty_stats": faculty_stats, "candidate_stats": candidate_stats, "election_status": status
    })

@app.route('/api/faculty_progress')
def faculty_progress():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # 1. Get total students per faculty
    cursor.execute("""
        SELECT faculty, COUNT(*) as total_students 
        FROM user 
        WHERE faculty != 'Unknown Faculty' AND faculty IS NOT NULL
        GROUP BY faculty
    """)
    students_data = {row['faculty']: row['total_students'] for row in cursor.fetchall()}
    
    # 2. Get total votes cast per faculty
    cursor.execute("""
        SELECT u.faculty, COUNT(DISTINCT v.user_id) as votes_cast 
        FROM votes v
        JOIN user u ON v.user_id = u.user_id
        WHERE u.faculty != 'Unknown Faculty' AND u.faculty IS NOT NULL
        GROUP BY u.faculty
    """)
    votes_data = {row['faculty']: row['votes_cast'] for row in cursor.fetchall()}
    
    cursor.close()
    conn.close()
    
    # 3. Combine data and FORCE all 7 faculties to display
    progress_report = []
    
    # Extract every unique faculty name from your master FACULTY_MAP
    all_faculties = list(set(FACULTY_MAP.values()))
    
    for faculty_name in all_faculties:
        total = students_data.get(faculty_name, 0)
        voted = votes_data.get(faculty_name, 0)
        percentage = round((voted / total * 100), 2) if total > 0 else 0.0
        
        # Reverse lookup to grab the short code (e.g., AI, AW)
        code = next((k for k, v in FACULTY_MAP.items() if v == faculty_name), "UTHM")
        
        progress_report.append({
            "faculty_code": code,
            "faculty_name": faculty_name,
            "voted": voted,
            "total": total,
            "percentage": percentage
        })
        
    # Sort the list so the faculty with the highest turnout is always at the top!
    progress_report.sort(key=lambda x: x['percentage'], reverse=True)
        
    return jsonify(progress_report)

@app.route("/check-duplicate", methods=["POST"])
def check_duplicate():
    data = request.get_json() or {}
    matric = data.get("matric", "").strip().upper()
    email = data.get("email", "").strip().lower()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if matric:
        cursor.execute("SELECT matric_no FROM user WHERE matric_no = %s", (matric,))
        if cursor.fetchone(): return jsonify({"registered": True})
    if email:
        cursor.execute("SELECT email FROM user WHERE email = %s", (email,))
        if cursor.fetchone(): return jsonify({"registered": True})
    cursor.close()
    conn.close()
    return jsonify({"registered": False})

@app.route("/")
def home():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM candidate WHERE status = 'Active'")
    active_candidates = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("index.html", candidates=active_candidates)

@app.route("/signup")
def signup(): return render_template("signup.html")

# --- ADD THIS ENTIRE NEW ROUTE BLOCK ---
@app.route("/register", methods=["POST"])
def register():
    matric = request.form.get("matric").strip().upper()
    password = request.form.get("password")

    if not matric or not password:
        flash("Please fill in all fields.", "error")
        return redirect(url_for("signup"))

    # Automatically derive the student's faculty based on the first two letters of their matric number
    prefix = matric[:2]
    faculty = FACULTY_MAP.get(prefix, "Unknown Faculty")
    
    # Automatically generate the official UTHM Student email
    email = f"{matric.lower()}@student.uthm.edu.my"
    
    # Cryptographically hash the password for security
    hashed_pwd = generate_password_hash(password)

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Final safety check to prevent duplicates
        cursor.execute("SELECT * FROM user WHERE matric_no = %s", (matric,))
        if cursor.fetchone():
            flash("Matric number is already registered.", "error")
            return redirect(url_for("signup"))
        
        # Insert the new student into the database
        cursor.execute(
            "INSERT INTO user (matric_no, email, password_hash, faculty) VALUES (%s, %s, %s, %s)",
            (matric, email, hashed_pwd, faculty)
        )
        conn.commit()
        log_system_activity(f"New student account registered via portal: {matric}")
        flash("Registration successful! You can now log in.", "success")
        return redirect(url_for("home", launchLogin="true"))
        
    except Exception as e:
        conn.rollback()
        print("Registration Error:", e)
        flash("System error during registration. Please try again.", "error")
        return redirect(url_for("signup"))
    finally:
        cursor.close()
        conn.close()
# ---------------------------------------

@app.route("/email-verify")
def email_verify_page(): return render_template("email-verify.html")
@app.route("/otp")
def otp_page(): return render_template("otp-signup.html")
@app.route("/login-mfa")
def login_mfa_page():
    if 'pre_auth_user' not in session: return redirect(url_for("login_page"))
    return render_template("otp-login.html")
@app.route("/forgot-password")
def forgot_password_page(): return render_template("forgot-password.html")

@app.route("/forgot-password-request", methods=["POST"])
def forgot_password_request():
    email = request.form.get("target_email").strip().lower()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM user WHERE email = %s", (email,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if user:
        token = serializer.dumps(email, salt='email-reset')
        reset_link = url_for('reset_password_page', token=token, _external=True)
        try:
            msg = Message("Password Reset Link | UTHM E-Voting", sender="ayienbb0904@gmail.com", recipients=[email])
            msg.body = f"Hello,\n\nYou requested to reset your password. Click the link below to verify your identity and configure a new password:\n\n{reset_link}\n\nThis link will expire in 1 hour."
            mail.send(msg)
            flash("Reset link sent successfully! Check your email inbox.", "success")
        except Exception as e:
            flash("System Error: Failed to dispatch recovery email.", "error")
    else:
        flash("Unrecognized email address.", "error")
        
    return redirect(url_for("forgot_password_page"))

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password_page(token):
    try:
        email = serializer.loads(token, salt='email-reset', max_age=3600)
    except Exception:
        flash("The reset link is invalid or has expired. Please try again.", "error")
        return redirect(url_for("forgot_password_page"))
        
    if request.method == "POST":
        new_pwd = request.form.get("new_password")
        hashed = generate_password_hash(new_pwd)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE user SET password_hash = %s WHERE email = %s", (hashed, email))
        conn.commit()
        cursor.close()
        conn.close()
        log_system_activity(f"User {email} successfully reset their account password.")
        flash("Password updated successfully! Please login.", "success")
        return redirect(url_for("home"))
        
    return render_template("reset-password.html", token=token)

@app.route("/student-dashboard")
def student_dashboard():
    if "user_id" not in session: return redirect(url_for("login_page"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT candidate_id, name, faculty, manifesto, avatar_path FROM candidate WHERE status = 'Active'")
    active_candidates = cursor.fetchall()
    cursor.execute("SELECT election_id, tx_hash FROM election_participation WHERE user_id = %s", (session["user_id"],))
    voted_records = cursor.fetchall()
    voted_elections = [r['election_id'] for r in voted_records]
    voted_elections_data = {r['election_id']: r['tx_hash'] for r in voted_records}
    cursor.execute("SELECT profile_pic FROM user WHERE user_id = %s", (session["user_id"],))
    user_rec = cursor.fetchone()
    profile_pic = user_rec.get('profile_pic') if user_rec and user_rec.get('profile_pic') else None
    cursor.close()
    conn.close()

    return render_template("student-dashboard.html",
        full_name=session.get("full_name", "Student User"), matric_no=session.get("matric_no"),
        email=session.get("email"), faculty=session.get("faculty", "Unknown Faculty"),
        phone=session.get("phone", ""), address=session.get("address", ""),
        profile_pic=profile_pic, candidates=active_candidates,
        voted_elections=voted_elections, voted_elections_data=voted_elections_data)

@app.route("/update-profile", methods=["POST"])
def update_profile():
    if "user_id" not in session: return redirect(url_for("login_page"))
    
    full_name = request.form.get("full_name")
    phone = request.form.get("phone_number")
    address = request.form.get("residential_address")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Update the database
        cursor.execute("""
            UPDATE user 
            SET full_name = %s, phone = %s, address = %s 
            WHERE user_id = %s
        """, (full_name, phone, address, session["user_id"]))
        conn.commit()
        
        # Update the live session variables so the UI refreshes instantly
        session["full_name"] = full_name
        session["phone"] = phone
        session["address"] = address
        
        flash("Profile information updated successfully!", "success")
    except Exception as e:
        conn.rollback()
        flash("System error while updating profile.", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for("student_dashboard"))

@app.route("/update-profile-pic", methods=["POST"])
def update_profile_pic():
    if "user_id" not in session: return redirect(url_for("login_page"))
    
    file = request.files.get("profile_pic")
    if file and file.filename:
        # Create an uploads directory inside static if it doesn't exist
        uploads_dir = os.path.join(app.root_path, 'static', 'uploads')
        os.makedirs(uploads_dir, exist_ok=True)
        
        # Save the file securely with a unique timestamp
        filename = secure_filename(f"avatar_{session['user_id']}_{int(datetime.timestamp(datetime.now()))}_{file.filename}")
        save_path = os.path.join(uploads_dir, filename)
        file.save(save_path)
        
        file_path = "/static/uploads/" + filename
        
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE user SET profile_pic = %s WHERE user_id = %s", (file_path, session["user_id"]))
            conn.commit()
            flash("Profile picture updated successfully!", "success")
        except Exception as e:
            conn.rollback()
            flash("Failed to update profile picture.", "error")
        finally:
            cursor.close()
            conn.close()
            
    return redirect(url_for("student_dashboard"))

@app.route("/change-password-dashboard", methods=["POST"])
def change_password_dashboard():
    if "user_id" not in session: return redirect(url_for("login_page"))
    
    new_pwd = request.form.get("new_password")
    hashed = generate_password_hash(new_pwd)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE user SET password_hash = %s WHERE user_id = %s", (hashed, session["user_id"]))
        conn.commit()
        log_system_activity(f"Student {session.get('matric_no')} updated their account password.")
        flash("Security key updated successfully!", "success")
    except Exception as e:
        conn.rollback()
        flash("Failed to update password.", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for("student_dashboard"))

@app.route("/submit-vote", methods=["POST"])
def submit_vote():
    if "user_id" not in session: return jsonify({"success": False, "message": "Unauthorized"}), 401
    data = request.get_json()
    election_id = data.get("election_id")
    candidate_ids = data.get("candidate_ids", []) 
    if not election_id or not candidate_ids: return jsonify({"success": False, "message": "Invalid data."}), 400

    student_matric = session.get("matric_no")
    
    # FIX: Join multiple candidates into a single comma-separated string for the blockchain ledger
    candidate_id_str = ",".join(map(str, candidate_ids))
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT public_election_status, faculty_election_status, public_election_id, faculty_election_id FROM settings WHERE election_id=1")
    settings = cursor.fetchone()
    
    if settings:
        if election_id == settings['public_election_id'] and settings['public_election_status'] == 'Paused':
            cursor.close(); conn.close()
            return jsonify({"success": False, "message": "Election is currently paused by Superadmin."}), 403
        if election_id == settings['faculty_election_id'] and settings['faculty_election_status'] == 'Paused':
            cursor.close(); conn.close()
            return jsonify({"success": False, "message": "Election is currently paused by Superadmin."}), 403

    try:
        blockchain_response = requests.post(
            "http://localhost:3000/api/blockchain/vote", 
            json={"electionId": election_id, "studentMatric": student_matric, "candidateId": candidate_id_str}, timeout=10
        )
        blockchain_data = blockchain_response.json()
        if blockchain_response.status_code != 200 or not blockchain_data.get("success"):
            cursor.close(); conn.close()
            return jsonify({"success": False, "message": f"Blockchain Rejection: {blockchain_data.get('message', 'Transaction failed')}"})
    except requests.exceptions.RequestException as e:
        cursor.close(); conn.close()
        return jsonify({"success": False, "message": "Critical Error: Could not connect to the Hyperledger Voting Ledger."}), 500

    try:
        tx_hash_dummy = blockchain_data.get("txId", f"0x{uuid.uuid4().hex[:16]}")
        cursor.execute("INSERT INTO election_participation (user_id, election_id, tx_hash) VALUES (%s, %s, %s)", (session["user_id"], election_id, tx_hash_dummy))
        
        # FIX: Loop through array and insert all selected candidates into the local DB
        for cid in candidate_ids:
            cursor.execute("INSERT INTO votes (user_id, election_id, candidate_id) VALUES (%s, %s, %s)", (session["user_id"], election_id, cid))
            
        conn.commit()
        success = True
    except Exception as e:
        conn.rollback()
        success = False
        
    cursor.close()
    conn.close()
    
    if success:
        log_system_activity(f"Student {student_matric} cast a secure vote.")
        # FIX: Return the actual transaction hash so the UI can display it
        return jsonify({"success": True, "txId": tx_hash_dummy})
        
    return jsonify({"success": False, "message": "Database sync failed."}), 500

@app.route("/admin-dashboard")
def admin_dashboard():
    if "admin_authenticated" not in session and "super_authenticated" not in session: return redirect(url_for("home"))
    search_query = request.args.get("search", "").strip()
    filter_year = request.args.get("year", "2026")
    filter_faculty = request.args.get("faculty", "")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    query = "SELECT * FROM candidate WHERE election_year = %s"
    params = [filter_year]
    if search_query:
        query += " AND name LIKE %s"
        params.append(f"%{search_query}%")
    if filter_faculty:
        query += " AND faculty = %s"
        params.append(filter_faculty)
        
    cursor.execute(query, tuple(params))
    candidates = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("admin-dashboard.html", candidates=candidates, selected_year=filter_year, search_query=search_query, selected_faculty=filter_faculty)

# --- ADD THIS ENTIRE ROUTE TO HANDLE CANDIDATE CREATION ---
@app.route("/add-candidate", methods=["POST"])
def add_candidate():
    # Security check: Ensure only admins can do this
    if "admin_authenticated" not in session and "super_authenticated" not in session: 
        return redirect(url_for("home"))
        
    name = request.form.get("name")
    matric_no = request.form.get("matric_no").upper()
    faculty = request.form.get("faculty")
    manifesto = request.form.get("manifesto")
    file = request.files.get("image")
    
    file_path = None
    if file and file.filename:
        # Save the uploaded candidate photo securely
        uploads_dir = os.path.join(app.root_path, 'static', 'uploads')
        os.makedirs(uploads_dir, exist_ok=True)
        filename = secure_filename(f"candidate_{matric_no}_{int(datetime.timestamp(datetime.now()))}_{file.filename}")
        file.save(os.path.join(uploads_dir, filename))
        file_path = "/static/uploads/" + filename

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Determine current election year (e.g., 2026)
        current_year = datetime.now().year
        
        cursor.execute("""
            INSERT INTO candidate (name, matric_no, faculty, manifesto, avatar_path, election_year, status) 
            VALUES (%s, %s, %s, %s, %s, %s, 'Active')
        """, (name, matric_no, faculty, manifesto, file_path, current_year))
        conn.commit()
        
        log_system_activity(f"Admin registered new candidate: {name} ({matric_no})")
        flash("Candidate added successfully!", "success")
    except Exception as e:
        conn.rollback()
        print("Error adding candidate:", e)
        flash("Failed to add candidate. Ensure matric number is unique.", "error")
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for("admin_dashboard", active_tab="manage-candidates"))

@app.route("/toggle-candidate-status/<int:candidate_id>", methods=["POST"])
def toggle_candidate_status(candidate_id):
    if "admin_authenticated" not in session and "super_authenticated" not in session: 
        return redirect(url_for("home"))
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT status, name FROM candidate WHERE candidate_id = %s", (candidate_id,))
        result = cursor.fetchone()
        if result:
            new_status = 'Inactive' if result[0] == 'Active' else 'Active'
            cursor.execute("UPDATE candidate SET status = %s WHERE candidate_id = %s", (new_status, candidate_id))
            conn.commit()
            log_system_activity(f"Admin changed candidate {result[1]} status to {new_status}")
    except Exception as e:
        conn.rollback()
        print("Toggle error:", e)
    finally:
        cursor.close()
        conn.close()
        
    return redirect(url_for("admin_dashboard", active_tab="manage-candidates"))

# --- UPDATED: SUPERADMIN DASHBOARD (ADDED RESULTS LOGIC) ---
@app.route("/superadmin-dashboard")
def superadmin_dashboard():
    if "super_authenticated" not in session: return redirect(url_for("home"))
    
    search_query = request.args.get("search", "").strip()
    filter_year = request.args.get("year", "2026")
    filter_faculty = request.args.get("faculty", "")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Candidate DB
    query = "SELECT * FROM candidate WHERE election_year = %s"
    params = [filter_year]
    if search_query:
        query += " AND name LIKE %s"
        params.append(f"%{search_query}%")
    if filter_faculty:
        query += " AND faculty = %s"
        params.append(filter_faculty)
        
    cursor.execute(query, tuple(params))
    candidates = cursor.fetchall()
    
    # Need current public election ID to filter results safely to zero if newly released
    cursor.execute("SELECT public_election_id FROM settings WHERE election_id = 1")
    settings = cursor.fetchone()
    pub_id = settings['public_election_id'] if settings else None
    
    # Results Filter Logic (Strictly Tied to Active Session)
    if pub_id:
        result_query = """
            SELECT c.candidate_id, c.name, c.faculty, c.avatar_path, COUNT(v.vote_id) as vote_count
            FROM candidate c LEFT JOIN votes v ON c.candidate_id = v.candidate_id AND v.election_id = %s
            WHERE c.election_year = %s
        """
        result_params = [pub_id, filter_year]
    else:
        result_query = """
            SELECT c.candidate_id, c.name, c.faculty, c.avatar_path, 0 as vote_count
            FROM candidate c 
            WHERE c.election_year = %s
        """
        result_params = [filter_year]
        
    if filter_faculty:
        result_query += " AND c.faculty = %s"
        result_params.append(filter_faculty)
        
    if pub_id:
        result_query += " GROUP BY c.candidate_id ORDER BY vote_count DESC"
        
    cursor.execute(result_query, tuple(result_params))
    results_data = cursor.fetchall()
    
    total_scope_votes = sum(r['vote_count'] for r in results_data)
    for r in results_data:
        r['vote_pct'] = round((r['vote_count'] / total_scope_votes) * 100, 1) if total_scope_votes > 0 else 0
    
    # Admins & Ledger Fetch
    cursor.execute("SELECT * FROM admins")
    admins = cursor.fetchall()
    
    cursor.execute("""
        SELECT e.election_id, e.user_id, e.tx_hash, e.participated_at, u.matric_no 
        FROM election_participation e 
        JOIN user u ON e.user_id = u.user_id 
        ORDER BY e.participated_at DESC LIMIT 50
    """)
    ledgers = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    return render_template("superadmin-dashboard.html", 
                           candidates=candidates, 
                           admins=admins, 
                           results_data=results_data, 
                           ledgers=ledgers, 
                           selected_year=filter_year, 
                           search_query=search_query, 
                           selected_faculty=filter_faculty)

@app.route("/api/superadmin/add-admin", methods=["POST"])
def add_admin():
    if "super_authenticated" not in session: return jsonify({"success": False})
    data = request.get_json()
    hashed_pwd = generate_password_hash(data['password'])
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO admins (full_name, email, password_hash) VALUES (%s, %s, %s)", (data['name'], data['email'], hashed_pwd))
        conn.commit()
        log_system_activity(f"SUPERADMIN: Created new admin account for {data['email']}.")
        cursor.close()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/superadmin/toggle-admin", methods=["POST"])
def toggle_admin():
    if "super_authenticated" not in session: return jsonify({"success": False})
    admin_id = request.json.get("admin_id")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM admins WHERE admin_id=%s", (admin_id,))
    current = cursor.fetchone()[0]
    new_stat = 'Inactive' if current == 'Active' else 'Active'
    cursor.execute("UPDATE admins SET status=%s WHERE admin_id=%s", (new_stat, admin_id))
    conn.commit()
    log_system_activity(f"SUPERADMIN: Modified Admin #{admin_id} status to {new_stat}.")
    cursor.close()
    conn.close()
    return jsonify({"success": True})

@app.route("/api/superadmin/emergency", methods=["POST"])
def emergency_control():
    if "super_authenticated" not in session: 
        return jsonify({"success": False, "message": "Session expired. Please log in again."})
    
    try:
        data = request.get_json() or {}
        action = data.get("action")
        target = data.get("target")

        if not action or not target:
            return jsonify({"success": False, "message": "Missing action or target parameters."})

        conn = get_db_connection()
        cursor = conn.cursor()
        status_col = f"{target}_election_status"

        if action == "pause":
            cursor.execute(f"UPDATE settings SET {status_col} = 'Paused' WHERE election_id = 1")
            log_system_activity(f"SUPERADMIN EMERGENCY: Force Stopped {target.upper()} Election.")
        elif action == "reopen":
            cursor.execute(f"UPDATE settings SET {status_col} = 'Live' WHERE election_id = 1")
            log_system_activity(f"SUPERADMIN: Reopened {target.upper()} Election.")
        elif action == "end":
            cursor.execute(f"UPDATE settings SET {status_col} = 'Ended' WHERE election_id = 1")
            log_system_activity(f"SUPERADMIN: Archived/Ended {target.upper()} Election.")
        elif action == "delete":
            cursor.execute("SELECT public_election_id, faculty_election_id FROM settings WHERE election_id=1")
            old_ids = cursor.fetchone()
            if target == "public":
                if old_ids and old_ids[0]:
                    cursor.execute("DELETE FROM votes WHERE election_id = %s", (old_ids[0],))
                    cursor.execute("DELETE FROM election_participation WHERE election_id = %s", (old_ids[0],))
                cursor.execute("UPDATE settings SET public_election_id=NULL, public_start_time=NULL, public_end_time=NULL, public_election_status='Pending' WHERE election_id=1")
            else:
                if old_ids and old_ids[1]:
                    cursor.execute("DELETE FROM votes WHERE election_id = %s", (old_ids[1],))
                    cursor.execute("DELETE FROM election_participation WHERE election_id = %s", (old_ids[1],))
                cursor.execute("UPDATE settings SET faculty_election_id=NULL, faculty_start_time=NULL, target_faculty=NULL, faculty_end_time=NULL, faculty_election_status='Pending' WHERE election_id=1")
            log_system_activity(f"SUPERADMIN EMERGENCY: Permanently Deleted {target.upper()} Session.")

        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"success": True})
        
    except Exception as e:
        print("Emergency Exec Error:", e)
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/submit-report", methods=["POST"])
def submit_report():
    if "admin_authenticated" not in session: return jsonify({"success": False})
    
    scope = request.form.get("scope", "General")
    remarks = request.form.get("remarks")
    target_email = request.form.get("target_email")
    file = request.files.get("report_file")
    
    file_path = None
    save_path = None
    
    if file and file.filename:
        reports_dir = os.path.join(app.root_path, 'static', 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        filename = secure_filename(f"report_{int(datetime.timestamp(datetime.now()))}_{file.filename}")
        save_path = os.path.join(reports_dir, filename)
        file.save(save_path)
        file_path = "/static/reports/" + filename

    # --- NEW: SEND THE EMAIL WITH ATTACHMENT ---
    if target_email:
        try:
            msg = Message(f"Official Election Audit Report - {scope.upper()}", sender=app.config['MAIL_USERNAME'], recipients=[target_email])
            msg.body = f"Hello,\n\nPlease find the attached official election audit report and administrative remarks regarding the {scope} session.\n\nAdmin Remarks:\n{remarks}\n\nRegards,\nUniPoll System Administrator"
            
            # Attach the file to the email
            if save_path:
                with open(save_path, 'rb') as fp:
                    msg.attach(filename, file.content_type, fp.read())
                    
            mail.send(msg)
        except Exception as e:
            print("Email dispatch error:", e)

    sender = "System Admin"
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO reports (sender, remarks, file_path) VALUES (%s, %s, %s)", (sender, remarks, file_path))
    conn.commit()
    
    log_system_activity(f"Admin dispatched an Official Report to {target_email}.")
    
    cursor.close()
    conn.close()
    return jsonify({"success": True})

@app.route("/login-page")
def login_page(): 
    return redirect(url_for("home"))
@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))

@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username").strip().lower()
    password = request.form.get("password")
    user_data = None

    if username == "unipollofficial@gmail.com" and password == "superadmin123":
        user_data = {"role": "super", "email": username, "full_name": "Root Admin"}
        
    elif username == "unipolladmin123@gmail.com" and password == "admin123":
        user_data = {"role": "admin", "email": username, "full_name": "System Admin"}
    
    else:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM admins WHERE email=%s AND status='Active'", (username,))
        admin_user = cursor.fetchone()
        
        if admin_user and check_password_hash(admin_user["password_hash"], password):
            user_data = {"role": "admin", "email": username, "full_name": admin_user["full_name"]}
        else:
            cursor.execute("SELECT * FROM user WHERE email=%s", (username,))
            db_user = cursor.fetchone()
            if db_user and check_password_hash(db_user["password_hash"], password):
                user_data = {"role": "student", "id": db_user["user_id"], "matric_no": db_user["matric_no"], "email": db_user["email"], "faculty": db_user.get("faculty"), "full_name": db_user.get("full_name")}
        conn.close()

    if user_data:
        target_number = random.randint(10, 99)
        auth_token = str(uuid.uuid4()) 
        session['login_target'] = target_number
        session['auth_token'] = auth_token
        
        choices = [target_number]
        while len(choices) < 3:
            num = random.randint(10, 99)
            if num not in choices:
                choices.append(num)
        random.shuffle(choices)
        
        MFA_TRACKER[auth_token] = {"target": target_number, "verified": False, "user_data": user_data, "choices": choices}

        base_url = request.host_url.rstrip('/')
        link1 = f"{base_url}/process-selection/{auth_token}/{choices[0]}"
        link2 = f"{base_url}/process-selection/{auth_token}/{choices[1]}"
        link3 = f"{base_url}/process-selection/{auth_token}/{choices[2]}"

        try:
            msg = Message("Verify Your Login", sender="ayienbb0904@gmail.com", recipients=[user_data["email"]])
            msg.body = f"A login attempt was detected. To authorize access, please select the matching number:\n\n1) {choices[0]} -> {link1}\n2) {choices[1]} -> {link2}\n3) {choices[2]} -> {link3}\n\nIf you did not request this, ignore this email."
            msg.html = f"""
            <div style="font-family: Arial, sans-serif; text-align: center; padding: 30px; background-color: #0f0c29; border-radius: 10px; color: white;">
                <h2 style="color: #d946ef; margin-bottom: 10px;">Device Authorization</h2>
                <p style="color: #b3b3b3; font-size: 16px; margin-bottom: 30px;">A login attempt was detected. Please click the number below that exactly matches the number shown on your login screen:</p>
                <div style="margin: 20px 0;">
                    <a href="{link1}" style="display: inline-block; background: linear-gradient(90deg, #9d50bb, #d946ef); color: #ffffff; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-size: 24px; font-weight: bold; margin: 0 10px;">{choices[0]}</a>
                    <a href="{link2}" style="display: inline-block; background: linear-gradient(90deg, #9d50bb, #d946ef); color: #ffffff; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-size: 24px; font-weight: bold; margin: 0 10px;">{choices[1]}</a>
                    <a href="{link3}" style="display: inline-block; background: linear-gradient(90deg, #9d50bb, #d946ef); color: #ffffff; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-size: 24px; font-weight: bold; margin: 0 10px;">{choices[2]}</a>
                </div>
                <p style="color: #888; font-size: 12px; margin-top: 30px;">If you did not initiate this login, please secure your account immediately.</p>
            </div>
            """
            mail.send(msg)
            return redirect(url_for("login_waiting_page"))
            
        except Exception as e:
            print("Mail Send Failure:", e)
            flash(f"System Error: Could not dispatch the verification email. Reason: {str(e)}", "error")
            return redirect(url_for("login_page"))
    
    flash("Invalid email or password.", "error")
    return redirect(url_for("login_page"))

@app.route("/login-waiting")
def login_waiting_page():
    if 'login_target' not in session: return redirect(url_for("login_page"))
    return render_template("login-waiting.html", target_number=session['login_target'])

@app.route("/process-selection/<token>/<int:selected_number>")
def process_selection(token, selected_number):
    if token not in MFA_TRACKER: return "Expired or invalid verification link.", 400
    if selected_number == MFA_TRACKER[token]["target"]:
        MFA_TRACKER[token]["verified"] = True 
        return "<h1 style='color:green; font-family:sans-serif; text-align:center; margin-top:50px;'>Identity confirmed! You can return to your laptop screen.</h1>"
    MFA_TRACKER.pop(token, None)
    return "<h1 style='color:red; font-family:sans-serif; text-align:center; margin-top:50px;'>Security validation failed. Wrong number clicked.</h1>", 403

@app.route("/device-auth/<token>")
def device_auth(token):
    if token not in MFA_TRACKER:
        return "Expired or invalid verification link.", 400
    choices = MFA_TRACKER[token].get("choices", [])
    return render_template("number-select.html", token=token, choices=choices)

@app.route("/check-login-status")
def check_login_status():
    token = session.get('auth_token')
    if token in MFA_TRACKER and MFA_TRACKER[token]["verified"]:
        user = MFA_TRACKER[token]["user_data"]
        redirect_url = "/student-dashboard"
        
        if user["role"] == "super":
            session["super_authenticated"] = True
            redirect_url = "/superadmin-dashboard"
            log_system_activity("Superadmin authenticated securely.")
        elif user["role"] == "admin":
            session["admin_authenticated"] = True
            redirect_url = "/admin-dashboard"
            log_system_activity(f"Admin ({user['full_name']}) authenticated.")
        else:
            session["user_id"] = user["id"]
            session["matric_no"] = user["matric_no"]
            session["email"] = user["email"]
            session["faculty"] = user["faculty"]
            session["full_name"] = user["full_name"]
            log_system_activity(f"Student {user['matric_no']} logged in.")
        
        MFA_TRACKER.pop(token, None)
        session.pop('auth_token', None)
        return jsonify({"success": True, "redirect_url": redirect_url})
    return jsonify({"success": False})

@app.route("/api/get-system-settings", methods=["GET"])
def get_system_settings():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM settings WHERE election_id = 1")
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        for key in ['public_end_time', 'faculty_end_time', 'public_start_time', 'faculty_start_time']:
            if key in row and row[key] and isinstance(row[key], datetime):
                row[key] = row[key].isoformat()
    return jsonify(row or {})

@app.route("/api/db-release-session", methods=["POST"])
def db_release_session():
    data = request.get_json() or {}
    el_type = data.get("type")
    start_dt = data.get("start")
    end_dt = data.get("end")
    scope = data.get("scope", "All Students")
    generated_id = f"EL-{el_type.upper()}-{random.randint(10000, 99999)}"

    conn = get_db_connection()
    cursor = conn.cursor()

    # POWERFUL WIPE: Delete all old ghost data for the specific type of election
    if el_type == 'public':
        cursor.execute("DELETE FROM votes WHERE election_id LIKE 'EL-PUBLIC-%' OR election_id REGEXP '^[0-9]+$'")
        cursor.execute("DELETE FROM election_participation WHERE election_id LIKE 'EL-PUBLIC-%' OR election_id REGEXP '^[0-9]+$'")
    else:
        cursor.execute("DELETE FROM votes WHERE election_id LIKE 'EL-FACULTY-%'")
        cursor.execute("DELETE FROM election_participation WHERE election_id LIKE 'EL-FACULTY-%'")

    cursor.execute("SELECT COUNT(*) FROM settings")
    exists = cursor.fetchone()[0]
    if exists == 0:
        cursor.execute("INSERT INTO settings (election_id, public_election_status, faculty_election_status) VALUES (1, 'Pending','Pending')")
        conn.commit()

    if el_type == 'public':
        cursor.execute("""
            UPDATE settings 
            SET public_election_id = %s, public_start_time = %s, public_end_time = %s, public_election_status = 'Live' 
            WHERE election_id = 1
        """, (generated_id, start_dt, end_dt))
    else:
        cursor.execute("""
            UPDATE settings 
            SET faculty_election_id = %s, faculty_start_time = %s, faculty_end_time = %s, faculty_election_status = 'Live', target_faculty = %s 
            WHERE election_id = 1
        """, (generated_id, start_dt, end_dt, scope))
        
    conn.commit()
    cursor.close()
    conn.close()
    log_system_activity(f"Admin released {el_type.upper()} Voting Session: {generated_id}.")
    return jsonify({"success": True, "generated_id": generated_id})

@app.route("/send-admin-release-otp", methods=["POST"])
def send_admin_release_otp():
    if "admin_authenticated" not in session: return jsonify({"success": False, "message": "Unauthorized"}), 401
    otp = str(random.randint(100000, 999999))
    session['admin_release_otp'] = otp
    try:
        msg = Message("Security Alert: Voting Session Release Request", sender="ayienbb0904@gmail.com", recipients=["unipolladmin123@gmail.com"])
        msg.body = f"Your secure transmission authorize validation token key is: {otp}"
        mail.send(msg)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/verify-admin-release-otp", methods=["POST"])
def verify_admin_release_otp():
    data = request.get_json() or {}
    entered_otp = data.get("otp")
    stored_otp = session.get("admin_release_otp")

    if not stored_otp or entered_otp != stored_otp:
        return jsonify({"success": False, "message": "Security token mismatch fault."}), 403

    session.pop("admin_release_otp", None)
    
    el_type = data.get("type")
    start_dt = data.get("start")
    end_dt = data.get("end")
    scope = data.get("scope", "All Students")
    generated_id = f"EL-{el_type.upper()}-{random.randint(10000, 99999)}"

    conn = get_db_connection()
    cursor = conn.cursor()

    # POWERFUL WIPE: Delete all old ghost data for the specific type of election
    if el_type == 'public':
        cursor.execute("DELETE FROM votes WHERE election_id LIKE 'EL-PUBLIC-%' OR election_id REGEXP '^[0-9]+$'")
        cursor.execute("DELETE FROM election_participation WHERE election_id LIKE 'EL-PUBLIC-%' OR election_id REGEXP '^[0-9]+$'")
    else:
        cursor.execute("DELETE FROM votes WHERE election_id LIKE 'EL-FACULTY-%'")
        cursor.execute("DELETE FROM election_participation WHERE election_id LIKE 'EL-FACULTY-%'")

    cursor.execute("SELECT COUNT(*) FROM settings")
    exists = cursor.fetchone()[0]
    if exists == 0:
        cursor.execute("INSERT INTO settings (election_id, public_election_status, faculty_election_status) VALUES (1, 'Pending','Pending')")
        conn.commit()

    if el_type == 'public':
        cursor.execute("""
            UPDATE settings 
            SET public_election_id = %s, public_start_time = %s, public_end_time = %s, public_election_status = 'Live' 
            WHERE election_id = 1
        """, (generated_id, start_dt, end_dt))
    else:
        cursor.execute("""
            UPDATE settings 
            SET faculty_election_id = %s, faculty_start_time = %s, faculty_end_time = %s, faculty_election_status = 'Live', target_faculty = %s 
            WHERE election_id = 1
        """, (generated_id, start_dt, end_dt, scope))
        
    conn.commit()
    cursor.close()
    conn.close()
    log_system_activity(f"Admin released {el_type.upper()} Voting Session: {generated_id}.")
    return jsonify({"success": True, "generated_id": generated_id})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)