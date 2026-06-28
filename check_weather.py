"""
Check current temperature and set a GitHub Actions output controlling whether
the robot should run.

Time slots (CDT = UTC-5, summer):
  9 PM CT  → 02 UTC : always run
  2 AM CT  → 07 UTC : always run
  Noon CT  → 17 UTC : run only if temperature > 80°F
  6 PM CT  → 23 UTC : run only if temperature > 90°F
  manual trigger    : always run
"""

import datetime
import os
import sys

import requests

ALWAYS_RUN_HOURS = {2, 7}
NOON_UTC = 17
EVENING_UTC = 23


def get_temp_f(lat: float, lon: float) -> float:
    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m",
            "temperature_unit": "fahrenheit",
            "forecast_days": 1,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["current"]["temperature_2m"]


def main():
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    utc_hour = datetime.datetime.now(datetime.timezone.utc).hour
    lat_str = os.environ.get("WEATHER_LAT", "")
    lon_str = os.environ.get("WEATHER_LON", "")

    if event == "workflow_dispatch":
        print("Manual trigger — running regardless of weather.")
        run = True
    elif utc_hour in ALWAYS_RUN_HOURS:
        print(f"Fixed schedule (UTC {utc_hour:02d}:00) — always runs.")
        run = True
    elif not lat_str or not lon_str:
        print("WEATHER_LAT/WEATHER_LON not configured — running without weather check.")
        run = True
    else:
        temp = get_temp_f(float(lat_str), float(lon_str))
        if utc_hour == NOON_UTC:
            run = temp > 80
            print(f"Noon slot: {temp:.1f}°F — {'running' if run else 'skipping (threshold 80°F)'}.")
        elif utc_hour == EVENING_UTC:
            run = temp > 90
            print(f"6 PM slot: {temp:.1f}°F — {'running' if run else 'skipping (threshold 90°F)'}.")
        else:
            print(f"Unexpected UTC hour {utc_hour} — running anyway.")
            run = True

    output_file = os.environ.get("GITHUB_OUTPUT", "")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"should_run={'true' if run else 'false'}\n")
    else:
        sys.exit(0 if run else 1)


if __name__ == "__main__":
    main()
