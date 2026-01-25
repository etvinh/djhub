from app import app
from extensions import db
from models import Users, Profile, Campus, Listing, Genre, Location
from werkzeug.security import generate_password_hash

from datetime import date, timedelta
import random

# --------------------
# Seed constants
# --------------------
USERNAMES = ["djalex", "djethan", "djmia", "djzoe"]
CITIES = ["Santa Cruz"]
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
    total_profiles = 0

    for username, pw in USER_SEED:
        user = Users(
            username=username,
            password=generate_password_hash(pw, method="scrypt"),  # ✅ matches your login code
        )
        db.session.add(user)
        db.session.flush()  # 🔑 ensures user.id exists

        # Create MULTIPLE profiles per user
        profiles = [
            Profile(
                user_id=user.id,
                city=pick(CITIES),
                genres=pick(GENRES),
                bio="DJ available for gigs and events.",
                avatar_url=f"https://api.dicebear.com/7.x/identicon/svg?seed={username}-dj"
            )
           
        ]

        db.session.add_all(profiles)
        total_profiles += len(profiles)
        users.append(user)

    print(f"👤 Seeded {len(users)} users with {total_profiles} profiles")

    # --------------------
    # Listings
    # --------------------
    NUM_LISTINGS = 20
    TIMES = [
        "6:00 PM",
        "7:30 PM",
        "8:00 PM",
        "8:30 PM",
        "9:00 PM",
        "10:00 PM",
    ]

    for i in range(NUM_LISTINGS):
        p = pick(profiles)  # uses your helper

        listing = Listing(
            title="DJ Booking",
            city=pick(CITIES),
            date=date.today() + timedelta(days=pick(range(1, 30))),
            time=pick(TIMES),
            budget=pick(range(200, 801)),
            genres=pick(GENRES),
            description="Looking for a DJ.",
            profile=p,   # ✅ must be a Profile object
        )

        db.session.add(listing)

    print(f"🎤 Seeded {NUM_LISTINGS} listings")

    # --------------------
    # Genres + Locations
    # --------------------
    for name in sorted(set(GENRES)):
        db.session.add(Genre(name=name))

    db.session.add(Location(name="Santa Cruz"))


    # --------------------
    # Commit
    # --------------------
    db.session.commit()
    print("✅ Database seeded successfully")
