"""健康與跑步資料儀表板（Streamlit，多分頁）。

資料來源由 db.py 決定：部署到雲端讀 Supabase，本機開發讀 garmin_data.db。
分頁：每日健康總覽 / 跑步。
"""

import os
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
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


# ---------- 共用工具 ----------

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
    """密碼登入閘門。設定了 APP_PASSWORD 才啟用；本機開發沒設定時直接放行。"""
    password = None
    try:
        password = st.secrets.get("APP_PASSWORD")
    except Exception:
        password = None
    password = password or os.getenv("APP_PASSWORD")

    if not password:
        return
    if st.session_state.get("authenticated"):
        return

    st.title("健康與跑步儀表板")
    entered = st.text_input("請輸入密碼", type="password")
    if not entered:
        st.stop()
    if entered == password:
        st.session_state["authenticated"] = True
        st.rerun()
    else:
        st.error("密碼錯誤，請重試。")
        st.stop()


def trend_line(df, col, title, y_label, recent_days=90, as_bar=False):
    cutoff = pd.Timestamp(date.today() - timedelta(days=recent_days))
    d = df[(df["date"] >= cutoff) & df[col].notna()].sort_values("date")
    if d.empty:
        st.info(f"{title}：近 {recent_days} 天沒有資料。")
        return
    kwargs = dict(x="date", y=col, labels={"date": "日期", col: y_label}, title=title)
    fig = px.bar(d, **kwargs) if as_bar else px.line(d, markers=True, **kwargs)
    st.plotly_chart(fig, use_container_width=True)


# ---------- 每日健康總覽頁 ----------

def _latest_health(wellness: pd.DataFrame):
    d = wellness[wellness["total_steps"].notna()].sort_values("date")
    return d.iloc[-1] if not d.empty else None


def health_snapshot(wellness: pd.DataFrame):
    row = _latest_health(wellness)
    if row is None:
        st.info("目前沒有每日健康資料，請執行 `python fetch_data.py --update` 抓取。")
        return
    st.caption(f"最新資料日期：{pd.to_datetime(row['date']).date()}")

    def val(x, fmt="{:.0f}"):
        return fmt.format(x) if pd.notna(x) else "—"

    c1, c2, c3, c4, c5 = st.columns(5)
    steps, goal = row.get("total_steps"), row.get("step_goal")
    delta = f"{steps - goal:+,.0f} vs 目標" if pd.notna(steps) and pd.notna(goal) else None
    c1.metric("步數", f"{steps:,.0f}" if pd.notna(steps) else "—", delta)
    c2.metric("靜息心率", val(row.get("resting_hr"), "{:.0f} bpm"))
    c3.metric("睡眠分數", val(row.get("sleep_score")))
    c4.metric("Body Battery 高/低", f"{val(row.get('body_battery_high'))} / {val(row.get('body_battery_low'))}")
    c5.metric("平均壓力", val(row.get("avg_stress")))


HEALTH_TABLE_COLS = {
    "date": "日期",
    "total_steps": "步數",
    "resting_hr": "靜息心率",
    "min_hr": "最低心率",
    "max_hr": "最高心率",
    "sleep_score": "睡眠分數",
    "avg_stress": "平均壓力",
    "max_stress": "最高壓力",
    "body_battery_high": "BB最高",
    "body_battery_low": "BB最低",
    "hrv_last_night_avg": "HRV",
    "total_calories": "卡路里",
}


def health_table(wellness: pd.DataFrame):
    df = wellness.copy().sort_values("date", ascending=False)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df[[c for c in HEALTH_TABLE_COLS if c in df.columns]].rename(columns=HEALTH_TABLE_COLS)

    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button(
        "下載 CSV",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name="每日健康數據.csv",
        mime="text/csv",
    )


def health_page():
    st.title("每日健康總覽")
    _, wellness, _ = load_data()
    if wellness is None or wellness.empty:
        st.warning("找不到健康資料，請先執行 `python fetch_data.py` 抓取。")
        return

    st.header("今日快照")
    health_snapshot(wellness)

    st.header("趨勢（近 90 天）")
    col_left, col_right = st.columns(2)
    with col_left:
        trend_line(wellness, "total_steps", "每日步數", "步數", as_bar=True)
        trend_line(wellness, "sleep_score", "睡眠分數", "分數")
        trend_line(wellness, "avg_stress", "平均壓力", "壓力")
    with col_right:
        trend_line(wellness, "resting_hr", "靜息心率", "bpm")
        trend_line(wellness, "body_battery_high", "Body Battery 最高", "Body Battery")
        trend_line(wellness, "body_battery_low", "Body Battery 最低", "Body Battery")

    st.header("每日數據清單")
    st.caption("可點欄位標題排序，或按下方按鈕下載 CSV。")
    health_table(wellness)


# ---------- 跑步頁 ----------

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
    df["week_start"] = df["date"] - pd.to_timedelta(iso["day"] - 1, unit="D")
    weekly = df.groupby("week_start", as_index=False)["distance_km"].sum()
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
    fig.update_traces(hovertemplate="%{x|%Y-%m-%d}<br>%{text}",
                      text=[format_pace(v) for v in df["avg_pace_sec_per_km"]])
    st.plotly_chart(fig, use_container_width=True)


def hr_zone_chart(hr_zones: pd.DataFrame):
    if hr_zones.empty:
        st.info("目前沒有心率區間資料。")
        return
    by_zone = hr_zones.groupby("zone_number", as_index=False)["seconds_in_zone"].sum()
    by_zone["zone_label"] = by_zone["zone_number"].map(ZONE_LABELS).fillna("其他")
    by_zone = by_zone.sort_values("zone_number")
    fig = px.pie(by_zone, names="zone_label", values="seconds_in_zone",
                 title="心率區間分布（全部活動累計）")
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

    c1, c2, c3 = st.columns(3)
    c1.metric("近 7 天跑量", f"{total_distance:.1f} km")
    c2.metric("平均心率", f"{avg_hr:.0f} bpm" if pd.notna(avg_hr) else "—")
    c3.metric("平均配速", format_pace(avg_pace))


def running_page():
    st.title("跑步")
    activities, _, hr_zones = load_data()
    if activities is None or activities.empty:
        st.warning("資料庫中還沒有跑步活動，請先執行 `python fetch_data.py` 抓取。")
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


# ---------- 入口 ----------

def main():
    st.set_page_config(page_title="健康與跑步儀表板", layout="wide")
    require_login()
    nav = st.navigation([
        st.Page(health_page, title="每日健康總覽", icon="📊"),
        st.Page(running_page, title="跑步", icon="🏃"),
    ])
    nav.run()


if __name__ == "__main__":
    main()
