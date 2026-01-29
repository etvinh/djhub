import os
import re
import uuid
import secrets
import smtplib
from email.message import EmailMessage
import json
import urllib.request
from collections import defaultdict, deque
from time import time
from urllib.parse import urlparse, urljoin
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    abort,
    flash,
    session
)
from datetime import datetime, timedelta
from sqlalchemy import or_, func, and_
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm.exc import NoResultFound
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_wtf import CSRFProtect
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    current_user,
    login_required
)

# Models
from models import Listing
from models import Profile
from models import Campus
from models import Users
from models import Genre, Location, BookingRequest, ListingNotification, Review, ProfileTrack
from models import Conversation, Message, ListingPhoto
from extensions import db
 

##################################Flask App Initialization #########################################################
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or os.urandom(32) #use a secure random key if not set

#url fix for postgres
db_url = os.environ.get("DATABASE_URL") 
if db_url:
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url or "sqlite:///djhub.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False



#upload config
app.config["UPLOAD_FOLDER"] = os.path.join("static", "uploads")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024


#prevent xss attacks
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"

#smtp/email config
app.config["SMTP_HOST"] = os.environ.get("SMTP_HOST")
app.config["SMTP_PORT"] = int(os.environ.get("SMTP_PORT", "587"))
app.config["SMTP_USERNAME"] = os.environ.get("SMTP_USERNAME")
app.config["SMTP_PASSWORD"] = os.environ.get("SMTP_PASSWORD")
app.config["SMTP_USE_TLS"] = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
app.config["SMTP_FROM"] = os.environ.get("SMTP_FROM")
app.config["SENDGRID_API_KEY"] = os.environ.get("SENDGRID_API_KEY")

db.init_app(app)
csrf = CSRFProtect(app) #csrf protection https://portswigger.net/web-security/csrf

# --- Reference data seed helpers ---
def seed_reference_data():
    if not Campus.query.first():
        db.session.add(Campus(name="UC Santa Cruz", slug="ucsc", is_active=True))
    if not Location.query.first():
        db.session.add(Location(name="Santa Cruz"))
    if not Genre.query.first():
        for name in ["House", "Techno", "HipHop", "EDM", "Lo-Fi"]:
            db.session.add(Genre(name=name))
    db.session.commit()

if os.environ.get("AUTO_CREATE_DB") == "1":
    with app.app_context():
        db.create_all()
        app.logger.info("AUTO_CREATE_DB enabled: ensured tables exist.")
        if os.environ.get("AUTO_SEED_REFERENCE") == "1":
            seed_reference_data()
            app.logger.info("AUTO_SEED_REFERENCE enabled: seeded campus/genres/locations.")

# --- Validation / rate limit config ---
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
INSTAGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
SPOTIFY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_MESSAGE_LENGTH = 1000
rate_limit_store = defaultdict(deque)

# --- Error handlers ---
@app.errorhandler(413)
def request_entity_too_large(error):
    flash("File too large. Max upload size is 10MB.", "danger")
    return redirect(request.referrer or url_for("my_profile"))

# --- Rate limiting helpers ---
def is_rate_limited(key: str, limit: int, window_seconds: int) -> bool:
    now = time()
    bucket = rate_limit_store[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False

# --- URL helpers ---
def is_safe_redirect(target: str) -> bool:
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


# --- Flask-Login Initialization ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"

############################################# Campus Selection ##########################################################


# --- Landing & campus selection ---
@app.get("/", endpoint="landing")
def landing():
    if session.get('campus_slug'):
        return redirect(url_for("login"))
    return render_template("landing.html")

# --- Campus Selection Route ---
@app.route("/select-campus", methods=["GET", "POST"], endpoint="select_campus")
def select_campus():
    # If the campus is already in the session, skip this screen and go to listings
    if session.get('campus_slug'):
        return redirect(url_for("listings_feed")) 

    try:
        campuses = Campus.query.filter_by(is_active=True).all()
    except ProgrammingError:
        db.session.rollback()
        db.create_all()
        seed_reference_data()
        campuses = Campus.query.filter_by(is_active=True).all()
    if not campuses:
        seed_reference_data()
        campuses = Campus.query.filter_by(is_active=True).all()
    
    if request.method == "POST":
        selected_slug = request.form.get("campus_slug")
        campus = Campus.query.filter_by(slug=selected_slug).first()

        if campus:
            session['campus_slug'] = campus.slug
            flash(f"Campus set to {campus.name}.", "success")
            return redirect(url_for("listings_feed")) # Send users to explore first
        else:
            flash("Invalid campus selection.", "danger")
            return redirect(url_for("select_campus"))
            
    return render_template("select_campus.html", campuses=campuses)


############################################## Authentication ####################################################################






# --- Auth: login/logout/session ---
@login_manager.user_loader
def load_user(user_id):
    try:
        return Users.query.get(int(user_id))
    except NoResultFound:
        return None


@app.route("/login", methods=["GET", "POST"])
def login():
    # 1. Check for campus selection first
    if not session.get('campus_slug'):
        return redirect(url_for("select_campus"))
        
    # If already logged in, skip the form/gateway and go straight to the feed
    if current_user.is_authenticated:
        return redirect(url_for("listings_feed"))
        
    if request.method == "POST":
        if is_rate_limited(f"login:{request.remote_addr}", 10, 60):
            flash("Too many login attempts. Try again in a minute.", "danger")
            return redirect(url_for("login"))
        username = (request.form.get("username") or "").strip()
        if username.startswith("@"):
            username = username[1:]
        password = request.form.get("password")
        if not USERNAME_RE.match(username):
            flash("Login failed. Check your username and password.", "danger")
            return redirect(url_for("login"))

        user = Users.query.filter(func.lower(Users.username) == username.lower()).first()

        if user and check_password_hash(user.password, password):
            if not user.email_verified:
                session["pending_verification_user_id"] = user.id
                if not user.email_verification_expires_at or user.email_verification_expires_at < datetime.utcnow():
                    sent = issue_verification_code(user)
                    if sent:
                        flash("Your verification code expired. A new one was sent.", "info")
                    else:
                        flash("Your verification code expired. Please resend a new one.", "warning")
                else:
                    flash("Please verify your email to continue.", "warning")
                return redirect(url_for("verify_email"))
            login_user(user)
            flash(f"Welcome back, {user.username}!", "success")
            next_page = request.args.get("next")
            if next_page and is_safe_redirect(next_page):
                return redirect(next_page)
            return redirect(url_for("listings_feed"))
        else:
            flash("Login failed. Check your username and password.", "danger")

    # This renders the combined login/gateway template
    return render_template("login.html")

@app.get("/logout")
@login_required 
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("listings_feed"))


@app.context_processor
def inject_auth():
    pending_booking_count = 0
    booking_updates_count = 0
    unread_messages_count = 0
    current_user_profile = None
    if current_user.is_authenticated:
        current_user_profile = Profile.query.filter_by(user_id=current_user.id).first()
        pending_booking_count = (ListingNotification.query
                                 .join(Listing, ListingNotification.listing_id == Listing.id)
                                 .join(Profile, Listing.profile_id == Profile.id)
                                 .filter(ListingNotification.recipient_id == current_user.id,
                                         ListingNotification.is_read.is_(False),
                                         Profile.user_id == current_user.id)
                                 .count())
        booking_updates_count = (ListingNotification.query
                                 .join(Listing, ListingNotification.listing_id == Listing.id)
                                 .join(Profile, Listing.profile_id == Profile.id)
                                 .filter(ListingNotification.recipient_id == current_user.id,
                                         ListingNotification.is_read.is_(False),
                                         Profile.user_id != current_user.id)
                                 .count())
        unread_messages_count = (Message.query
                                 .join(Conversation, Message.conversation_id == Conversation.id)
                                 .filter(or_(
                                     and_(Conversation.user1_id == current_user.id,
                                          Message.read_by_user1.is_(False),
                                          Message.sender_id != current_user.id),
                                     and_(Conversation.user2_id == current_user.id,
                                          Message.read_by_user2.is_(False),
                                          Message.sender_id != current_user.id),
                                 ))
                                 .count())
    return {
        "current_user": current_user,
        "is_logged_in": current_user.is_authenticated,
        "pending_booking_count": pending_booking_count,
        "booking_updates_count": booking_updates_count,
        "unread_messages_count": unread_messages_count,
        "current_user_profile": current_user_profile,
    }

# --- Auth: signup + email verification ---
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("listings_feed"))
        
    if request.method == "POST":
        if is_rate_limited(f"signup:{request.remote_addr}", 5, 60):
            flash("Too many signups. Try again in a minute.", "danger")
            return redirect(url_for("signup"))
        username = (request.form.get("username") or "").strip()
        if username.startswith("@"):
            username = username[1:]
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")


        if not USERNAME_RE.match(username):
            flash("Username must be 3-20 characters and only letters, numbers, or underscores.", "danger")
            return render_template("signup.html", username=username)

        if not email or not EMAIL_RE.match(email):
            flash("Please enter a valid email address.", "danger")
            return render_template("signup.html", username=username, email=email)

        if not password or len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            flash("Password must be at least 8 characters and include a letter and a number.", "danger")
            return render_template("signup.html", username=username, email=email)
        
        if Users.query.filter(func.lower(Users.username) == username.lower()).first():
            flash("Username already taken. Please choose another.", "danger")
            return render_template("signup.html", username=username, email=email)

        if Users.query.filter(func.lower(Users.email) == email.lower()).first():
            flash("Email already in use. Please use another.", "danger")
            return render_template("signup.html", username=username, email=email)

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("signup.html", username=username, email=email)

        hashed_password = generate_password_hash(password, method='scrypt')
        
        new_user = Users(username=username, email=email, password=hashed_password, email_verified=False)
        db.session.add(new_user)
        db.session.commit()
        if not Profile.query.filter_by(user_id=new_user.id).first():
            db.session.add(Profile(user_id=new_user.id))
            db.session.commit()

        session["pending_verification_user_id"] = new_user.id
        sent = issue_verification_code(new_user)
        if sent:
            flash("A confirmation code has been sent to your email.", "success")
        else:
            flash("We couldn't send your code yet. Check your email settings or resend.", "warning")
        return redirect(url_for("verify_email"))

    return render_template("signup.html")

def get_pending_verification_user():
    user_id = session.get("pending_verification_user_id")
    if not user_id:
        return None
    return Users.query.get(int(user_id))

@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    if current_user.is_authenticated:
        return redirect(url_for("listings_feed"))
    user = get_pending_verification_user()
    if not user:
        flash("No verification in progress. Please sign up or log in.", "info")
        return redirect(url_for("login"))
    if user.email_verified:
        session.pop("pending_verification_user_id", None)
        flash("Email already verified. Please log in.", "info")
        return redirect(url_for("login"))

    if request.method == "POST":
        code = "".join((request.form.get(f"code_{i}") or "").strip() for i in range(1, 7))
        if len(code) != 6 or not code.isdigit():
            flash("Enter the 6-digit code.", "danger")
            return render_template("verify_email.html", email=user.email)
        if user.email_verification_expires_at and user.email_verification_expires_at < datetime.utcnow():
            flash("That code has expired. Please resend a new one.", "warning")
            return render_template("verify_email.html", email=user.email)
        if not user.email_verification_hash or not check_password_hash(user.email_verification_hash, code):
            flash("Invalid code. Try again.", "danger")
            return render_template("verify_email.html", email=user.email)

        user.email_verified = True
        user.email_verification_hash = None
        user.email_verification_sent_at = None
        user.email_verification_expires_at = None
        db.session.commit()
        session.pop("pending_verification_user_id", None)
        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("verify_email.html", email=user.email)

@app.post("/verify-email/resend")
def resend_verification():
    if current_user.is_authenticated:
        return redirect(url_for("listings_feed"))
    user = get_pending_verification_user()
    if not user:
        flash("No verification in progress. Please sign up or log in.", "info")
        return redirect(url_for("login"))
    if is_rate_limited(f"resend:{user.id}", 3, 600):
        flash("Too many resend attempts. Try again in a few minutes.", "danger")
        return redirect(url_for("verify_email"))
    sent = issue_verification_code(user)
    if sent:
        flash("A new verification code has been sent.", "success")
    else:
        flash("We couldn't send your code yet. Please try again.", "warning")
    return redirect(url_for("verify_email"))

################################################## Helpers ################################################################




# --- Email verification helpers ---
def generate_verification_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"

def send_verification_email(to_email: str, code: str) -> bool:
    from_addr = app.config.get("SMTP_FROM")
    api_key = app.config.get("SENDGRID_API_KEY")
    if api_key and from_addr:
        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": from_addr},
            "subject": "Your DJHub verification code",
            "content": [
                {
                    "type": "text/plain",
                    "value": f"Your DJHub verification code is {code}. It expires in 10 minutes.",
                }
            ],
        }
        try:
            req = urllib.request.Request(
                "https://api.sendgrid.com/v3/mail/send",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return 200 <= resp.status < 300
        except Exception:
            app.logger.exception("Failed to send verification email via SendGrid API.")
            return False

    host = app.config.get("SMTP_HOST")
    if not host or not from_addr:
        app.logger.warning("SMTP not configured; skipping verification email.")
        return False
    msg = EmailMessage()
    msg["Subject"] = "Your DJHub verification code"
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.set_content(
        "Your DJHub verification code is "
        f"{code}. It expires in 10 minutes."
    )
    try:
        with smtplib.SMTP(host, app.config["SMTP_PORT"], timeout=6) as server:
            if app.config.get("SMTP_USE_TLS", True):
                server.starttls()
            if app.config.get("SMTP_USERNAME") and app.config.get("SMTP_PASSWORD"):
                server.login(app.config["SMTP_USERNAME"], app.config["SMTP_PASSWORD"])
            server.send_message(msg)
        return True
    except Exception:
        app.logger.exception("Failed to send verification email.")
        return False

def issue_verification_code(user: Users) -> bool:
    code = generate_verification_code()
    user.email_verification_hash = generate_password_hash(code)
    user.email_verification_sent_at = datetime.utcnow()
    user.email_verification_expires_at = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()
    return send_verification_email(user.email, code)

# --- Helpers: normalization ---
def normalize_instagram_url(raw_value: str) -> str | None:
    if not raw_value:
        return None
    value = raw_value.strip()
    if not value:
        return None

    if value.startswith("@"):
        value = value[1:]

    if "://" not in value:
        if value.startswith("instagram.com") or value.startswith("www.instagram.com") or value.startswith("m.instagram.com"):
            value = f"https://{value}"
        elif "/" not in value:
            value = f"https://www.instagram.com/{value}"

    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        return None

    host = parsed.netloc.lower()
    if host not in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        return None

    path = parsed.path.strip("/")
    if not path:
        return None

    segments = [seg for seg in path.split("/") if seg]
    if len(segments) != 1:
        return None

    username = segments[0]
    if not INSTAGRAM_USERNAME_RE.match(username):
        return None

    return f"https://www.instagram.com/{username}"

def normalize_spotify_url(raw_value: str) -> str | None:
    if not raw_value:
        return None
    value = raw_value.strip()
    if not value:
        return None

    if "://" not in value:
        if value.startswith("open.spotify.com") or value.startswith("www.spotify.com") or value.startswith("spotify.com"):
            value = f"https://{value}"

    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        return None

    host = parsed.netloc.lower()
    if host not in {"open.spotify.com", "spotify.com", "www.spotify.com"}:
        return None

    path = parsed.path.strip("/")
    if not path:
        return None

    segments = [seg for seg in path.split("/") if seg]
    if len(segments) != 2:
        return None

    kind, identifier = segments
    if kind not in {"artist", "user", "playlist"}:
        return None
    if not SPOTIFY_ID_RE.match(identifier):
        return None

    return f"https://open.spotify.com/{kind}/{identifier}"

# --- Helpers: uploads ---
def get_upload_size(file_storage) -> int:
    try:
        file_storage.stream.seek(0, os.SEEK_END)
        size = file_storage.stream.tell()
        file_storage.stream.seek(0)
        return size
    except Exception:
        return 0


#######################################################################################################

# --- Static pages ---
@app.route("/about", endpoint="about")
def about():
    return render_template("about.html")

# --- Reviews ---
@app.post("/profiles/<int:profile_id>/review", endpoint="profile_review")
@login_required
def profile_review(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    if current_user.id == profile.user_id:
        flash("You can’t review your own profile.", "danger")
        return redirect(url_for("profile_detail", profile_id=profile.id))

    has_booking = (BookingRequest.query
                   .join(Listing, BookingRequest.listing_id == Listing.id)
                   .join(Profile, Listing.profile_id == Profile.id)
                   .filter(
                       BookingRequest.requester_id == profile.user_id,
                       BookingRequest.status == "accepted",
                       Profile.user_id == current_user.id
                   )
                   .first() is not None)
    if not has_booking:
        flash("Only planners who booked this DJ can leave a review.", "danger")
        return redirect(url_for("profile_detail", profile_id=profile.id))

    rating_raw = (request.form.get("rating") or "").strip()
    try:
        rating = int(rating_raw)
    except ValueError:
        rating = 0
    if rating < 1 or rating > 5:
        flash("Select a star rating from 1 to 5.", "danger")
        return redirect(url_for("profile_detail", profile_id=profile.id))

    review = Review.query.filter_by(profile_id=profile.id, reviewer_id=current_user.id).first()
    if review:
        review.rating = rating
    else:
        review = Review(profile_id=profile.id, reviewer_id=current_user.id, rating=rating)
        db.session.add(review)
    db.session.commit()

    flash("Thanks for reviewing!", "success")
    return redirect(url_for("profile_detail", profile_id=profile.id))


##################################################################################################################

# --- Register external endpoint modules ---
import listingfeed
import profile as profile_routes
import bookingrequests as booking_request_routes

listingfeed.register(app)
profile_routes.register(app)
booking_request_routes.register(app)

# --- Run app ---

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if os.environ.get("SEED_DATA") == "1":
            seed_campus_data()
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
