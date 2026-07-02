"""跑步資料總覽儀表板（Streamlit）。

資料來源由 db.py 決定：部署到雲端讀 Supabase，本機開發讀 garmin_data.db。
"""

import os
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

import db

load_dotenv()

# (目標距離公里, 名稱, 容許的實際距離範圍倍率)
PB_TARGETS = [
    (1.0, "1K", 0.90, 1.15),
    (5.0, "5K", 0.95, 1.08),
    (10.0, "10K", 0.95, 1.06),
    (21.0975, "半馬", 0.97, 1.03),
    (42.195, "全馬", 0.98, 1.02),
]

ZONE_LABELS = {1: "Z1", 2: "Z2", 3: "Z3", 4: "Z4", 5: "Z5"}


def format_pace(sec_per_km):
    if sec_per_km is None or pd.isna(sec_per_km) or sec_per_km <= 0:
        return "—"
    minutes = int(sec_per_km // 60)
    seconds = int(sec_per_km % 60)
    return f"{minutes}'{seconds:02d}\"/km"


def format_duration(sec):
    if sec is None or pd.isna(sec):
        return "—"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


@st.cache_data(ttl=300)
def load_data():
    return db.load_tables()


def require_login():
    """密碼登入閘門。設定了 APP_PASSWORD（st.secrets 或環境變數）才啟用；
    本機開發沒設定時直接放行。"""
    password = None
    try:
        password = st.secrets.get("APP_PASSWORD")
    except Exception:
        password = None
    password = password or os.getenv("APP_PASSWORD")

    if not password:
        return  # 沒設定密碼 → 本機開發模式，直接放行

    if st.session_state.get("authenticated"):
        return

    st.title("跑步資料總覽")
    entered = st.text_input("請輸入密碼", type="password")
    if not entered:
        st.stop()
    if entered == password:
        st.session_state["authenticated"] = True
        st.rerun()
    else:
        st.error("密碼錯誤，請重試。")
        st.stop()


def compute_personal_bests(activities: pd.DataFrame) -> dict:
    bests = {}
    for target_km, label, low, high in PB_TARGETS:
        candidates = activities[
            (activities["distance_km"] >= target_km * low)
            & (activities["distance_km"] <= target_km * high)
        ]
        if candidates.empty:
            bests[label] = None
            continue
        best_row = candidates.loc[candidates["duration_sec"].idxmin()]
        bests[label] = best_row["duration_sec"]
    return bests


def weekly_volume_chart(activities: pd.DataFrame):
    df = activities.copy()
    iso = df["date"].dt.isocalendar()
    df["year_week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    df["week_start"] = df["date"] - pd.to_timedelta(iso["day"] - 1, unit="D")

    weekly = df.groupby(["year_week", "week_start"], as_index=False)["distance_km"].sum()
    weekly = weekly.sort_values("week_start").tail(12)

    fig = px.bar(
        weekly, x="week_start", y="distance_km",
        labels={"week_start": "週別", "distance_km": "距離 (km)"},
        title="週跑量趨勢（近 12 週）",
    )
    fig.update_traces(hovertemplate="週起始 %{x|%Y-%m-%d}<br>%{y:.1f} km")
    st.plotly_chart(fig, use_container_width=True)


def pace_trend_chart(activities: pd.DataFrame):
    cutoff = pd.Timestamp(date.today() - timedelta(days=90))
    df = activities[activities["date"] >= cutoff].sort_values("date")
    df = df[df["avg_pace_sec_per_km"].notna()]

    if df.empty:
        st.info("近 3 個月沒有配速資料。")
        return

    fig = px.line(
        df, x="date", y="avg_pace_sec_per_km", markers=True,
        labels={"date": "日期", "avg_pace_sec_per_km": "配速"},
        title="配速趨勢（近 3 個月）",
    )
    fig.update_yaxes(autorange="reversed")

    y_min, y_max = df["avg_pace_sec_per_km"].min(), df["avg_pace_sec_per_km"].max()
    tick_step = max(int((y_max - y_min) / 5), 10)
    tickvals = list(range(int(y_min), int(y_max) + tick_step, tick_step)) or [int(y_min)]
    fig.update_yaxes(tickvals=tickvals, ticktext=[format_pace(v) for v in tickvals])
    fig.update_traces(hovertemplate="%{x|%Y-%m-%d}<br>%{text}", text=[format_pace(v) for v in df["avg_pace_sec_per_km"]])

    st.plotly_chart(fig, use_container_width=True)


def hr_zone_chart(hr_zones: pd.DataFrame):
    if hr_zones.empty:
        st.info("目前沒有心率區間資料（需先執行 fetch_data.py 抓取活動的心率區間）。")
        return

    by_zone = hr_zones.groupby("zone_number", as_index=False)["seconds_in_zone"].sum()
    by_zone["zone_label"] = by_zone["zone_number"].map(ZONE_LABELS).fillna("其他")
    by_zone = by_zone.sort_values("zone_number")

    fig = px.pie(
        by_zone, names="zone_label", values="seconds_in_zone",
        title="心率區間分布（全部活動累計）",
    )
    st.plotly_chart(fig, use_container_width=True)


def personal_best_cards(activities: pd.DataFrame):
    bests = compute_personal_bests(activities)
    cols = st.columns(len(bests))
    for col, (label, sec) in zip(cols, bests.items()):
        col.metric(label, format_duration(sec) if sec else "尚無資料")
    st.caption("個人最佳成績為近似值：取符合該距離區間內時間最短的一次活動。")


def recent_week_summary(activities: pd.DataFrame):
    cutoff = pd.Timestamp(date.today() - timedelta(days=6))
    df = activities[activities["date"] >= cutoff]

    total_distance = df["distance_km"].sum()
    avg_hr = df["avg_hr"].mean()
    total_duration = df["duration_sec"].sum()
    avg_pace = total_duration / total_distance if total_distance > 0 else None

    col1, col2, col3 = st.columns(3)
    col1.metric("近 7 天跑量", f"{total_distance:.1f} km")
    col2.metric("平均心率", f"{avg_hr:.0f} bpm" if pd.notna(avg_hr) else "—")
    col3.metric("平均配速", format_pace(avg_pace))


def main():
    st.set_page_config(page_title="跑步資料總覽", layout="wide")

    require_login()

    st.title("跑步資料總覽")

    activities, wellness, hr_zones = load_data()

    if activities is None:
        st.warning("找不到資料，請先執行 `python fetch_data.py` 抓取資料。")
        return

    if activities.empty:
        st.warning("資料庫中還沒有跑步活動，請先執行 `python fetch_data.py` 抓取資料。")
        return

    st.header("最近 7 天摘要")
    recent_week_summary(activities)

    st.header("個人最佳成績")
    personal_best_cards(activities)

    col_left, col_right = st.columns(2)
    with col_left:
        weekly_volume_chart(activities)
    with col_right:
        pace_trend_chart(activities)

    hr_zone_chart(hr_zones)


if __name__ == "__main__":
    main()
