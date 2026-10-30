"""The only pages the bot is allowed to know about. Nothing else is crawled."""

SOURCES = [
    {
        "url": "https://www.placidway.com/profile/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center",
        "type": "Clinic profile",
    },
    {
        "url": "https://www.placidway.com/package/7903/Alternative-Adenocarcinoma-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC",
        "type": "Treatment package",
    },
    {
        "url": "https://www.placidway.com/package/7902/Alternative-Adrenal-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC",
        "type": "Treatment package",
    },
    {
        "url": "https://www.placidway.com/package/7901/Alternative-Anal-Cancer-Treatment-Package-in-Tijuana-Mexico-by-ITC",
        "type": "Treatment package",
    },
    {
        "url": "https://www.placidway.com/profile-videos/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center",
        "type": "Clinic videos",
    },
    {
        "url": "https://www.placidway.com/price-list/5/Alternative-Cancer-Treatment-by-ITC-Immunity-Therapy-Center",
        "type": "Price list",
    },
    {
        "url": "https://www.placidway.com/search-medical-pricings/Alternative-Medicine/All/1",
        "type": "Price comparison",
    },
]

QUOTE_URL = "https://www.placidway.com/request-info"
CONTACT_URL = "https://www.placidway.com/contact-us"

# Links the bot may show besides the cited pages.
ALLOWED_EXTRA_URLS = {QUOTE_URL, CONTACT_URL}
