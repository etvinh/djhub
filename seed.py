from app import app
from djhub.extensions import db
from djhub.models import Users, Profile, Campus, Listing
from werkzeug.security import generate_password_hash

from datetime import date, timedelta
import random

# --------------------
# Seed constants
# --------------------
USERNAMES = ["djalex", "djethan", "djmia", "djzoe"]
CITIES = ["New York", "Los Angeles", "Chicago", "Miami"]
GENRES = ["House", "Techno", "HipHop", "EDM", "Lo-Fi"]

USER_SEED = [

    ("djalex",  "password123"),
    ("djethan", "password123"),
    ("djmia",   "password123"),
    ("djzoe",   "password123"),
]

def pick(seq):
    return random.choice(seq)

with app.app_context():
    print("🗑️ Dropping tables...")
    db.drop_all()

    print("🧱 Creating tables...")
    db.create_all()

    # --------------------
    # Campus
    # --------------------
    campus = Campus(name="UC Santa Cruz", slug="ucsc")
    db.session.add(campus)

    # --------------------
    # Users + Profiles
    # --------------------
    users = []

    for username, pw in USER_SEED:
        user = Users(
            username=username,
            password=generate_password_hash(pw, method="scrypt")  # ✅ matches your login code
        )
        db.session.add(user)
        db.session.flush()  # 🔑 ensures user.id exists

        # Create MULTIPLE profiles per user
        profiles = [
            Profile(
                user_id=user.id,
                display_name=f"{username.title()} (DJ)",
                city=pick(CITIES),
                genres=pick(GENRES),
                bio="DJ available for gigs and events.",
                avatar_url=f"https://api.dicebear.com/7.x/identicon/svg?seed={username}-dj"
            )
           
        ]

        db.session.add_all(profiles)
        users.append(user)

    print(f"👤 Seeded {len(users)} users with {len(users) * 2} profiles")

    # --------------------
    # Listings
    # --------------------
    today = date.today()

    NUM_LISTINGS = 20

    for i in range(NUM_LISTINGS):
        p = pick(profiles)  # uses your helper

        listing = Listing(
            title="DJ Booking",
            city=pick(CITIES),
            date=date.today() + timedelta(days=pick(range(1, 30))),
            budget=pick(range(200, 801)),
            genres=pick(GENRES),
            description="Looking for a DJ.",
            profile=p,   # ✅ must be a Profile object
        )

        db.session.add(listing)

    print(f"🎤 Seeded {NUM_LISTINGS} listings")


    # --------------------
    # Commit
    # --------------------
    db.session.commit()
    print("✅ Database seeded successfully")
