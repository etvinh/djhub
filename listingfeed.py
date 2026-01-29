# --- Listings: detail + feed + API ---
# Register GET route for a listing detail page
@app.get("/l/<int:listing_id>", endpoint="listing_detail")
# Define the listing detail handler
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
