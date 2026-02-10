from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Conversation, Message, Profile, Users


# --- Messaging helpers ---
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


# --- Routes ---
def register(app, *, is_rate_limited, MAX_MESSAGE_LENGTH):
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
