import os
import re
import uuid
import secrets
import smtplib
from email.message import EmailMessage
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
from sqlalchemy.exc import IntegrityError
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
 

# --- Flask App Initialization ---
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or os.urandom(32)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///djhub.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join("static", "uploads")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"
app.config["SMTP_HOST"] = os.environ.get("SMTP_HOST")
app.config["SMTP_PORT"] = int(os.environ.get("SMTP_PORT", "587"))
app.config["SMTP_USERNAME"] = os.environ.get("SMTP_USERNAME")
app.config["SMTP_PASSWORD"] = os.environ.get("SMTP_PASSWORD")
app.config["SMTP_USE_TLS"] = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
app.config["SMTP_FROM"] = os.environ.get("SMTP_FROM")

db.init_app(app)
csrf = CSRFProtect(app)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
INSTAGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
SPOTIFY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_MESSAGE_LENGTH = 1000
rate_limit_store = defaultdict(deque)

@app.errorhandler(413)
def request_entity_too_large(error):
    flash("File too large. Max upload size is 10MB.", "danger")
    return redirect(request.referrer or url_for("my_profile"))

def is_rate_limited(key: str, limit: int, window_seconds: int) -> bool:
    now = time()
    bucket = rate_limit_store[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False

def is_safe_redirect(target: str) -> bool:
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc

def generate_verification_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"

def send_verification_email(to_email: str, code: str) -> bool:
    host = app.config.get("SMTP_HOST")
    from_addr = app.config.get("SMTP_FROM")
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
        with smtplib.SMTP(host, app.config["SMTP_PORT"], timeout=10) as server:
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

def get_upload_size(file_storage) -> int:
    try:
        file_storage.stream.seek(0, os.SEEK_END)
        size = file_storage.stream.tell()
        file_storage.stream.seek(0)
        return size
    except Exception:
        return 0




# --- Flask-Login Initialization ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"

@app.get("/l/<int:listing_id>", endpoint="listing_detail")
def listing_detail(listing_id):
    listing = Listing.query.get_or_404(listing_id)
    accepted_request = BookingRequest.query.filter_by(
        listing_id=listing.id,
        status="accepted"
    ).first()
    if listing.is_archived:
        if not current_user.is_authenticated:
            abort(404)
        is_owner = listing.profile and current_user.id == listing.profile.user_id
        is_booked_dj = accepted_request and accepted_request.requester_id == current_user.id
        if not (is_owner or is_booked_dj):
            abort(404)
    if current_user.is_authenticated:
        ListingNotification.query.filter_by(
            listing_id=listing.id,
            recipient_id=current_user.id,
            is_read=False
        ).update({"is_read": True})
        db.session.commit()
    booking_requests = []
    current_request = None
    requester_profiles = {}
    if current_user.is_authenticated and listing.profile:
        if current_user.id == listing.profile.user_id:
            request_query = BookingRequest.query.filter_by(listing_id=listing.id)
            if listing.is_archived:
                request_query = request_query.filter_by(status="accepted")
            booking_requests = (request_query
                                .order_by(BookingRequest.created_at.desc())
                                .all())
            if booking_requests:
                requester_ids = [req.requester_id for req in booking_requests]
                profiles = Profile.query.filter(Profile.user_id.in_(requester_ids)).all()
                requester_profiles = {p.user_id: p for p in profiles}
        else:
            if listing.is_archived:
                current_request = accepted_request
            else:
                current_request = BookingRequest.query.filter_by(
                    listing_id=listing.id,
                    requester_id=current_user.id
                ).first()
    return render_template(
        "listing_detail.html",
        listing=listing,
        booking_requests=booking_requests,
        current_request=current_request,
        accepted_request=accepted_request,
        requester_profiles=requester_profiles,
    )

def listings_pagination_from_request(per_page=10): #pagination helper
    page = request.args.get("page", 1, type=int)

    keyword = (request.args.get("keyword") or "").strip()[:100]
    genre = (request.args.get("genre") or "").strip()[:100]
    location = (request.args.get("location") or "").strip()[:100]
    sort = (request.args.get("sort") or "").strip().lower()

    q = Listing.query.filter(Listing.is_archived.is_(False))

    if keyword:
        like = f"%{keyword.lower()}%"
        q = q.filter(or_(
            func.lower(Listing.title).like(like),
            func.lower(Listing.description).like(like),
        ))

    if genre:
        q = q.filter(func.lower(Listing.genres) == genre.lower())

    if location:
        q = q.filter(func.lower(Listing.city) == location.lower())

    if sort == "price_asc":
        q = q.order_by(Listing.budget.asc().nullslast(), Listing.created_at.desc())
    elif sort == "price_desc":
        q = q.order_by(Listing.budget.desc().nullslast(), Listing.created_at.desc())
    elif sort == "oldest":
        q = q.order_by(Listing.created_at.asc())
    else:
        q = q.order_by(Listing.created_at.desc())

    return q.paginate(page=page, per_page=per_page, error_out=False)

@app.get("/api/listings")
def api_listings():
    pagination = listings_pagination_from_request(per_page=10)

    return jsonify({
        "listings": [
            {
                "id": l.id,
                "title": l.title,
                "city": l.city,
                "date": l.date.isoformat() if l.date else None,
                "time": l.time,
                "budget": l.budget,
                "genres": l.genres,
                "description": l.description,
                "cover_image_url": l.cover_image_url,
            }
            for l in pagination.items
        ],
        "has_next": pagination.has_next,
        "next_page": pagination.next_num if pagination.has_next else None,
    })

@app.route("/feed", endpoint="listings_feed")
def listings_feed(): #display listing feed
    pagination = listings_pagination_from_request(per_page=10)
    genres = Genre.query.order_by(Genre.name.asc()).all()
    locations = Location.query.order_by(Location.name.asc()).all()

    return render_template(
        "index.html",
        listings=pagination.items,
        has_next=pagination.has_next,
        next_page=pagination.next_num if pagination.has_next else None,
        genres=genres,
        locations=locations,
    )
# --- Landing Page (before campus selection) ---
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

# --- Flask-Login User Loader ---



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



# --- AUTHENTICATION ROUTES (Modified login route) ---

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







# --- MAIN APP ROUTES ---




@app.route("/listings", endpoint="home") # Redirects '/' to 'select_campus', so /listings can be accessed directly if user continues without signing in
def listings_redirect():
    return redirect(url_for('listings_feed'))

@app.route("/listings/new", methods=["GET", "POST"], endpoint="create_listing")
@login_required
def create_listing():
    genres = Genre.query.order_by(Genre.name.asc()).all()
    locations = Location.query.order_by(Location.name.asc()).all()

    profile = Profile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        flash("Create a profile before posting a listing.", "danger")
        return redirect(url_for("listings_feed"))

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        budget_raw = (request.form.get("budget") or "").strip()
        genre = (request.form.get("genre") or "").strip()
        location = (request.form.get("location") or "").strip()
        time_value = (request.form.get("time") or "").strip()
        flyer_file = request.files.get("flyer_file")
        flyer_file = request.files.get("flyer_file")
        flyer_url = None
        photos = request.files.getlist("photos")

        if not title:
            flash("Title is required.", "danger")
            return render_template(
                "create_listing.html",
                genres=genres,
                locations=locations,
                form=request.form,
            )

        budget = None
        if budget_raw:
            if not budget_raw.isdigit():
                flash("Budget must be a number.", "danger")
                return render_template(
                    "create_listing.html",
                    genres=genres,
                    locations=locations,
                    form=request.form,
                )
            budget = int(budget_raw)

        if genre and not Genre.query.filter_by(name=genre).first():
            flash("Select a valid genre.", "danger")
            return render_template(
                "create_listing.html",
                genres=genres,
                locations=locations,
                form=request.form,
            )

        if location and not Location.query.filter_by(name=location).first():
            flash("Select a valid location.", "danger")
            return render_template(
                "create_listing.html",
                genres=genres,
                locations=locations,
                form=request.form,
            )

        valid_photos = [p for p in photos if p and p.filename]
        if len(valid_photos) > 5:
            flash("You can upload up to 5 photos.", "danger")
            return render_template(
                "create_listing.html",
                genres=genres,
                locations=locations,
                form=request.form,
                is_edit=False,
                action_url=url_for("create_listing"),
                submit_label="Post listing",
                page_title="Create a listing",
                page_subtitle="Post a gig to reach verified DJs fast.",
            )

        if flyer_file and flyer_file.filename:
            filename = secure_filename(flyer_file.filename)
            ext = os.path.splitext(filename)[1].lower()
            if ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                flash("Flyer must be an image file.", "danger")
                return render_template(
                    "create_listing.html",
                    genres=genres,
                    locations=locations,
                    form=request.form,
                )
            if get_upload_size(flyer_file) == 0:
                flash("Flyer upload appears empty. Please reselect the file.", "danger")
                return render_template(
                    "create_listing.html",
                    genres=genres,
                    locations=locations,
                    form=request.form,
                )
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            unique_name = f"{uuid.uuid4().hex}{ext}"
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
            flyer_file.save(save_path)
            flyer_url = f"/{save_path}"

        flyer_url = None
        if flyer_file and flyer_file.filename:
            filename = secure_filename(flyer_file.filename)
            ext = os.path.splitext(filename)[1].lower()
            if ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                flash("Flyer must be an image file.", "danger")
                return render_template(
                    "create_listing.html",
                    genres=genres,
                    locations=locations,
                    form=request.form,
                    is_edit=True,
                    action_url=url_for("edit_listing", listing_id=listing.id),
                    submit_label="Save changes",
                    page_title="Edit listing",
                    page_subtitle="Update details for your gig.",
                )
            if get_upload_size(flyer_file) == 0:
                flash("Flyer upload appears empty. Please reselect the file.", "danger")
                return render_template(
                    "create_listing.html",
                    genres=genres,
                    locations=locations,
                    form=request.form,
                    is_edit=True,
                    action_url=url_for("edit_listing", listing_id=listing.id),
                    submit_label="Save changes",
                    page_title="Edit listing",
                    page_subtitle="Update details for your gig.",
                )
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            unique_name = f"{uuid.uuid4().hex}{ext}"
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
            flyer_file.save(save_path)
            flyer_url = f"/{save_path}"

        saved_photos = []
        if valid_photos:
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            for photo in valid_photos:
                filename = secure_filename(photo.filename)
                ext = os.path.splitext(filename)[1].lower()
                if ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                    flash("Photos must be image files.", "danger")
                    return render_template(
                        "create_listing.html",
                        genres=genres,
                        locations=locations,
                        form=request.form,
                    )
                if get_upload_size(photo) == 0:
                    flash("One of the photos appears empty. Please reselect the file.", "danger")
                    return render_template(
                        "create_listing.html",
                        genres=genres,
                        locations=locations,
                        form=request.form,
                    )
                unique_name = f"{uuid.uuid4().hex}{ext}"
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                photo.save(save_path)
                saved_photos.append(f"/{save_path}")

        listing = Listing(
            title=title,
            description=description or None,
            budget=budget,
            genres=genre or None,
            city=location or None,
            time=time_value or None,
            profile_id=profile.id,
            cover_image_url=flyer_url or (saved_photos[0] if saved_photos else None),
        )
        db.session.add(listing)
        db.session.commit()

        if flyer_url:
            db.session.add(ListingPhoto(
                listing_id=listing.id,
                image_url=flyer_url,
                is_cover=True,
            ))
        if saved_photos:
            for idx, url in enumerate(saved_photos):
                db.session.add(ListingPhoto(
                    listing_id=listing.id,
                    image_url=url,
                    is_cover=(not flyer_url and idx == 0),
                ))
        db.session.commit()

        flash("Listing created.", "success")
        return redirect(url_for("listing_detail", listing_id=listing.id))

    return render_template(
        "create_listing.html",
        genres=genres,
        locations=locations,
        form={},
        is_edit=False,
        action_url=url_for("create_listing"),
        submit_label="Post listing",
        page_title="Create a listing",
        page_subtitle="Post a gig to reach verified DJs fast.",
    )

@app.route("/listings/<int:listing_id>/edit", methods=["GET", "POST"], endpoint="edit_listing")
@login_required
def edit_listing(listing_id):
    listing = Listing.query.get_or_404(listing_id)
    if not listing.profile or listing.profile.user_id != current_user.id:
        abort(403)

    genres = Genre.query.order_by(Genre.name.asc()).all()
    locations = Location.query.order_by(Location.name.asc()).all()

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        budget_raw = (request.form.get("budget") or "").strip()
        genre = (request.form.get("genre") or "").strip()
        location = (request.form.get("location") or "").strip()
        time_value = (request.form.get("time") or "").strip()
        photos = request.files.getlist("photos")

        if not title:
            flash("Title is required.", "danger")
            return render_template(
                "create_listing.html",
                genres=genres,
                locations=locations,
                form=request.form,
                is_edit=True,
                action_url=url_for("edit_listing", listing_id=listing.id),
                submit_label="Save changes",
                page_title="Edit listing",
                page_subtitle="Update details for your gig.",
            )

        budget = None
        if budget_raw:
            if not budget_raw.isdigit():
                flash("Budget must be a number.", "danger")
                return render_template(
                    "create_listing.html",
                    genres=genres,
                    locations=locations,
                    form=request.form,
                    is_edit=True,
                    action_url=url_for("edit_listing", listing_id=listing.id),
                    submit_label="Save changes",
                    page_title="Edit listing",
                    page_subtitle="Update details for your gig.",
                )
            budget = int(budget_raw)

        if genre and not Genre.query.filter_by(name=genre).first():
            flash("Select a valid genre.", "danger")
            return render_template(
                "create_listing.html",
                genres=genres,
                locations=locations,
                form=request.form,
                is_edit=True,
                action_url=url_for("edit_listing", listing_id=listing.id),
                submit_label="Save changes",
                page_title="Edit listing",
                page_subtitle="Update details for your gig.",
            )

        if location and not Location.query.filter_by(name=location).first():
            flash("Select a valid location.", "danger")
            return render_template(
                "create_listing.html",
                genres=genres,
                locations=locations,
                form=request.form,
                is_edit=True,
                action_url=url_for("edit_listing", listing_id=listing.id),
                submit_label="Save changes",
                page_title="Edit listing",
                page_subtitle="Update details for your gig.",
            )

        valid_photos = [p for p in photos if p and p.filename]
        if len(valid_photos) > 5:
            flash("You can upload up to 5 photos.", "danger")
            return render_template(
                "create_listing.html",
                genres=genres,
                locations=locations,
                form=request.form,
                is_edit=True,
                action_url=url_for("edit_listing", listing_id=listing.id),
                submit_label="Save changes",
                page_title="Edit listing",
                page_subtitle="Update details for your gig.",
            )

        saved_photos = []
        if valid_photos:
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            for photo in valid_photos:
                filename = secure_filename(photo.filename)
                ext = os.path.splitext(filename)[1].lower()
                if ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                    flash("Photos must be image files.", "danger")
                    return render_template(
                        "create_listing.html",
                        genres=genres,
                        locations=locations,
                        form=request.form,
                        is_edit=True,
                        action_url=url_for("edit_listing", listing_id=listing.id),
                        submit_label="Save changes",
                        page_title="Edit listing",
                        page_subtitle="Update details for your gig.",
                    )
                if get_upload_size(photo) == 0:
                    flash("One of the photos appears empty. Please reselect the file.", "danger")
                    return render_template(
                        "create_listing.html",
                        genres=genres,
                        locations=locations,
                        form=request.form,
                        is_edit=True,
                        action_url=url_for("edit_listing", listing_id=listing.id),
                        submit_label="Save changes",
                        page_title="Edit listing",
                        page_subtitle="Update details for your gig.",
                    )
                unique_name = f"{uuid.uuid4().hex}{ext}"
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                photo.save(save_path)
                saved_photos.append(f"/{save_path}")

        listing.title = title
        listing.description = description or None
        listing.budget = budget
        listing.genres = genre or None
        listing.city = location or None
        listing.time = time_value or None
        if flyer_url:
            listing.cover_image_url = flyer_url
        elif saved_photos:
            listing.cover_image_url = saved_photos[0]
        db.session.commit()

        if flyer_url:
            db.session.add(ListingPhoto(
                listing_id=listing.id,
                image_url=flyer_url,
                is_cover=True,
            ))
        if saved_photos:
            for idx, url in enumerate(saved_photos):
                db.session.add(ListingPhoto(
                    listing_id=listing.id,
                    image_url=url,
                    is_cover=(not flyer_url and idx == 0),
                ))
            db.session.commit()

        flash("Listing updated.", "success")
        return redirect(url_for("listing_detail", listing_id=listing.id))

    form = {
        "title": listing.title,
        "description": listing.description or "",
        "budget": listing.budget or "",
        "genre": listing.genres or "",
        "location": listing.city or "",
        "time": listing.time or "",
    }
    return render_template(
        "create_listing.html",
        genres=genres,
        locations=locations,
        form=form,
        is_edit=True,
        action_url=url_for("edit_listing", listing_id=listing.id),
        submit_label="Save changes",
        page_title="Edit listing",
        page_subtitle="Update details for your gig.",
    )

@app.get("/my-listings", endpoint="my_listings")
@login_required
def my_listings():
    listings = (Listing.query
                .join(Profile, Listing.profile_id == Profile.id)
                .filter(Profile.user_id == current_user.id, Listing.is_archived.is_(False))
                .order_by(Listing.created_at.desc())
                .all())
    upcoming_requests = (BookingRequest.query
                         .join(Listing, BookingRequest.listing_id == Listing.id)
                         .join(Profile, Listing.profile_id == Profile.id)
                         .filter(Profile.user_id == current_user.id, BookingRequest.status == "accepted")
                         .order_by(Listing.created_at.desc())
                         .all())
    requester_profiles = {}
    if upcoming_requests:
        requester_ids = {req.requester_id for req in upcoming_requests}
        if requester_ids:
            profiles = (Profile.query
                        .filter(Profile.user_id.in_(requester_ids))
                        .all())
            requester_profiles = {p.user_id: p for p in profiles}
    notifications = (ListingNotification.query
                     .join(Listing, ListingNotification.listing_id == Listing.id)
                     .join(Profile, Listing.profile_id == Profile.id)
                     .filter(ListingNotification.recipient_id == current_user.id,
                             Profile.user_id == current_user.id)
                     .order_by(ListingNotification.created_at.desc())
                     .all())
    notifications_by_listing = {}
    unread_counts = {}
    for note in notifications:
        notifications_by_listing.setdefault(note.listing_id, []).append(note)
        if not note.is_read:
            unread_counts[note.listing_id] = unread_counts.get(note.listing_id, 0) + 1
    return render_template(
        "my_listings.html",
        listings=listings,
        upcoming_requests=upcoming_requests,
        requester_profiles=requester_profiles,
        notifications_by_listing=notifications_by_listing,
        unread_counts=unread_counts,
    )

@app.get("/my-bookings", endpoint="my_bookings")
@login_required
def my_bookings():
    bookings = (BookingRequest.query
                .join(Listing, BookingRequest.listing_id == Listing.id)
                .filter(BookingRequest.requester_id == current_user.id,
                        BookingRequest.status == "accepted")
                .order_by(Listing.created_at.desc())
                .all())
    notifications = (ListingNotification.query
                     .join(Listing, ListingNotification.listing_id == Listing.id)
                     .join(Profile, Listing.profile_id == Profile.id)
                     .filter(ListingNotification.recipient_id == current_user.id,
                             Profile.user_id != current_user.id)
                     .order_by(ListingNotification.created_at.desc())
                     .all())
    unread_counts = {}
    for note in notifications:
        if not note.is_read:
            unread_counts[note.listing_id] = unread_counts.get(note.listing_id, 0) + 1
    return render_template(
        "my_bookings.html",
        bookings=bookings,
        unread_counts=unread_counts,
    )

# --- Data Seeding ---
def seed_campus_data():
    if not Campus.query.filter_by(slug='ucsc').first():
        ucsc = Campus(name='UC Santa Cruz', slug='ucsc')
        db.session.add(ucsc)
        db.session.commit()
        print("Seeded UC Santa Cruz campus.")

@app.route("/profiles/search", endpoint="profile_search")
def profile_search():
    genres = Genre.query.order_by(Genre.name.asc()).all()
    return render_template("profile_search.html", genres=genres)

@app.route("/my-profile", methods=["GET", "POST"], endpoint="my_profile")
@login_required
def my_profile():
    profile = Profile.query.filter_by(user_id=current_user.id).first()
    genres = Genre.query.order_by(Genre.name.asc()).all()
    locations = Location.query.order_by(Location.name.asc()).all()
    tracks = []
    track_map = {}
    if profile:
        tracks = (ProfileTrack.query
                  .filter_by(profile_id=profile.id)
                  .order_by(ProfileTrack.position.asc())
                  .all())
        track_map = {t.position: t for t in tracks}
    if request.method == "POST":
        city = (request.form.get("city") or "").strip()
        profile_type = (request.form.get("profile_type") or "").strip().lower()
        genres_value = (request.form.get("genres") or "").strip()
        bio = (request.form.get("bio") or "").strip()
        instagram_url_raw = (request.form.get("instagram_url") or "").strip()
        spotify_url_raw = (request.form.get("spotify_url") or "").strip()
        avatar_file = request.files.get("avatar_file")
        track_files = [
            request.files.get("track_file_1"),
            request.files.get("track_file_2"),
            request.files.get("track_file_3"),
        ]
        track_titles = [
            (request.form.get("track_title_1") or "").strip(),
            (request.form.get("track_title_2") or "").strip(),
            (request.form.get("track_title_3") or "").strip(),
        ]

        if not profile:
            profile = Profile(user_id=current_user.id)
            db.session.add(profile)

        if city and not Location.query.filter_by(name=city).first():
            flash("Select a valid location.", "danger")
            return render_template("my_profile.html", profile=profile, form=request.form, genres=genres, locations=locations, tracks=tracks, track_map=track_map)
        if genres_value and not Genre.query.filter_by(name=genres_value).first():
            flash("Select a valid genre.", "danger")
            return render_template("my_profile.html", profile=profile, form=request.form, genres=genres, locations=locations, tracks=tracks, track_map=track_map)

        instagram_url = normalize_instagram_url(instagram_url_raw)
        if instagram_url_raw and not instagram_url:
            flash("Enter a valid Instagram profile URL (e.g. https://instagram.com/yourname).", "danger")
            return render_template("my_profile.html", profile=profile, form=request.form, genres=genres, locations=locations, tracks=tracks, track_map=track_map)

        spotify_url = normalize_spotify_url(spotify_url_raw)
        if spotify_url_raw and not spotify_url:
            flash("Enter a valid Spotify URL (artist, user, or playlist).", "danger")
            return render_template("my_profile.html", profile=profile, form=request.form, genres=genres, locations=locations, tracks=tracks, track_map=track_map)

        if profile_type not in {"dj", "planner"}:
            flash("Select a valid profile type.", "danger")
            return render_template("my_profile.html", profile=profile, form=request.form, genres=genres, locations=locations, tracks=tracks)

        profile.profile_type = profile_type
        profile.city = city or None
        profile.genres = genres_value or None
        profile.bio = bio or None
        profile.instagram_url = instagram_url or None
        profile.spotify_url = spotify_url or None
        if avatar_file and avatar_file.filename:
            filename = secure_filename(avatar_file.filename)
            ext = os.path.splitext(filename)[1].lower()
            if ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                flash("Avatar must be an image file.", "danger")
                return render_template("my_profile.html", profile=profile, form=request.form, genres=genres, locations=locations, tracks=tracks, track_map=track_map)
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            unique_name = f"{uuid.uuid4().hex}{ext}"
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
            avatar_file.save(save_path)
            profile.avatar_url = f"/{save_path}"

        uploaded_tracks = []
        for idx, f in enumerate(track_files):
            if f and f.filename:
                uploaded_tracks.append((idx + 1, f))
        if uploaded_tracks:
            allowed_audio_exts = {".mp3", ".m4a", ".wav", ".ogg", ".aac"}
            for position, f in uploaded_tracks:
                if get_upload_size(f) > 10 * 1024 * 1024:
                    flash(f"Track line {position} exceeds 10MB.", "danger")
                    return render_template("my_profile.html", profile=profile, form=request.form, genres=genres, locations=locations, tracks=tracks, track_map=track_map)
                ext = os.path.splitext(secure_filename(f.filename))[1].lower()
                if ext not in allowed_audio_exts:
                    flash("Tracks must be audio files (mp3, m4a, wav, ogg, aac).", "danger")
                    return render_template("my_profile.html", profile=profile, form=request.form, genres=genres, locations=locations, tracks=tracks, track_map=track_map)

            ProfileTrack.query.filter_by(profile_id=profile.id).delete()
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            new_tracks = []
            for position, f in uploaded_tracks:
                filename = secure_filename(f.filename)
                ext = os.path.splitext(filename)[1].lower()
                unique_name = f"{uuid.uuid4().hex}{ext}"
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                f.save(save_path)
                title_input = track_titles[position - 1]
                title = title_input or os.path.splitext(filename)[0].replace("_", " ").replace("-", " ").strip() or f"Track {position}"
                new_tracks.append(ProfileTrack(
                    profile_id=profile.id,
                    title=title,
                    audio_url=f"/{save_path}",
                    position=position,
                ))
            db.session.add_all(new_tracks)
        else:
            # Allow title-only updates without re-uploading files
            existing_tracks = {t.position: t for t in tracks}
            updated = False
            for position in (1, 2, 3):
                title_input = track_titles[position - 1]
                if title_input and existing_tracks.get(position):
                    existing_tracks[position].title = title_input
                    updated = True
            if updated:
                db.session.add_all(list(existing_tracks.values()))
        db.session.commit()

        flash("Profile saved.", "success")
        return redirect(url_for("my_profile"))

    return render_template("my_profile.html", profile=profile, form={}, genres=genres, locations=locations, tracks=tracks, track_map=track_map)

@app.route("/about", endpoint="about")
def about():
    return render_template("about.html")


@app.get("/api/profiles/search")
def profile_search_api():
    q = (request.args.get("q") or "").strip()[:100]
    genre = (request.args.get("genre") or "").strip()[:100]
    min_rating_raw = (request.args.get("min_rating") or "").strip()
    page_raw = (request.args.get("page") or "1").strip()
    try:
        page = max(1, int(page_raw))
    except ValueError:
        page = 1
    per_page = 12
    try:
        min_rating = float(min_rating_raw) if min_rating_raw else None
    except ValueError:
        min_rating = None

    like = f"%{q.lower()}%"
    avg_rating = func.coalesce(func.avg(Review.rating), 0).label("avg_rating")
    query = (db.session.query(Profile, avg_rating)
             .join(Profile.user)
             .outerjoin(Review, Review.profile_id == Profile.id)
             .filter(Profile.profile_type == "dj")
             .group_by(Profile.id))

    if q:
        query = query.filter(
            or_(
                func.lower(Users.username).like(like),
                func.lower(Profile.city).like(like),
                func.lower(Profile.genres).like(like),
            )
        )
    if genre:
        query = query.filter(func.lower(Profile.genres).like(f"%{genre.lower()}%"))
    if min_rating is not None:
        query = query.having(avg_rating >= min_rating)

    total = query.count()
    results = (query
               .order_by(Users.username.asc())
               .offset((page - 1) * per_page)
               .limit(per_page)
               .all())

    return jsonify({
        "profiles": [
            {
                "id": p.id,
                "username": p.user.username,
                "city": p.city or "",
                "genres": p.genres or "",
                "avatar_url": p.avatar_url or "/static/default-avatar.png",
                "avg_rating": round(float(avg or 0), 1),
            }
            for p, avg in results
        ],
        "has_next": (page * per_page) < total,
        "next_page": page + 1 if (page * per_page) < total else None,
    })

@app.get("/p/<int:profile_id>", endpoint="profile_detail")
def profile_detail(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    instagram_url = normalize_instagram_url(profile.instagram_url or "")
    spotify_url = normalize_spotify_url(profile.spotify_url or "")
    tracks = (ProfileTrack.query
              .filter_by(profile_id=profile.id)
              .order_by(ProfileTrack.position.asc())
              .all())
    avg_rating = (db.session.query(func.avg(Review.rating))
                  .filter(Review.profile_id == profile.id)
                  .scalar())
    review_count = Review.query.filter_by(profile_id=profile.id).count()
    avg_rating = float(avg_rating) if avg_rating is not None else 0.0

    existing_review = None
    can_review = False
    if current_user.is_authenticated and current_user.id != profile.user_id:
        existing_review = Review.query.filter_by(
            profile_id=profile.id,
            reviewer_id=current_user.id
        ).first()
        has_booking = (BookingRequest.query
                       .join(Listing, BookingRequest.listing_id == Listing.id)
                       .join(Profile, Listing.profile_id == Profile.id)
                       .filter(
                           BookingRequest.requester_id == profile.user_id,
                           BookingRequest.status == "accepted",
                           Profile.user_id == current_user.id
                       )
                       .first() is not None)
        can_review = has_booking

    return render_template(
        "profile_detail.html",
        profile=profile,
        instagram_url=instagram_url,
        spotify_url=spotify_url,
        tracks=tracks,
        avg_rating=avg_rating,
        review_count=review_count,
        can_review=can_review,
        existing_review=existing_review,
    )





@app.get("/messages/need-account", endpoint="go_signup_for_messages")
def go_signup_for_messages():
    if current_user.is_authenticated:
        return redirect(url_for("inbox"))
    flash("Create an account to start messaging.", "info")
    return redirect(url_for("signup"))

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

@app.get("/messages", endpoint="inbox")
@login_required
def inbox():
    convos = (Conversation.query
              .filter(or_(Conversation.user1_id == current_user.id,
                          Conversation.user2_id == current_user.id))
              .order_by(Conversation.last_message_at.desc())
              .all())

    items = []
    for c in convos:
        other_id = c.user2_id if c.user1_id == current_user.id else c.user1_id
        other_user = Users.query.get(other_id)
        other_profile = Profile.query.filter_by(user_id=other_id).first()

        last_msg = (Message.query
                    .filter_by(conversation_id=c.id)
                    .order_by(Message.created_at.desc())
                    .first())

        # unread count for current user
        if c.user1_id == current_user.id:
            unread_count = (Message.query
                            .filter_by(conversation_id=c.id, read_by_user1=False)
                            .filter(Message.sender_id != current_user.id)
                            .count())
        else:
            unread_count = (Message.query
                            .filter_by(conversation_id=c.id, read_by_user2=False)
                            .filter(Message.sender_id != current_user.id)
                            .count())

        items.append({
            "convo": c,
            "other_name": (other_user.username if other_user else "User"),
            "other_username": (other_user.username if other_user else "user"),
            "avatar": (other_profile.avatar_url if other_profile and other_profile.avatar_url else "/static/default-avatar.png"),
            "last_text": (last_msg.body if last_msg else ""),
            "last_at": (last_msg.created_at if last_msg else c.last_message_at),
            "unread_count": unread_count,
        })

    return render_template("inbox.html", items=items)


def is_participant(convo: Conversation, user_id: int) -> bool:
    return user_id in (convo.user1_id, convo.user2_id)

def sender_is_user1(convo: Conversation, sender_id: int) -> bool:
    return convo.user1_id == sender_id

def make_message(convo: Conversation, sender_id: int, body: str) -> Message:
    """Create a message with correct read flags for a 1:1 convo."""
    msg = Message(conversation_id=convo.id, sender_id=sender_id, body=body)

    if sender_is_user1(convo, sender_id):
        msg.read_by_user1 = True
        msg.read_by_user2 = False
    else:
        msg.read_by_user1 = False
        msg.read_by_user2 = True

    convo.last_message_at = datetime.utcnow()
    return msg

def mark_conversation_read(convo: Conversation, user_id: int) -> None:
    """Mark all messages as read for this user in this convo."""
    if sender_is_user1(convo, user_id):
        Message.query.filter_by(conversation_id=convo.id).update({"read_by_user1": True})
    else:
        Message.query.filter_by(conversation_id=convo.id).update({"read_by_user2": True})


def get_or_create_conversation(user_a_id: int, user_b_id: int) -> Conversation:
    if user_a_id == user_b_id:
        raise ValueError("Cannot create conversation with self")

    u1, u2 = sorted((user_a_id, user_b_id))

    convo = Conversation.query.filter_by(user1_id=u1, user2_id=u2).first()
    if convo:
        return convo

    convo = Conversation(user1_id=u1, user2_id=u2)
    db.session.add(convo)

    try:
        db.session.commit()
        return convo
    except IntegrityError:
        db.session.rollback()
        return Conversation.query.filter_by(user1_id=u1, user2_id=u2).first()


@app.post("/listings/<int:listing_id>/request-booking")
@login_required
def request_booking(listing_id):
    listing = Listing.query.get_or_404(listing_id)
    if listing.is_archived:
        flash("Listing is no longer accepting requests.", "info")
        return redirect(url_for("listing_detail", listing_id=listing_id))
    if not listing.profile:
        flash("Listing has no owner.", "danger")
        return redirect(url_for("listing_detail", listing_id=listing_id))

    owner_id = listing.profile.user_id
    if owner_id == current_user.id:
        flash("You cannot request your own listing.", "danger")
        return redirect(url_for("listing_detail", listing_id=listing_id))

    existing = BookingRequest.query.filter_by(
        listing_id=listing.id,
        requester_id=current_user.id
    ).first()
    if existing:
        if existing.status == "pending":
            flash("Booking request already sent.", "info")
        else:
            flash(f"Booking request already {existing.status}.", "info")
        return redirect(url_for("listing_detail", listing_id=listing_id))

    convo = get_or_create_conversation(current_user.id, owner_id)
    db.session.add(BookingRequest(
        listing_id=listing.id,
        requester_id=current_user.id,
        conversation_id=convo.id,
    ))
    db.session.add(ListingNotification(
        listing_id=listing.id,
        recipient_id=owner_id,
        message=f"{current_user.username} would like to book your event.",
    ))
    db.session.commit()

    flash("Booking request sent.", "success")
    return redirect(url_for("listing_detail", listing_id=listing_id))


@app.post("/bookings/<int:booking_id>/respond")
@login_required
def respond_booking(booking_id):
    booking = BookingRequest.query.get_or_404(booking_id)
    listing = Listing.query.get_or_404(booking.listing_id)
    if not listing.profile or current_user.id != listing.profile.user_id:
        abort(403)

    action = (request.form.get("action") or "").strip().lower()
    if action not in ("accept", "decline"):
        abort(400)

    if booking.status != "pending":
        flash("Booking request already handled.", "info")
        return redirect(url_for("listing_detail", listing_id=listing.id))

    if action == "accept":
        booking.status = "accepted"
        listing.is_archived = True
        other_requests = BookingRequest.query.filter(
            BookingRequest.listing_id == listing.id,
            BookingRequest.id != booking.id
        ).all()
        for req in other_requests:
            if req.status == "pending":
                req.status = "declined"
        convo = Conversation.query.get_or_404(booking.conversation_id)
        reply = f"Your booking request for \"{listing.title}\" was {booking.status}."
        msg = make_message(convo, current_user.id, reply)
        db.session.add(msg)
        db.session.add(ListingNotification(
            listing_id=listing.id,
            recipient_id=booking.requester_id,
            message=f"Your booking request for \"{listing.title}\" was accepted.",
        ))
        db.session.commit()

        flash("Booking request accepted. Listing archived.", "success")
        return redirect(url_for("my_listings"))

    booking.status = "declined"
    convo = Conversation.query.get_or_404(booking.conversation_id)
    reply = f"Your booking request for \"{listing.title}\" was {booking.status}."
    msg = make_message(convo, current_user.id, reply)
    db.session.add(msg)
    db.session.commit()

    flash(f"Booking request {booking.status}.", "success")
    return redirect(url_for("listing_detail", listing_id=listing.id))



@app.post("/messages/start")
@login_required
def start_conversation():
    recipient_raw = (request.form.get("recipient_id") or "").strip()
    body = (request.form.get("body") or "").strip()

    if is_rate_limited(f"messages_start:{request.remote_addr}", 15, 60):
        flash("You're sending messages too quickly. Try again in a minute.", "danger")
        return redirect(request.referrer or url_for("inbox"))

    if not recipient_raw.isdigit():
        flash("Invalid recipient.", "danger")
        return redirect(request.referrer or url_for("listings_feed"))

    recipient_id = int(recipient_raw)
    if recipient_id == current_user.id:
        flash("You can’t message yourself.", "danger")
        return redirect(request.referrer or url_for("listings_feed"))

    recipient = Users.query.get_or_404(recipient_id)

    convo = get_or_create_conversation(current_user.id, recipient.id)

    if not body:
        flash("Message cannot be empty.", "danger")
        return redirect(request.referrer or url_for("inbox"))
    if len(body) > MAX_MESSAGE_LENGTH:
        flash("Message is too long.", "danger")
        return redirect(request.referrer or url_for("inbox"))

    msg = make_message(convo, current_user.id, body)
    db.session.add(msg)
    db.session.commit()

    return redirect(url_for("view_conversation", conversation_id=convo.id))


@app.route("/messages/<int:conversation_id>", methods=["GET", "POST"], endpoint="view_conversation")
@login_required
def view_conversation(conversation_id):
    convo = Conversation.query.get_or_404(conversation_id)

    # ✅ Access control: only participants
    if current_user.id not in (convo.user1_id, convo.user2_id):
        abort(403)
    if request.method == "POST":
        body = (request.form.get("body") or "").strip()
        if not body:
            flash("Message cannot be empty.", "danger")
            return redirect(url_for("view_conversation", conversation_id=conversation_id))
        if len(body) > MAX_MESSAGE_LENGTH:
            flash("Message is too long.", "danger")
            return redirect(url_for("view_conversation", conversation_id=conversation_id))
        msg = make_message(convo, current_user.id, body)
        db.session.add(msg)
        db.session.commit()
        return redirect(url_for("view_conversation", conversation_id=conversation_id))
    
    msgs = convo.messages.order_by(Message.created_at.asc()).all()

    other_id = convo.user2_id if convo.user1_id == current_user.id else convo.user1_id
    other_user = Users.query.get(other_id)
    other_profile = Profile.query.filter_by(user_id=other_id).first()
    other_display = (other_user.username if other_user else "User")
    other_avatar = (other_profile.avatar_url if other_profile and other_profile.avatar_url else "/static/default-avatar.png")
    other_username = other_user.username if other_user else "user"

    # mark as read for current user
    if convo.user1_id == current_user.id:
        Message.query.filter_by(conversation_id=convo.id).update({"read_by_user1": True})
    else:
        Message.query.filter_by(conversation_id=convo.id).update({"read_by_user2": True})
    db.session.commit()

    return render_template(
        "conversation.html",
        convo=convo,
        messages=msgs,
        other_display=other_display,
        other_username=other_username,
        other_avatar=other_avatar,
    )


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if os.environ.get("SEED_DATA") == "1":
            seed_campus_data()
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
