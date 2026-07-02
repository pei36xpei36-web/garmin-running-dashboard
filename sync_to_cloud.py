"""把本機 garmin_data.db 現有資料一次推送到 Supabase 雲端。

用途：
- 第一次設定雲端後，把已經抓好的歷史資料上傳（不需重連 Garmin，不會被限流）。
- 之後若雲端資料遺失，也可用本機資料重新同步。

需先在 .env 設好 SUPABASE_DB_URL。執行：python sync_to_cloud.py
"""

import os
import sqlite3

from dotenv import load_dotenv

import db

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "garmin_data.db")


def main():
    if not db.using_cloud():
        raise SystemExit("未設定 SUPABASE_DB_URL，請先在 .env 填入 Supabase 連線字串。")
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"找不到本機資料庫 {DB_PATH}，請先執行 fetch_data.py。")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    total = 0
    for table in ("activities", "daily_wellness", "hr_zones"):
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
        n = db.upsert_rows(table, rows)
        print(f"  {table}: 推送 {n} 筆")
        total += n

    conn.close()
    print(f"完成，共同步 {total} 筆到 Supabase。")


if __name__ == "__main__":
    main()
