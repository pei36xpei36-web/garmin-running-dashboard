"""資料存取層：偵測到 Supabase 設定就走雲端，否則走本機 SQLite。

- 部署到 Streamlit Cloud 時，會讀 st.secrets 或環境變數的 SUPABASE_DB_URL → 連 Supabase Postgres。
- 在本機沒設定 SUPABASE_DB_URL 時，沿用原本的本機 garmin_data.db。
同一份程式，本機開發與雲端部署都能跑。
"""

import os
import sqlite3

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, "garmin_data.db")

# 各表的主鍵，upsert 時用來判斷衝突
CONFLICT_KEYS = {
    "activities": ["activity_id"],
    "daily_wellness": ["date"],
    "hr_zones": ["activity_id", "zone_number"],
}

_engine = None


def _get_db_url():
    """依序找 SUPABASE_DB_URL：環境變數 → Streamlit secrets。找不到回傳 None。"""
    url = os.getenv("SUPABASE_DB_URL")
    if url:
        return url
    try:
        import streamlit as st

        if "SUPABASE_DB_URL" in st.secrets:
            return st.secrets["SUPABASE_DB_URL"]
    except Exception:
        pass
    return None


def using_cloud() -> bool:
    return _get_db_url() is not None


def get_engine():
    """有設定就回傳連 Supabase 的 SQLAlchemy engine，否則 None。"""
    global _engine
    url = _get_db_url()
    if not url:
        return None
    if _engine is None:
        from sqlalchemy import create_engine

        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def load_tables():
    """讀三張表回傳 (activities, wellness, hr_zones) DataFrame。

    沒有任何資料來源時回傳 (None, None, None)（給儀表板顯示提示用）。
    """
    engine = get_engine()
    if engine is not None:
        activities = pd.read_sql("SELECT * FROM activities", engine, parse_dates=["date"])
        wellness = pd.read_sql("SELECT * FROM daily_wellness", engine, parse_dates=["date"])
        hr_zones = pd.read_sql("SELECT * FROM hr_zones", engine)
        return activities, wellness, hr_zones

    if not os.path.exists(SQLITE_PATH):
        return None, None, None
    conn = sqlite3.connect(SQLITE_PATH)
    activities = pd.read_sql("SELECT * FROM activities", conn, parse_dates=["date"])
    wellness = pd.read_sql("SELECT * FROM daily_wellness", conn, parse_dates=["date"])
    hr_zones = pd.read_sql("SELECT * FROM hr_zones", conn)
    conn.close()
    return activities, wellness, hr_zones


def upsert_rows(table: str, rows: list[dict]) -> int:
    """把 rows（list of dict）upsert 進 Supabase。沒設定雲端或沒資料時直接跳過。

    回傳實際寫入的筆數（未設定雲端時回傳 0）。
    """
    engine = get_engine()
    if engine is None or not rows:
        return 0

    from sqlalchemy import text

    conflict_cols = CONFLICT_KEYS[table]
    cols = list(rows[0].keys())
    col_list = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    update_cols = [c for c in cols if c not in conflict_cols]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)

    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(conflict_cols)}) "
        + (f"DO UPDATE SET {set_clause}" if update_cols else "DO NOTHING")
    )

    with engine.begin() as conn:
        conn.execute(text(sql), rows)
    return len(rows)
