from flask import abort, flash, redirect, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models import BookingRequest, Conversation, Listing, ListingNotification, Message


def register(app):
    # TODO: add booking request endpoints here
    pass
