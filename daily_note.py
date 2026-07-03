"""把某天的 Garmin 健康數據寫進 Obsidian 每日筆記。

- 在筆記裡插入一段「標記包起來」的自動區塊，重跑只更新那塊，不動你手寫的內容。
- 若當天有跑步，會把「運動：… ⬜ 跑步」那格打勾（⬜→✅），只勾不取消。

用法：
  python daily_note.py            # 更新今天
  python daily_note.py 2026-07-01 # 更新指定日期
需在 .env 設 OBSIDIAN_VAULT。每日筆記路徑：{vault}/每日筆記/YYYY-MM-DD.md
"""

import os
import sys
from datetime import date

import pandas as pd
from dotenv import load_dotenv

import db

load_dotenv()

START = "<!-- garmin-auto:start -->"
END = "<!-- garmin-auto:end -->"


def fmt_pace(sec_per_km):
    if sec_per_km is None or pd.isna(sec_per_km) or sec_per_km <= 0:
        return "—"
    return f"{int(sec_per_km // 60)}'{int(sec_per_km % 60):02d}\"/km"


def build_block(act, well, day, nl):
    ts = pd.Timestamp(day)
    runs = act[act["date"] == ts]
    w = well[well["date"] == ts]

    def g(col):
        if len(w) and pd.notna(w.iloc[0].get(col)):
            return w.iloc[0][col]
        return None

    steps, goal = g("total_steps"), g("step_goal")
    sleep, rhr = g("sleep_score"), g("resting_hr")

    if len(runs):
        dist = runs["distance_km"].sum()
        dur = runs["duration_sec"].sum()
        run_line = f"跑步：✅ {dist:.1f} km・配速 {fmt_pace(dur / dist if dist else None)}"
    else:
        run_line = "跑步：今日無跑步紀錄"

    steps_txt = f"{steps:,.0f}" if steps is not None else "—"
    goal_txt = f"／目標 {goal:,.0f}" if goal is not None else ""
    sleep_txt = f"{sleep:.0f}" if sleep is not None else "—"
    rhr_txt = f"{rhr:.0f} bpm" if rhr is not None else "—"
    lines = [
        START,
        "",
        "> [!info]+ 🏃 Garmin 今日數據（自動更新）",
        f"> 步數 {steps_txt}{goal_txt} ｜ 睡眠分數 {sleep_txt} ｜ 靜息心率 {rhr_txt}",
        f"> {run_line}",
        END,
    ]
    return nl.join(lines), len(runs) > 0


def upsert_block(content, block, ran, nl):
    # 1) 有跑步 → 把「⬜ 跑步」勾成「✅ 跑步」（只勾不取消）
    if ran and "⬜ 跑步" in content:
        content = content.replace("⬜ 跑步", "✅ 跑步")

    # 2) 已有自動區塊 → 取代；否則插入
    if START in content and END in content:
        pre = content.split(START)[0]
        post = content.split(END, 1)[1]
        return pre + block + post

    # 插在「運動：」那一行之後，對接你的「自我 Myself」區塊
    lines = content.split(nl)
    out = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and "運動：" in line:
            out.append(block)
            inserted = True
    if not inserted:
        out.append(nl + block)
    return nl.join(out)


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    vault = os.getenv("OBSIDIAN_VAULT")
    if not vault:
        raise SystemExit("未設定 OBSIDIAN_VAULT（.env）。")
    note_path = os.path.join(vault, "每日筆記", f"{day}.md")

    act, well, _ = db.load_tables()
    if act is None:
        raise SystemExit("找不到資料，請先執行 fetch_data.py。")

    if os.path.exists(note_path):
        with open(note_path, "r", encoding="utf-8", newline="") as f:
            content = f.read()
        nl = "\r\n" if "\r\n" in content else "\n"
    else:
        # 沒有當天筆記就建一個很簡單的（正常情況你的每日筆記模板已先建好）
        nl = "\r\n"
        content = f"# {day} 每日筆記{nl}"

    block, ran = build_block(act, well, day, nl)
    new_content = upsert_block(content, block, ran, nl)

    os.makedirs(os.path.dirname(note_path), exist_ok=True)
    with open(note_path, "w", encoding="utf-8", newline="") as f:
        f.write(new_content)
    print(f"已更新每日筆記：{note_path}（{'有跑步已打勾' if ran else '無跑步'}）")


if __name__ == "__main__":
    main()
