import os

# ── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE").strip()
ADMIN_IDS = set(
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
)

# ── Swiggy API ────────────────────────────────────────────────────────────────
PROFILE_BASE_URL  = "https://profile.swiggy.com"
OTP_ENDPOINT      = "/api/v3/app/sms_otp"
VERIFY_ENDPOINT   = "/api/v3/app/login/verify"
PROFILE_ENDPOINT  = "/api/v3/app/profile"           # GET — check if name needed
UPDATE_ENDPOINT   = "/api/v3/app/profile/update"    # POST — set name/email
ADDRESS_ENDPOINT  = "/api/v2/user_address/save"     # POST — add Delhi address

# ── Device pool — rotated per login ──────────────────────────────────────────
# Each dict: manufacturer, model, version-code, app-version
DEVICE_POOL = [
    # Motorola
    {"manufacturer": "MOTOROLA", "model-name": "MOTO G(60)",      "version-code": "1795", "app-version": "4.113.0"},
    {"manufacturer": "MOTOROLA", "model-name": "MOTO G82",        "version-code": "1795", "app-version": "4.113.0"},
    {"manufacturer": "MOTOROLA", "model-name": "MOTO G72",        "version-code": "1795", "app-version": "4.113.0"},
    # Realme
    {"manufacturer": "REALME",   "model-name": "RMX3085",         "version-code": "1795", "app-version": "4.113.0"},
    {"manufacturer": "REALME",   "model-name": "RMX3761",         "version-code": "1795", "app-version": "4.113.0"},
    {"manufacturer": "REALME",   "model-name": "RMX3521",         "version-code": "1795", "app-version": "4.113.0"},
    # OPPO
    {"manufacturer": "OPPO",     "model-name": "CPH2385",         "version-code": "1795", "app-version": "4.113.0"},
    {"manufacturer": "OPPO",     "model-name": "CPH2269",         "version-code": "1795", "app-version": "4.113.0"},
    {"manufacturer": "OPPO",     "model-name": "CPH2471",         "version-code": "1795", "app-version": "4.113.0"},
    # Samsung
    {"manufacturer": "SAMSUNG",  "model-name": "SM-A135F",        "version-code": "1795", "app-version": "4.113.0"},
    {"manufacturer": "SAMSUNG",  "model-name": "SM-M336B",        "version-code": "1795", "app-version": "4.113.0"},
    {"manufacturer": "SAMSUNG",  "model-name": "SM-A528B",        "version-code": "1795", "app-version": "4.113.0"},
    # Xiaomi / Redmi
    {"manufacturer": "XIAOMI",   "model-name": "2201117TI",       "version-code": "1795", "app-version": "4.113.0"},
    {"manufacturer": "REDMI",    "model-name": "2201117TG",       "version-code": "1795", "app-version": "4.113.0"},
    {"manufacturer": "XIAOMI",   "model-name": "23049PCD8G",      "version-code": "1795", "app-version": "4.113.0"},
    # Vivo
    {"manufacturer": "VIVO",     "model-name": "V2254",           "version-code": "1795", "app-version": "4.113.0"},
    {"manufacturer": "VIVO",     "model-name": "V2206",           "version-code": "1795", "app-version": "4.113.0"},
    # OnePlus
    {"manufacturer": "ONEPLUS",  "model-name": "CPH2423",         "version-code": "1795", "app-version": "4.113.0"},
    {"manufacturer": "ONEPLUS",  "model-name": "NE2211",          "version-code": "1795", "app-version": "4.113.0"},
]

# ── Indian first names for account generation ─────────────────────────────────
FIRST_NAMES = [
    "Aarav","Aditya","Akash","Amit","Ankit","Ankur","Arjun","Arnav","Ayush",
    "Deepak","Gaurav","Harsh","Karan","Kunal","Manish","Mohit","Nikhil","Nitin",
    "Pranav","Rahul","Raj","Rajesh","Ravi","Rohit","Sachin","Sahil","Sanjay",
    "Shivam","Sumit","Suresh","Tarun","Varun","Vikram","Vikas","Vishal","Yash",
    "Priya","Pooja","Neha","Anjali","Divya","Sneha","Riya","Nisha","Kavya",
    "Meera","Swati","Shreya","Tanvi","Avni","Ishaan","Dhruv","Kabir","Veer",
]
LAST_NAMES = [
    "Sharma","Verma","Singh","Kumar","Gupta","Patel","Joshi","Mehta","Agarwal",
    "Yadav","Tiwari","Mishra","Pandey","Shukla","Srivastava","Dubey","Chauhan",
    "Rajput","Nair","Reddy","Iyer","Pillai","Menon","Ghosh","Das","Bose","Sen",
    "Kapoor","Malhotra","Khurana","Arora","Bhatia","Sethi","Chopra","Khanna",
]

# ── Random email providers ────────────────────────────────────────────────────
EMAIL_DOMAINS = [
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "rediffmail.com", "ymail.com",
]

# ── Delhi delivery location (randomised slightly per account) ─────────────────
DELHI_LOCATIONS = [
    {"lat": 28.6139, "lng": 77.2090, "area": "Connaught Place",   "city": "New Delhi"},
    {"lat": 28.5355, "lng": 77.3910, "area": "Noida Sector 18",   "city": "Noida"},
    {"lat": 28.6692, "lng": 77.4538, "area": "Indirapuram",       "city": "Ghaziabad"},
    {"lat": 28.4595, "lng": 77.0266, "area": "Dwarka Sector 10",  "city": "New Delhi"},
    {"lat": 28.5706, "lng": 77.3219, "area": "Lajpat Nagar",      "city": "New Delhi"},
    {"lat": 28.6304, "lng": 77.2177, "area": "Karol Bagh",        "city": "New Delhi"},
    {"lat": 28.5494, "lng": 77.1883, "area": "Saket",             "city": "New Delhi"},
    {"lat": 28.6862, "lng": 77.1322, "area": "Rohini Sector 3",   "city": "New Delhi"},
    {"lat": 28.6271, "lng": 77.3732, "area": "Preet Vihar",       "city": "New Delhi"},
    {"lat": 28.5921, "lng": 77.0460, "area": "Janakpuri",         "city": "New Delhi"},
]

# ── Proxy ─────────────────────────────────────────────────────────────────────
# HOST_PROXY: used first before asking admin.  Set via env or leave blank.
HOST_PROXY = os.environ.get("HOST_PROXY", "").strip() or None

# ── Firebase ──────────────────────────────────────────────────────────────────
OTP_TIMEOUT       = 50
OTP_POLL_INTERVAL = 1.0
FB_TIMEOUT        = 6

SMS_SUBPATHS = [
    "messages", "sms", "inbox", "Sms", "SMS",
    "callLogs", "calls", "notifications",
    "receivedSms", "smsList", "All_Users/sms", "user_sms",
]
ROOT_KEYS = [
    "clients", "devices", "users", "Clients", "Devices",
    "client", "device", "phones", "mobiles", "user_data", "All_Users",
]
ONLINE_FIELDS = ("status", "online", "isOnline", "connected", "active")
ONLINE_TRUE   = {"true", "1", "online", "connected", "active", "yes"}

# ── Runtime ───────────────────────────────────────────────────────────────────
MAX_CONCURRENT = 3
SESSIONS_DIR   = "sessions"
