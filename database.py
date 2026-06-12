# database.py — Person 2 owns this file
# imports from schemas.py — do not redefine data shapes here

import json
import os

import clickhouse_connect
from dotenv import load_dotenv

from schemas import DEMO_FORECAST_OVERRIDE, DEMO_MODE, SHIPMENTS

load_dotenv()


# ── Section 1: ClickHouse client ──────────────────────────────────────────────

def get_client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8443")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        database=os.getenv("CLICKHOUSE_DATABASE", "default"),
        secure=True,
    )


# ── Section 2: setup_tables() ─────────────────────────────────────────────────

def setup_tables():
    client = get_client()

    client.command("CREATE DATABASE IF NOT EXISTS supply_chain")

    client.command("""
        CREATE TABLE IF NOT EXISTS supply_chain.shipments (
            id          String,
            vessel      String,
            origin      String,
            destination String,
            cargo       String,
            status      String,
            eta_days    Int32,
            waypoints   String
        ) ENGINE = MergeTree() ORDER BY id
    """)

    client.command("""
        CREATE TABLE IF NOT EXISTS supply_chain.weather_forecasts (
            shipment_id        String,
            lat                Float64,
            lon                Float64,
            wind_knots_now     Float64,
            wind_knots_max_72h Float64,
            wave_height_m      Float64,
            storm_probability  Float64,
            fetched_at         DateTime DEFAULT now()
        ) ENGINE = MergeTree() ORDER BY (shipment_id, fetched_at)
    """)

    print("Tables created")


# ── Section 3: seed_shipments() ───────────────────────────────────────────────

def seed_shipments():
    client = get_client()

    client.command("TRUNCATE TABLE supply_chain.shipments")

    rows = [
        [
            s["id"],
            s["vessel"],
            s["origin"],
            s["destination"],
            s["cargo"],
            s["status"],
            s["eta_days"],
            json.dumps(s["waypoints"]),
        ]
        for s in SHIPMENTS
    ]

    client.insert(
        "supply_chain.shipments",
        rows,
        column_names=["id", "vessel", "origin", "destination", "cargo", "status", "eta_days", "waypoints"],
    )

    print("Seeded 5 shipments")


# ── Section 4: get_weather_forecast(lat, lon) ─────────────────────────────────

def get_weather_forecast(lat: float, lon: float) -> dict:
    # Demo mode: inject typhoon reading for the South China Sea waypoint
    if DEMO_MODE and abs(lat - 20.0) < 0.1 and abs(lon - 118.0) < 0.1:
        return DEMO_FORECAST_OVERRIDE

    # Mock mode: serve from local fixture file
    if os.getenv("USE_MOCKS", "false").lower() == "true":
        mock_path = os.path.join(os.path.dirname(__file__), "mocks", "jua_weather.json")
        with open(mock_path) as f:
            mocks = json.load(f)
        for m in mocks:
            if abs(lat - m["lat"]) < 1.0 and abs(lon - m["lon"]) < 1.0:
                return {
                    "lat": m["lat"],
                    "lon": m["lon"],
                    "wind_knots_now": m["wind_knots_now"],
                    "wind_knots_max_72h": m["wind_knots_max_72h"],
                    "wave_height_m": m["wave_height_m"],
                    "storm_probability": m["storm_probability"],
                }
        # Calm fallback when no mock entry matches the coordinate
        return {
            "lat": lat,
            "lon": lon,
            "wind_knots_now": 10.0,
            "wind_knots_max_72h": 12.0,
            "wave_height_m": 0.8,
            "storm_probability": 0.05,
        }

    # Live Open-Meteo API calls (free, no API key required)
    # Two endpoints: standard API for wind/precip, marine API for wave height
    import httpx

    # ── Standard forecast API: wind speed + precipitation ─────
    resp1 = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "wind_speed_10m,precipitation_probability",
            "wind_speed_unit": "kn",
            "forecast_days": 3,
        },
        timeout=10.0,
    )
    resp1.raise_for_status()
    hourly1 = resp1.json()["hourly"]
    wind_speeds = hourly1["wind_speed_10m"]
    precip_probs = hourly1["precipitation_probability"]

    # ── Marine API: wave height (standard API returns None over ocean) ──
    try:
        resp2 = httpx.get(
            "https://marine-api.open-meteo.com/v1/marine",
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "wave_height",
                "forecast_days": 3,
            },
            timeout=10.0,
        )
        resp2.raise_for_status()
        wave_heights = resp2.json()["hourly"]["wave_height"]
    except Exception:
        wave_heights = [0.0]  # fall back to 0 if marine API fails

    wave_now = wave_heights[0] if wave_heights and wave_heights[0] is not None else 0.0

    return {
        "lat": lat,
        "lon": lon,
        "wind_knots_now": round(wind_speeds[0], 1),
        "wind_knots_max_72h": round(max(wind_speeds), 1),
        "wave_height_m": round(wave_now, 1),
        "storm_probability": round(max(p for p in precip_probs if p is not None) / 100.0, 2),
    }


# ── Section 5: get_shipment_risks() ──────────────────────────────────────────

def get_shipment_risks() -> list:
    results = []

    for s in SHIPMENTS:
        worst_forecast = None
        worst_waypoint = None

        for lat, lon in s["waypoints"]:
            forecast = get_weather_forecast(lat, lon)
            if worst_forecast is None or forecast["wind_knots_max_72h"] > worst_forecast["wind_knots_max_72h"]:
                worst_forecast = forecast
                worst_waypoint = [lat, lon]

        results.append({
            "shipment_id": s["id"],
            "vessel": s["vessel"],
            "origin": s["origin"],
            "destination": s["destination"],
            "cargo": s["cargo"],
            "status": s["status"],
            "eta_days": s["eta_days"],
            "worst_waypoint": worst_waypoint,
            "weather_snapshot": {
                "wind_knots_now": worst_forecast["wind_knots_now"],
                "wind_knots_max_72h": worst_forecast["wind_knots_max_72h"],
                "wave_height_m": worst_forecast["wave_height_m"],
                "storm_probability": worst_forecast["storm_probability"],
            },
        })

    _store_forecasts(results)
    return results


# ── Section 6: _store_forecasts(results) ─────────────────────────────────────

def _store_forecasts(results: list) -> None:
    try:
        client = get_client()
        rows = []
        for r in results:
            snap = r["weather_snapshot"]
            lat, lon = r["worst_waypoint"]
            rows.append([
                r["shipment_id"],
                lat,
                lon,
                snap["wind_knots_now"],
                snap["wind_knots_max_72h"],
                snap["wave_height_m"],
                snap["storm_probability"],
            ])
        client.insert(
            "supply_chain.weather_forecasts",
            rows,
            column_names=[
                "shipment_id", "lat", "lon",
                "wind_knots_now", "wind_knots_max_72h",
                "wave_height_m", "storm_probability",
            ],
        )
    except Exception as e:
        print(f"Warning: failed to store forecasts in ClickHouse: {e}")


# ── Section 7: main block ─────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        setup_tables()
        seed_shipments()
    except Exception as e:
        print(f"Warning: ClickHouse setup skipped ({e})")

    os.environ["USE_MOCKS"] = "true"
    results = get_shipment_risks()
    print(json.dumps(results, indent=2))
    print("All done")
