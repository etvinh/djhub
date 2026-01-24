from extensions import db
from datetime import datetime
from flask_login import UserMixin
from sqlalchemy import Date # Import Date type for Listing model

# 1. Campus Model (For location selection)
class Campus(db.Model):
    id = db.Column(db.Integer, primary_key=True) 
    name = db.Column(db.String(100), unique=True, nullable=False)
    slug = db.Column(db.String(50), unique=True, nullable=False) # e.g., 'ucsc'
    is_active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f'<Campus {self.name}>'

# 2. Users Model (Crucial for Flask-Login Authentication)
class Users(UserMixin, db.Model): 
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(250), unique=True, nullable=False)
    password = db.Column(db.String(250), nullable=False) # Stores the hashed password
    profiles = db.relationship(
        "Profile",
        back_populates="user",
        cascade="all, delete-orphan"
    ) #one user to many profiles, delete orphan profiles

    
    def get_id(self):
        return str(self.id)
    
    def __repr__(self):
        return f'<User {self.username}>'

# 3. Profile Model
class Profile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column( #column for user_id in table Profile
        db.Integer,
        db.ForeignKey("users.id"), #user_id used for key between profile
        nullable = False,
        index = True

    )


    user = db.relationship("Users", back_populates="profiles") #profile inherets user

    display_name = db.Column(db.String(120), nullable=False)
    city = db.Column(db.String(120))
    genres = db.Column(db.String(200))
    bio = db.Column(db.Text)
    avatar_url = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Profile id={self.id} user_id={self.user_id} display_name={self.display_name}>"
# 4. Listing Model
class Listing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    city = db.Column(db.String(120))
    # Corrected the date column type
    date = db.Column(Date) 
    time = db.Column(db.String(20))
    budget = db.Column(db.Integer)
    genres = db.Column(db.String(200))
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_archived = db.Column(db.Boolean, default=False, nullable=False)
    profile_id = db.Column(
        db.Integer,
        db.ForeignKey("profile.id"),
        nullable=False,
        index=True
    )

    profile = db.relationship("Profile", backref="listings")
    
    def __repr__(self):
        return f"<Listing {self.title}>"

class BookingRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False, index=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("listing_id", "requester_id", name="uq_booking_request"),
    )

    requester = db.relationship("Users")
    listing = db.relationship("Listing")
    conversation = db.relationship("Conversation")

    def __repr__(self):
        return f"<BookingRequest listing_id={self.listing_id} requester_id={self.requester_id} status={self.status}>"

class ListingNotification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    message = db.Column(db.String(255), nullable=False)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    recipient = db.relationship("Users")
    listing = db.relationship("Listing")

    def __repr__(self):
        return f"<ListingNotification listing_id={self.listing_id} recipient_id={self.recipient_id} is_read={self.is_read}>"

class Genre(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

    def __repr__(self):
        return f"<Genre {self.name}>"

class Location(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)

    def __repr__(self):
        return f"<Location {self.name}>"

class Conversation(db.Model):
    __tablename__ = "conversations"
    id = db.Column(db.Integer, primary_key=True)

    user1_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_message_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Prevent duplicate conversations for the same pair (user1,user2) order-agnostic:
    __table_args__ = (
        db.UniqueConstraint("user1_id", "user2_id", name="uq_convo_user_pair"),
    )

    def other_user_id(self, me_id: int) -> int:
        return self.user2_id if self.user1_id == me_id else self.user1_id


class Message(db.Model):
    __tablename__ = "messages"
    id = db.Column(db.Integer, primary_key=True)

    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    body = db.Column(db.Text, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # simple read receipts (per-user)
    read_by_user1 = db.Column(db.Boolean, default=False, nullable=False)
    read_by_user2 = db.Column(db.Boolean, default=False, nullable=False)

    conversation = db.relationship("Conversation", backref=db.backref("messages", lazy="dynamic", cascade="all, delete-orphan"))
