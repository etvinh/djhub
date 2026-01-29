import os
import uuid

from flask import flash, redirect, render_template, request, url_for, jsonify
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from werkzeug.utils import secure_filename

from extensions import db
from models import Genre, Location, Profile, ProfileTrack, Review, Users

# --- Profiles ---
def register(app):
    from app import normalize_instagram_url, normalize_spotify_url, get_upload_size
    # --- Profiles ---
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
    
    
    
    
    
    
    
    
    
    
    
    
    
    # --- Profiles API ---
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
                 .group_by(Profile.id, Users.username))
    
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
