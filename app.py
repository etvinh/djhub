
#Libraries
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
from datetime import date, datetime
from sqlalchemy import or_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    current_user,
    login_required
)

#Models
from djhub.models import Listing  
from djhub.models import Profile 
from djhub.models import Campus  
from djhub.models import Users 
from djhub.models import Conversation, Message
from djhub.extensions import db  # db = SQLAlchemy() lives here




# --- Flask App Initialization ---
app = Flask(__name__)
# CRITICAL: Secret key needed for sessions (campus selection) and Flask-Login
app.config["SECRET_KEY"] = "a_super_secret_key_for_sessions" 
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///djhub.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

# --- Flask-Login Initialization ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"

@app.get("/l/<int:listing_id>", endpoint="listing_detail")
def listing_detail(listing_id):
    listing = Listing.query.get_or_404(listing_id)
    return render_template("listing_detail.html", listing=listing)

def listings_pagination_from_request(per_page=10): #pagination helper
    page = request.args.get("page", 1, type=int)

    keyword = (request.args.get("keyword") or "").strip()
    genre = (request.args.get("genre") or "").strip()
    location = (request.args.get("location") or "").strip()
    date_from = parse_date((request.args.get("date_from") or "").strip())

    q = Listing.query

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

    if date_from:
        q = q.filter(Listing.date.isnot(None), Listing.date >= date_from)

    return (q.order_by(Listing.date.asc().nullslast(), Listing.created_at.desc())
             .paginate(page=page, per_page=per_page, error_out=False))

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
                "budget": l.budget,
                "genres": l.genres,
                "description": l.description,
            }
            for l in pagination.items
        ],
        "has_next": pagination.has_next,
        "next_page": pagination.next_num if pagination.has_next else None,
    })

@app.route("/feed", endpoint="listings_feed")
def listings_feed(): #display listing feed
    pagination = listings_pagination_from_request(per_page=10)

    return render_template(
        "index.html",
        listings=pagination.items,
        has_next=pagination.has_next,
        next_page=pagination.next_num if pagination.has_next else None,
    )



from datetime import datetime, date as date_cls
from sqlalchemy import or_, func

from datetime import datetime

def parse_date(s):
   
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None

def apply_listing_filters(query, *, keyword=None, genre=None, location=None, date_from=None, date_to=None):
    if keyword:
        like = f"%{keyword.strip().lower()}%"
        query = query.filter(or_(
            func.lower(Listing.title).like(like),
            func.lower(Listing.description).like(like),
            func.lower(Listing.city).like(like),
            func.lower(Listing.genres).like(like),
        ))

    if genre:
        query = query.filter(Listing.genres == genre)

    if location:
        query = query.filter(Listing.city == location)

    # Only add "date is not null" once, if any date filter exists
    if date_from or date_to:
        query = query.filter(Listing.date.isnot(None))
        if date_from:
            query = query.filter(Listing.date >= date_from)
        if date_to:
            query = query.filter(Listing.date <= date_to)

    return query

# --- NEW: Campus Selection Route (Root) ---
@app.route("/", methods=["GET", "POST"], endpoint="select_campus")
def select_campus():
    # If the campus is already in the session, skip this screen and go to login/gateway
    if session.get('campus_slug'):
        return redirect(url_for("login")) 

    campuses = Campus.query.filter_by(is_active=True).all()
    
    if request.method == "POST":
        selected_slug = request.form.get("campus_slug")
        campus = Campus.query.filter_by(slug=selected_slug).first()

        if campus:
            session['campus_slug'] = campus.slug
            flash(f"Campus set to {campus.name}.", "success")
            return redirect(url_for("login")) # Redirects to the combined gateway
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
        username = request.form.get("username").strip()
        password = request.form.get("password")

        user = Users.query.filter(func.lower(Users.username) == username.lower()).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            flash(f"Welcome back, {user.username}!", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("listings_feed"))
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
    return {
        "current_user": current_user,
        "is_logged_in": current_user.is_authenticated
    }



# --- AUTHENTICATION ROUTES (Modified login route) ---

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("listings_feed"))
        
    if request.method == "POST":
        username = request.form.get("username").strip()
        password = request.form.get("password")
        
        if Users.query.filter(func.lower(Users.username) == username.lower()).first():
            flash("Username already taken. Please choose another.", "danger")
            return render_template("signup.html", username=username)

        hashed_password = generate_password_hash(password, method='scrypt')
        
        new_user = Users(username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit() 
        
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")







# --- MAIN APP ROUTES ---




@app.route("/listings", endpoint="home") # Redirects '/' to 'select_campus', so /listings can be accessed directly if user continues without signing in
def listings_redirect():
    return redirect(url_for('listings_feed'))

# --- Data Seeding ---
def seed_campus_data():
    if not Campus.query.filter_by(slug='ucsc').first():
        ucsc = Campus(name='UC Santa Cruz', slug='ucsc')
        db.session.add(ucsc)
        db.session.commit()
        print("Seeded UC Santa Cruz campus.")

@app.route("/profiles/search", endpoint="profile_search")
def profile_search():
    return render_template("profile_search.html")


@app.get("/api/profiles/search")
def profile_search_api():
    q = (request.args.get("q") or "").strip()

    if not q:
        return jsonify({"profiles": []})

    like = f"%{q.lower()}%"

    results = (
        Profile.query
        .join(Profile.user)
        .filter(
            or_(
                func.lower(Users.username).like(like),
                func.lower(Profile.display_name).like(like),
                func.lower(Profile.city).like(like),
                func.lower(Profile.genres).like(like),
            )
        )
        .order_by(Users.username.asc())
        .limit(10)
        .all()
    )

    return jsonify({
        "profiles": [
            {
                "id": p.id,
                "username": p.user.username,
                "display_name": p.display_name,
                "city": p.city or "",
                "genres": p.genres or "",
                "avatar_url": p.avatar_url or "/static/default-avatar.png",
            }
            for p in results
        ]
    })

@app.get("/p/<int:profile_id>", endpoint="profile_detail")
def profile_detail(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    return render_template("profile_detail.html", profile=profile)





@app.get("/messages/need-account", endpoint="go_signup_for_messages")
def go_signup_for_messages():
    if current_user.is_authenticated:
        return redirect(url_for("inbox"))
    flash("Create an account to start messaging.", "info")
    return redirect(url_for("signup"))

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
            "other_name": (other_profile.display_name if other_profile else (other_user.username if other_user else "User")),
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



@app.post("/messages/start")
@login_required
def start_conversation():
    recipient_raw = (request.form.get("recipient_id") or "").strip()
    body = (request.form.get("body") or "").strip()  # optional

    if not recipient_raw.isdigit():
        flash("Invalid recipient.", "danger")
        return redirect(request.referrer or url_for("listings_feed"))

    recipient_id = int(recipient_raw)
    if recipient_id == current_user.id:
        flash("You can’t message yourself.", "danger")
        return redirect(request.referrer or url_for("listings_feed"))

    recipient = Users.query.get_or_404(recipient_id)

    convo = get_or_create_conversation(current_user.id, recipient.id)

    # If you want “Send” to open DM even with empty body, allow it:
    if body:
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
        if body:
            msg = make_message(convo, current_user.id, body)
            db.session.add(msg)
            db.session.commit()
        return redirect(url_for("view_conversation", conversation_id=conversation_id))
    
    msgs = convo.messages.order_by(Message.created_at.asc()).all()

    # mark as read for current user
    if convo.user1_id == current_user.id:
        Message.query.filter_by(conversation_id=convo.id).update({"read_by_user1": True})
    else:
        Message.query.filter_by(conversation_id=convo.id).update({"read_by_user2": True})
    db.session.commit()

    return render_template("conversation.html", convo=convo, messages=msgs)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed_campus_data()
    app.run(debug=True)