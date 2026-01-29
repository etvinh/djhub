

"""Endpoint                Methods    Rule                             
----------------------  ---------  ---------------------------------
about                   GET        /about                           
api_listings            GET        /api/listings                    
create_listing          GET, POST  /listings/new                    
edit_listing            GET, POST  /listings/<int:listing_id>/edit  
go_signup_for_messages  GET        /messages/need-account           
home                    GET        /listings                        
landing                 GET        /                                
listing_detail          GET        /l/<int:listing_id>              
listings_feed           GET        /feed                            
login                   GET, POST  /login                           
logout                  GET        /logout                          
my_bookings             GET        /my-bookings                     
my_listings             GET        /my-listings                     
my_profile              GET, POST  /my-profile                      
profile_detail          GET        /p/<int:profile_id>              
profile_review          POST       /profiles/<int:profile_id>/review
profile_search          GET        /profiles/search                 
profile_search_api      GET        /api/profiles/search             
resend_verification     POST       /verify-email/resend             
select_campus           GET, POST  /select-campus                   
signup                  GET, POST  /signup                          
static                  GET        /static/<path:filename>          
verify_email            GET, POST  /verify-email               

"""

# --- Imports ---
from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from extensions import db
from models import (
    BookingRequest,
    Genre,
    Listing,
    ListingNotification,
    Location,
    Profile,
)


def register(app):
    # --- Listings: detail + feed + API ---
    # Register GET route for a listing detail page
    @app.get("/l/<int:listing_id>", endpoint="listing_detail")
    
    
    
    def listing_detail(listing_id):
        # Fetch listing or 404 if missing
        listing = Listing.query.get_or_404(listing_id)
        # Look up any accepted booking for this listing
        accepted_request = BookingRequest.query.filter_by(
            # Match listing id
            listing_id=listing.id,
            # Only accepted requests
            status="accepted"
        # Execute query for first match
        ).first()
        # If listing is archived, restrict access
        if listing.is_archived:
            # If not logged in, hide archived listing
            if not current_user.is_authenticated:
                # Return 404 for unauthorized viewers
                abort(404)
            # Owner can view their own archived listing
            is_owner = listing.profile and current_user.id == listing.profile.user_id
            # Booked DJ can view archived listing
            is_booked_dj = accepted_request and accepted_request.requester_id == current_user.id
            # If neither owner nor booked DJ, deny access
            if not (is_owner or is_booked_dj):
                # Return 404 for unauthorized viewers
                abort(404)
        # If logged in, mark notifications as read
        if current_user.is_authenticated:
            # Mark listing notifications as read
            ListingNotification.query.filter_by(
                # Match listing id
                listing_id=listing.id,
                # Match current user
                recipient_id=current_user.id,
                # Only unread notifications
                is_read=False
            # Update notification state
            ).update({"is_read": True})
            # Persist notification updates
            db.session.commit()
        # Initialize list of booking requests
        booking_requests = []
        # Initialize current user's request
        current_request = None
        # Initialize map of requester profiles
        requester_profiles = {}
        # Only compute requests if logged in and listing has owner profile
        if current_user.is_authenticated and listing.profile:
            # If viewer is listing owner
            if current_user.id == listing.profile.user_id:
                # Start query for all requests on listing
                request_query = BookingRequest.query.filter_by(listing_id=listing.id)
                # If archived, show only accepted requests
                if listing.is_archived:
                    # Narrow to accepted requests
                    request_query = request_query.filter_by(status="accepted")
                # Fetch requests ordered by newest first
                booking_requests = (request_query
                                    # Sort by created time desc
                                    .order_by(BookingRequest.created_at.desc())
                                    # Execute query
                                    .all())
                # If requests exist, prefetch requester profiles
                if booking_requests:
                    # Collect requester user IDs
                    requester_ids = [req.requester_id for req in booking_requests]
                    # Fetch profiles for requesters
                    profiles = Profile.query.filter(Profile.user_id.in_(requester_ids)).all()
                    # Build user_id -> profile map
                    requester_profiles = {p.user_id: p for p in profiles}
            # If viewer is not listing owner
            else:
                # If archived, only show accepted request
                if listing.is_archived:
                    # Store accepted request for viewer
                    current_request = accepted_request
                # Otherwise, show viewer's request if any
                else:
                    # Fetch request for current user
                    current_request = BookingRequest.query.filter_by(
                        # Match listing id
                        listing_id=listing.id,
                        # Match requester id
                        requester_id=current_user.id
                    # Execute query
                    ).first()
        # Render listing detail template
        return render_template(
            # Template name
            "listing_detail.html",
            # Listing record
            listing=listing,
            # Requests for owner view
            booking_requests=booking_requests,
            # Current user's request
            current_request=current_request,
            # Accepted request (if any)
            accepted_request=accepted_request,
            # Map of requester profiles
            requester_profiles=requester_profiles,
        )
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    # Pagination helper for listings
    def listings_pagination_from_request(per_page=10): #pagination helper
        # Get page param (default 1)
        page = request.args.get("page", 1, type=int)
    
        # Read keyword filter
        keyword = (request.args.get("keyword") or "").strip()[:100]
        # Read genre filter
        genre = (request.args.get("genre") or "").strip()[:100]
        # Read location filter
        location = (request.args.get("location") or "").strip()[:100]
        # Read sort option
        sort = (request.args.get("sort") or "").strip().lower()
    
        # Base query for active listings
        q = Listing.query.filter(Listing.is_archived.is_(False))
    
        # Apply keyword search if provided
        if keyword:
            # Build case-insensitive LIKE pattern
            like = f"%{keyword.lower()}%"
            # Filter title/description by keyword
            q = q.filter(or_(
                # Title matches keyword
                func.lower(Listing.title).like(like),
                # Description matches keyword
                func.lower(Listing.description).like(like),
            ))
    
        # Apply genre filter if provided
        if genre:
            # Filter by exact genre (case-insensitive)
            q = q.filter(func.lower(Listing.genres) == genre.lower())
    
        # Apply location filter if provided
        if location:
            # Filter by exact city (case-insensitive)
            q = q.filter(func.lower(Listing.city) == location.lower())
    
        # Sort by ascending budget
        if sort == "price_asc":
            # Order by budget asc then newest
            q = q.order_by(Listing.budget.asc().nullslast(), Listing.created_at.desc())
        # Sort by descending budget
        elif sort == "price_desc":
            # Order by budget desc then newest
            q = q.order_by(Listing.budget.desc().nullslast(), Listing.created_at.desc())
        # Sort by oldest
        elif sort == "oldest":
            # Order by created time asc
            q = q.order_by(Listing.created_at.asc())
        # Default sort by newest
        else:
            # Order by created time desc
            q = q.order_by(Listing.created_at.desc())
    
        # Return paginated results
        return q.paginate(page=page, per_page=per_page, error_out=False)
    
    # Register listings API endpoint
    @app.get("/api/listings")
    # Define listings API handler
    def api_listings():
        # Build paginated query
        pagination = listings_pagination_from_request(per_page=10)
    
        # Return JSON response
        return jsonify({
            # Listings payload
            "listings": [
                {
                    # Listing id
                    "id": l.id,
                    # Listing title
                    "title": l.title,
                    # Listing city
                    "city": l.city,
                    # Listing date in ISO
                    "date": l.date.isoformat() if l.date else None,
                    # Listing time
                    "time": l.time,
                    # Listing budget
                    "budget": l.budget,
                    # Listing genres
                    "genres": l.genres,
                    # Listing description
                    "description": l.description,
                    # Cover image URL
                    "cover_image_url": l.cover_image_url,
                }
                # Build payload for each listing
                for l in pagination.items
            ],
            # Whether more pages exist
            "has_next": pagination.has_next,
            # Next page number
            "next_page": pagination.next_num if pagination.has_next else None,
        })
    
    # Register listings feed page
    @app.route("/feed", endpoint="listings_feed")
    # Define feed handler
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    def listings_feed(): #display listing feed
        # Build paginated query
        pagination = listings_pagination_from_request(per_page=10)
        # Fetch genres list
        genres = Genre.query.order_by(Genre.name.asc()).all()
        # Fetch locations list
        locations = Location.query.order_by(Location.name.asc()).all()
    
        # Render feed template
        return render_template(
            # Template name
            "index.html",
            # Listings for page
            listings=pagination.items,
            # Whether next page exists
            has_next=pagination.has_next,
            # Next page number
            next_page=pagination.next_num if pagination.has_next else None,
            # Available genres
            genres=genres,
            # Available locations
            locations=locations,
        )
    
    
    
    # --- Listings: create/edit/manage ---
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
