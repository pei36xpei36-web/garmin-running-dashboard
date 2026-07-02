"""抓取 Garmin Connect 的跑步活動與每日健康資料。

寫入本機 SQLite；若設定了 SUPABASE_DB_URL，會同步 upsert 到 Supabase 雲端，
讓部署在 Streamlit Cloud 的儀表板能跨裝置讀到最新資料。
"""

import argparse
import os
import sqlite3
import time
from datetime import date, timedelta

from dotenv import load_dotenv
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

import db

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "garmin_data.db")
TOKEN_STORE = os.path.join(BASE_DIR, ".garmin_tokens")

DEFAULT_LOOKBACK_DAYS = 365
WELLNESS_REQUEST_DELAY = 0.3  # 避免對 Garmin API 送出過快的請求


def get_client() -> Garmin:
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        raise SystemExit("請先在 .env 設定 GARMIN_EMAIL 與 GARMIN_PASSWORD")

    def _prompt_mfa() -> str:
        print("此 Garmin 帳號開啟了兩步驟驗證（MFA）。")
        return input("請輸入 Garmin App / 簡訊 / Email 收到的驗證碼: ").strip()

    client = Garmin(email, password, prompt_mfa=_prompt_mfa)
    try:
        client.login(tokenstore=TOKEN_STORE)
    except (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    ) as e:
        raise SystemExit(f"登入 Garmin Connect 失敗: {e}")
    return client


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS activities (
            activity_id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            activity_name TEXT,
            distance_km REAL,
            duration_sec REAL,
            avg_pace_sec_per_km REAL,
            avg_hr REAL,
            max_hr REAL,
            aerobic_training_effect REAL,
            anaerobic_training_effect REAL
        );

        CREATE TABLE IF NOT EXISTS daily_wellness (
            date TEXT PRIMARY KEY,
            resting_hr REAL,
            hrv_last_night_avg REAL,
            hrv_status TEXT,
            sleep_seconds REAL,
            deep_sleep_seconds REAL,
            light_sleep_seconds REAL,
            rem_sleep_seconds REAL,
            awake_sleep_seconds REAL,
            sleep_score REAL,
            total_steps REAL,
            step_goal REAL,
            min_hr REAL,
            max_hr REAL,
            avg_stress REAL,
            max_stress REAL,
            body_battery_high REAL,
            body_battery_low REAL,
            body_battery_charged REAL,
            body_battery_drained REAL,
            total_calories REAL
        );

        CREATE TABLE IF NOT EXISTS hr_zones (
            activity_id INTEGER,
            zone_number INTEGER,
            seconds_in_zone REAL,
            PRIMARY KEY (activity_id, zone_number)
        );
        """
    )
    # 為既有資料庫補上後來新增的欄位（第一版沒有這些健康欄位）
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(daily_wellness)")}
    for col in (
        "total_steps", "step_goal", "min_hr", "max_hr", "avg_stress", "max_stress",
        "body_battery_high", "body_battery_low", "body_battery_charged",
        "body_battery_drained", "total_calories",
    ):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE daily_wellness ADD COLUMN {col} REAL")
    conn.commit()


def _first_present(d: dict, keys: list[str]):
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return None


def fetch_activities(client: Garmin, start_date: date, end_date: date) -> list[dict]:
    raw = client.get_activities_by_date(
        start_date.isoformat(), end_date.isoformat(), activitytype="running"
    )

    rows = []
    for a in raw:
        distance_km = (a.get("distance") or 0) / 1000
        duration_sec = a.get("duration") or 0
        avg_pace = duration_sec / distance_km if distance_km > 0 else None
        rows.append(
            {
                "activity_id": a.get("activityId"),
                "date": (a.get("startTimeLocal") or "")[:10],
                "activity_name": a.get("activityName"),
                "distance_km": distance_km,
                "duration_sec": duration_sec,
                "avg_pace_sec_per_km": avg_pace,
                "avg_hr": a.get("averageHR"),
                "max_hr": a.get("maxHR"),
                "aerobic_training_effect": a.get("aerobicTrainingEffect"),
                "anaerobic_training_effect": a.get("anaerobicTrainingEffect"),
            }
        )
    return rows


def save_activities(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO activities (
            activity_id, date, activity_name, distance_km, duration_sec,
            avg_pace_sec_per_km, avg_hr, max_hr,
            aerobic_training_effect, anaerobic_training_effect
        ) VALUES (
            :activity_id, :date, :activity_name, :distance_km, :duration_sec,
            :avg_pace_sec_per_km, :avg_hr, :max_hr,
            :aerobic_training_effect, :anaerobic_training_effect
        )
        """,
        rows,
    )
    conn.commit()
    db.upsert_rows("activities", rows)


def fetch_hr_zones_for_new_activities(client: Garmin, conn: sqlite3.Connection, activity_ids: list[int]) -> None:
    existing = {
        row[0]
        for row in conn.execute("SELECT DISTINCT activity_id FROM hr_zones").fetchall()
    }
    todo = [aid for aid in activity_ids if aid is not None and aid not in existing]

    for aid in todo:
        try:
            zones = client.get_activity_hr_in_timezones(aid)
        except Exception as e:
            print(f"  警告：抓取活動 {aid} 的心率區間失敗，略過（{e}）")
            continue

        zone_rows = []
        for z in zones or []:
            zone_number = _first_present(z, ["zoneNumber", "zone"])
            seconds = _first_present(z, ["secsInZone", "secondsInZone", "timeInZone"])
            if zone_number is None or seconds is None:
                continue
            zone_rows.append(
                {"activity_id": aid, "zone_number": int(zone_number), "seconds_in_zone": seconds}
            )

        if zone_rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO hr_zones (activity_id, zone_number, seconds_in_zone)
                VALUES (:activity_id, :zone_number, :seconds_in_zone)
                """,
                zone_rows,
            )
            conn.commit()
            db.upsert_rows("hr_zones", zone_rows)
        time.sleep(WELLNESS_REQUEST_DELAY)


# daily_wellness 新增的健康欄位 → Garmin get_stats 回傳的 key
STATS_FIELD_MAP = {
    "total_steps": "totalSteps",
    "step_goal": "dailyStepGoal",
    "min_hr": "minHeartRate",
    "max_hr": "maxHeartRate",
    "avg_stress": "averageStressLevel",
    "max_stress": "maxStressLevel",
    "body_battery_high": "bodyBatteryHighestValue",
    "body_battery_low": "bodyBatteryLowestValue",
    "body_battery_charged": "bodyBatteryChargedValue",
    "body_battery_drained": "bodyBatteryDrainedValue",
    "total_calories": "totalKilocalories",
}


def fetch_daily_stats(client: Garmin, iso: str) -> dict:
    """抓某天的每日總覽（步數 / 壓力 / Body Battery / 每日 min-max 心率 / 卡路里）。"""
    row = {col: None for col in STATS_FIELD_MAP}
    try:
        stats = client.get_stats(iso) or {}
        for col, key in STATS_FIELD_MAP.items():
            row[col] = stats.get(key)
    except Exception as e:
        print(f"  警告：{iso} 每日總覽（步數/壓力/BodyBattery）抓取失敗（{e}）")
    return row


def fetch_daily_wellness(client: Garmin, conn: sqlite3.Connection, start_date: date, end_date: date) -> None:
    existing = {
        row[0]
        for row in conn.execute("SELECT date FROM daily_wellness").fetchall()
    }

    d = start_date
    while d <= end_date:
        iso = d.isoformat()
        if iso in existing:
            d += timedelta(days=1)
            continue

        resting_hr = None
        hrv_avg = None
        hrv_status = None
        sleep_seconds = deep_sleep = light_sleep = rem_sleep = awake_sleep = None
        sleep_score = None

        try:
            rhr_data = client.get_rhr_day(iso)
            metrics = (rhr_data or {}).get("allMetrics", {}).get("metricsMap", {})
            rhr_list = metrics.get("WELLNESS_RESTING_HEART_RATE") or []
            if rhr_list:
                resting_hr = rhr_list[0].get("value")
        except Exception as e:
            print(f"  警告：{iso} 靜息心率抓取失敗（{e}）")

        try:
            hrv_data = client.get_hrv_data(iso)
            summary = (hrv_data or {}).get("hrvSummary") or {}
            hrv_avg = summary.get("lastNightAvg")
            hrv_status = summary.get("status")
        except Exception as e:
            print(f"  警告：{iso} HRV 抓取失敗（{e}）")

        try:
            sleep_data = client.get_sleep_data(iso)
            dto = (sleep_data or {}).get("dailySleepDTO") or {}
            sleep_seconds = dto.get("sleepTimeSeconds")
            deep_sleep = dto.get("deepSleepSeconds")
            light_sleep = dto.get("lightSleepSeconds")
            rem_sleep = dto.get("remSleepSeconds")
            awake_sleep = dto.get("awakeSleepSeconds")
            sleep_score = ((dto.get("sleepScores") or {}).get("overall") or {}).get("value")
        except Exception as e:
            print(f"  警告：{iso} 睡眠資料抓取失敗（{e}）")

        wellness_row = {
            "date": iso,
            "resting_hr": resting_hr,
            "hrv_last_night_avg": hrv_avg,
            "hrv_status": hrv_status,
            "sleep_seconds": sleep_seconds,
            "deep_sleep_seconds": deep_sleep,
            "light_sleep_seconds": light_sleep,
            "rem_sleep_seconds": rem_sleep,
            "awake_sleep_seconds": awake_sleep,
            "sleep_score": sleep_score,
            **fetch_daily_stats(client, iso),
        }
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_wellness (
                date, resting_hr, hrv_last_night_avg, hrv_status,
                sleep_seconds, deep_sleep_seconds, light_sleep_seconds,
                rem_sleep_seconds, awake_sleep_seconds, sleep_score,
                total_steps, step_goal, min_hr, max_hr, avg_stress, max_stress,
                body_battery_high, body_battery_low, body_battery_charged,
                body_battery_drained, total_calories
            ) VALUES (
                :date, :resting_hr, :hrv_last_night_avg, :hrv_status,
                :sleep_seconds, :deep_sleep_seconds, :light_sleep_seconds,
                :rem_sleep_seconds, :awake_sleep_seconds, :sleep_score,
                :total_steps, :step_goal, :min_hr, :max_hr, :avg_stress, :max_stress,
                :body_battery_high, :body_battery_low, :body_battery_charged,
                :body_battery_drained, :total_calories
            )
            """,
            wellness_row,
        )
        conn.commit()
        db.upsert_rows("daily_wellness", [wellness_row])
        time.sleep(WELLNESS_REQUEST_DELAY)
        d += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description="抓取 Garmin 跑步與健康資料到本機 SQLite")
    parser.add_argument(
        "--update", action="store_true",
        help="只抓取資料庫中還沒有的新資料，而不是重新抓過去一年全部資料",
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_LOOKBACK_DAYS,
        help=f"沒有 --update 時，往前抓幾天的資料（預設 {DEFAULT_LOOKBACK_DAYS} 天）",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    end_date = date.today()
    if args.update:
        last_activity_date = conn.execute(
            "SELECT MAX(date) FROM activities"
        ).fetchone()[0]
        last_wellness_date = conn.execute(
            "SELECT MAX(date) FROM daily_wellness"
        ).fetchone()[0]
        candidates = [d for d in (last_activity_date, last_wellness_date) if d]
        if candidates:
            start_date = date.fromisoformat(min(candidates))
        else:
            start_date = end_date - timedelta(days=args.days)
    else:
        start_date = end_date - timedelta(days=args.days)

    if db.using_cloud():
        print("偵測到 SUPABASE_DB_URL：資料會同時寫入本機 SQLite 與 Supabase 雲端。")
    else:
        print("未設定 SUPABASE_DB_URL：只寫入本機 SQLite（純本機模式）。")

    print(f"登入 Garmin Connect...")
    client = get_client()
    print("登入成功。")

    print(f"抓取跑步活動：{start_date} ~ {end_date}")
    rows = fetch_activities(client, start_date, end_date)
    save_activities(conn, rows)
    print(f"  共取得 {len(rows)} 筆跑步活動。")

    print("抓取活動心率區間分布...")
    fetch_hr_zones_for_new_activities(client, conn, [r["activity_id"] for r in rows])

    print(f"抓取每日健康資料（靜息心率 / HRV / 睡眠）：{start_date} ~ {end_date}")
    fetch_daily_wellness(client, conn, start_date, end_date)

    conn.close()
    if db.using_cloud():
        print(f"完成，資料已存入本機 {DB_PATH}，並同步到 Supabase 雲端。")
    else:
        print(f"完成，資料已存入 {DB_PATH}")


if __name__ == "__main__":
    main()
