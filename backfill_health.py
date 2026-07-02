"""回補既有日期的健康資料（步數 / 壓力 / Body Battery / 每日 min-max 心率 / 卡路里）。

daily_wellness 原本只有靜息心率/HRV/睡眠，新增健康欄位後，用這支把歷史日期補齊。
逐日呼叫 get_stats，更新本機 SQLite 與 Supabase。執行：python backfill_health.py
"""

import sqlite3
import time

from dotenv import load_dotenv

import db
import fetch_data

load_dotenv()

NEW_COLS = list(fetch_data.STATS_FIELD_MAP)


def main():
    conn = sqlite3.connect(fetch_data.DB_PATH)
    fetch_data.init_db(conn)  # 確保新欄位存在

    dates = [r[0] for r in conn.execute("SELECT date FROM daily_wellness ORDER BY date").fetchall()]
    if not dates:
        raise SystemExit("daily_wellness 沒有資料，請先執行 fetch_data.py。")

    print(f"準備回補 {len(dates)} 天（{dates[0]} ~ {dates[-1]}）...")
    if db.using_cloud():
        print("同時會同步到 Supabase 雲端。")

    client = fetch_data.get_client()
    print("登入成功，開始逐日回補...")

    set_clause = ", ".join(f"{c} = :{c}" for c in NEW_COLS)
    done = 0
    for iso in dates:
        stats = fetch_data.fetch_daily_stats(client, iso)
        row = {"date": iso, **stats}
        conn.execute(f"UPDATE daily_wellness SET {set_clause} WHERE date = :date", row)
        conn.commit()
        db.upsert_rows("daily_wellness", [row])
        done += 1
        if done % 30 == 0 or done == len(dates):
            print(f"  已回補 {done}/{len(dates)}（最新 {iso}：步數={stats.get('total_steps')}）")
        time.sleep(fetch_data.WELLNESS_REQUEST_DELAY)

    conn.close()
    print(f"完成，共回補 {done} 天的健康資料。")


if __name__ == "__main__":
    main()
