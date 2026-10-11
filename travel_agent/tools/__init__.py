from .booking import book_flight, book_hotel
from .destinations import rank_destinations
from .flights import search_flights
from .hotels import search_hotels
from .weather import check_season

ALL_TOOLS = [rank_destinations, check_season, search_flights, search_hotels, book_flight, book_hotel]
