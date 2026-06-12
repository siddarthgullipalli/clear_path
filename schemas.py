# schemas.py
# Source of truth for all data contracts.
# Commit this first. Everyone imports from here. Never redefine these shapes elsewhere.

import os

# ── Shipments ─────────────────────────────────────────────────────────────────

SHIPMENTS = [
    {
        "id": "SH-01",
        "vessel": "MV Pacific Star",
        "origin": "Taipei",
        "destination": "Rotterdam",
        "cargo": "Semiconductors",
        "status": "IN_TRANSIT",        # IN_TRANSIT / DIVERTED / DELAYED
        "eta_days": 22,
        "waypoints": [                 # Taipei → S.China Sea → Singapore → Gulf of Aden → Suez → Rotterdam
            (25.0, 122.0),
            (20.0, 118.0),
            (1.3,  104.0),
            (12.0,  44.0),
            (30.0,  32.5),
            (51.9,   4.5),
        ],
    },
    {
        "id": "SH-02",
        "vessel": "MV Asian Horizon",
        "origin": "Singapore",
        "destination": "Los Angeles",
        "cargo": "Electronics",
        "status": "IN_TRANSIT",
        "eta_days": 18,
        "waypoints": [                 # Singapore → Philippines → Pacific → Hawaii → LA
            (1.3,  104.0),
            (14.5, 121.0),
            (25.0, 140.0),
            (21.3, -157.8),
            (33.7, -118.2),
        ],
    },
    {
        "id": "SH-03",
        "vessel": "MV Northern Light",
        "origin": "Shanghai",
        "destination": "Hamburg",
        "cargo": "Automotive Parts",
        "status": "IN_TRANSIT",
        "eta_days": 25,
        "waypoints": [                 # Shanghai → East China Sea → Malacca → Indian Ocean → Suez → Hamburg
            (31.2, 121.5),
            (22.0, 114.0),
            (5.0,  100.0),
            (11.5,  43.0),
            (30.0,  32.5),
            (53.5,   9.9),
        ],
    },
    {
        "id": "SH-04",
        "vessel": "MV Coral Queen",
        "origin": "Sydney",
        "destination": "Dubai",
        "cargo": "Minerals",
        "status": "IN_TRANSIT",
        "eta_days": 16,
        "waypoints": [                 # Sydney → Bass Strait → Indian Ocean → Maldives → Arabian Sea → Dubai
            (-33.8, 151.2),
            (-38.0, 146.0),
            (-20.0,  80.0),
            (4.0,   73.5),
            (15.0,  58.0),
            (25.2,  55.3),
        ],
    },
    {
        "id": "SH-05",
        "vessel": "MV Atlantic Bridge",
        "origin": "New York",
        "destination": "Lagos",
        "cargo": "Machinery",
        "status": "IN_TRANSIT",
        "eta_days": 14,
        "waypoints": [                 # New York → Mid Atlantic → Azores → Cape Verde → Gulf of Guinea → Lagos
            (40.7, -74.0),
            (35.0, -50.0),
            (38.7, -27.2),
            (15.0, -23.5),
            (4.0,   2.0),
            (6.5,   3.4),
        ],
    },
]

# Lookup by ID — used by agent and frontend
SHIPMENTS_BY_ID = {s["id"]: s for s in SHIPMENTS}


# ── Alternate routes (hardcoded — no path calculation needed) ─────────────────
# Agent picks this when severity is HIGH or CRITICAL.

ALTERNATE_ROUTES = {
    "SH-01": {"route": "Via Cape of Good Hope (bypass Suez)",   "eta_impact_hrs": 168},
    "SH-02": {"route": "Northern Pacific route via Aleutians",  "eta_impact_hrs": 36},
    "SH-03": {"route": "Via Cape of Good Hope (bypass Suez)",   "eta_impact_hrs": 192},
    "SH-04": {"route": "Divert to Colombo port, wait 48hrs",    "eta_impact_hrs": 48},
    "SH-05": {"route": "Southern Atlantic route, avoid Gulf",   "eta_impact_hrs": 24},
}


# ── Risk thresholds ───────────────────────────────────────────────────────────
# Trigger field is wind_knots_max_72h (worst-case over next 72 hours).
# Severity = highest bracket where EITHER condition is met.

RISK_THRESHOLDS = {
    "LOW":      {"wind_max": 30, "storm_prob": 0.15},
    "MEDIUM":   {"wind_max": 45, "storm_prob": 0.35},
    "HIGH":     {"wind_max": 60, "storm_prob": 0.60},
    "CRITICAL": {"wind_max": 75, "storm_prob": 0.80},
}

def classify_severity(wind_knots_max_72h: float, storm_probability: float) -> str:
    """
    Returns severity string. Highest bracket where either condition is met wins.
    Person 1 calls this inside the score_risk node — no LLM needed for the label.
    LLM is only used to generate the natural language reasoning string.
    """
    if wind_knots_max_72h >= 75 or storm_probability >= 0.80:
        return "CRITICAL"
    if wind_knots_max_72h >= 60 or storm_probability >= 0.60:
        return "HIGH"
    if wind_knots_max_72h >= 45 or storm_probability >= 0.35:
        return "MEDIUM"
    return "LOW"


# ── WeatherForecast shape (what Person 2's get_weather_forecast() returns) ────

# WeatherForecast = {
#     "lat":                 float,
#     "lon":                 float,
#     "wind_knots_now":      float,   # current reading — display only
#     "wind_knots_max_72h":  float,   # max over next 72hrs — triggers rerouting
#     "wave_height_m":       float,
#     "storm_probability":   float,   # 0.0 – 1.0
# }


# ── RiskResult shape (what the agent outputs, what OpenUI card consumes) ──────

# RiskResult = {
#     "shipment_id":    str,
#     "vessel":         str,
#     "origin":         str,
#     "destination":    str,
#     "status":         str,          # IN_TRANSIT / DIVERTED / DELAYED
#     "severity":       str,          # LOW / MEDIUM / HIGH / CRITICAL
#     "reasoning":      str,          # Pioneer LLM natural language output
#     "alternate_route": str,
#     "eta_impact_hrs": int,
#     "weather_snapshot": {
#         "wind_knots_now":      float,
#         "wind_knots_max_72h":  float,
#         "storm_probability":   float,
#         "wave_height_m":       float,
#         "worst_waypoint":      tuple,  # (lat, lon) of riskiest point on route
#     },
# }


# ── Demo mode — guaranteed CRITICAL on SH-01 for the 3-min presentation ──────
# Set DEMO_MODE=true in .env before the demo.
# Person 2: in get_weather_forecast(), check this flag before calling Jua API.

DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

DEMO_FORECAST_OVERRIDE = {
    # Injected for waypoint (20.0, 118.0) on SH-01 — typhoon in South China Sea
    "lat":                20.0,
    "lon":               118.0,
    "wind_knots_now":     72.0,
    "wind_knots_max_72h": 91.0,   # triggers CRITICAL (>= 75)
    "wave_height_m":       8.5,
    "storm_probability":   0.94,  # triggers CRITICAL (>= 0.80)
}


# ── Import guide ──────────────────────────────────────────────────────────────
# Person 1 (agent.py):
#   from schemas import SHIPMENTS_BY_ID, ALTERNATE_ROUTES, classify_severity, DEMO_MODE
#
# Person 2 (database.py):
#   from schemas import SHIPMENTS, DEMO_MODE, DEMO_FORECAST_OVERRIDE
#
# Person 3 (main.py / openui component):
#   from schemas import SHIPMENTS, SHIPMENTS_BY_ID
#   (RiskResult arrives as a dict from the agent — no import needed)
