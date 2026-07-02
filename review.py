"""週復盤 / 月復盤產生器：從資料算出摘要，寫成 Markdown 進 Obsidian vault。

用法：
  python review.py --week            # 產生上一個完整週的週復盤
  python review.py --week 2026-06-25 # 產生包含該日期那一週的週復盤
  python review.py --month 2026-06   # 產生該月的跑步健康月報

寫入位置由 .env 的 OBSIDIAN_VAULT 決定（例：D:\\Secondbrain）。
- 週復盤 → {vault}/Myself/馬拉松/週復盤/YYYY-Www 週復盤.md
- 月報   → {vault}/Myself/馬拉松/{民國年}-MM 跑步健康月報.md
數字全部來自實際資料，主觀欄位留「（待你補）」不自行編寫。
"""

import argparse
import os
from datetime import date, datetime, timedelta

import pandas as pd
from dotenv import load_dotenv

import db

load_dotenv()

# 新竹馬拉松 27 週訓練計畫：週一起始日 → (第幾週, 目標週跑量km, 階段備註)
PLAN = {
    "2026-06-01": (1, 14, "基礎建立期"),
    "2026-06-08": (2, 16, "基礎建立期"),
    "2026-06-15": (3, 18, "基礎建立期"),
    "2026-06-22": (4, 14, "基礎建立期・減量恢復週"),
    "2026-06-29": (5, 20, "基礎建立期"),
    "2026-07-06": (6, 21, "基礎建立期・週末突破 10k"),
    "2026-07-13": (7, 23, "耐力建設期"),
    "2026-07-20": (8, 18, "耐力建設期・減量恢復週"),
    "2026-07-27": (9, 26, "耐力建設期"),
    "2026-08-03": (10, 27, "耐力建設期"),
    "2026-08-10": (11, 21, "耐力建設期・減量恢復週"),
    "2026-08-17": (12, 30, "耐力建設期・週末突破 16k"),
    "2026-08-24": (13, 33, "耐力建設期"),
    "2026-08-31": (14, 23, "耐力建設期・減量恢復週"),
    "2026-09-07": (15, 35, "高峰期・關鍵長跑 1"),
    "2026-09-14": (16, 38, "高峰期・關鍵長跑 2"),
    "2026-09-21": (17, 26, "高峰期・減量恢復週"),
    "2026-09-28": (18, 41, "高峰期・關鍵長跑 3"),
    "2026-10-05": (19, 44, "高峰期・關鍵長跑 4"),
    "2026-10-12": (20, 29, "高峰期・減量恢復週"),
    "2026-10-19": (21, 46, "高峰期・關鍵長跑 5"),
    "2026-10-26": (22, 48, "高峰期・賽前最長距離"),
    "2026-11-02": (23, 32, "高峰期・準備減量"),
    "2026-11-09": (24, 35, "賽前減量期"),
    "2026-11-16": (25, 26, "賽前減量期"),
    "2026-11-23": (26, 20, "賽前減量期"),
    "2026-11-30": (27, None, "賽前減量期・比賽週（12/06 新竹馬）"),
}


def fmt_pace(sec_per_km):
    if sec_per_km is None or pd.isna(sec_per_km) or sec_per_km <= 0:
        return "—"
    return f"{int(sec_per_km // 60)}'{int(sec_per_km % 60):02d}\"/km"


def _mean(series):
    v = series.mean()
    return None if pd.isna(v) else v


def _f(v, fmt="{:.0f}"):
    return fmt.format(v) if v is not None and not pd.isna(v) else "—"


def _delta(cur, prev):
    if cur is None or prev is None or pd.isna(cur) or pd.isna(prev):
        return "—"
    d = cur - prev
    if abs(d) < 0.5:
        return "持平"
    return f"{'↑' if d > 0 else '↓'}{abs(d):.0f}"


def running_stats(act, start, end):
    m = (act["date"] >= pd.Timestamp(start)) & (act["date"] <= pd.Timestamp(end))
    d = act[m]
    dist = d["distance_km"].sum()
    dur = d["duration_sec"].sum()
    return {
        "dist": dist,
        "runs": len(d),
        "pace": dur / dist if dist > 0 else None,
        "longest": d["distance_km"].max() if len(d) else 0,
    }


def health_stats(well, start, end):
    m = (well["date"] >= pd.Timestamp(start)) & (well["date"] <= pd.Timestamp(end))
    w = well[m]
    return {
        "rhr": _mean(w["resting_hr"]),
        "sleep": _mean(w["sleep_score"]),
        "stress": _mean(w["avg_stress"]),
        "bb_low": _mean(w["body_battery_low"]),
        "steps": _mean(w["total_steps"]),
    }


def weekly_markdown(act, well, monday):
    start = monday
    end = monday + timedelta(days=6)
    iso = start.isocalendar()
    tag = f"{iso[0]}-W{iso[1]:02d}"
    num, target, phase = PLAN.get(start.isoformat(), (None, None, "（訓練計畫範圍外）"))

    r = running_stats(act, start, end)
    h = health_stats(well, start, end)
    hp = health_stats(well, start - timedelta(days=7), end - timedelta(days=7))

    target_line = (
        f"目標週跑量 {target}k → 實際 **{r['dist']:.1f} km**"
        + (f"（達成 {r['dist'] / target * 100:.0f}%）" if target else "")
    )
    plan_line = f"第 {num} 週｜{phase}" if num else phase

    warn = []
    if target and r["dist"] > target * 1.15:
        warn.append(f"實際跑量 {r['dist']:.1f}k 明顯超過本週目標 {target}k，若本週是減量/恢復週要留意是否真的有恢復。")
    if target and r["dist"] < target * 0.7:
        warn.append(f"實際跑量 {r['dist']:.1f}k 低於本週目標 {target}k 不少，注意訓練是否落後。")
    if h["sleep"] is not None and hp["sleep"] is not None and h["sleep"] < hp["sleep"] - 3:
        warn.append(f"睡眠分數較上週下降（{hp['sleep']:.0f}→{h['sleep']:.0f}），注意恢復。")
    if h["rhr"] is not None and hp["rhr"] is not None and h["rhr"] > hp["rhr"] + 3:
        warn.append(f"靜息心率較上週升高（{hp['rhr']:.0f}→{h['rhr']:.0f}），可能疲勞累積。")
    if not warn:
        warn.append("數據上沒有明顯警訊，恢復狀態尚可。")

    md = f"""# {tag} 跑步週復盤（{start.strftime('%m/%d')}–{end.strftime('%m/%d')}）
> 訓練計畫：{plan_line}

## 本週跑步（實際數據）
- {target_line}
- 跑步次數：{r['runs']} 次｜平均配速：{fmt_pace(r['pace'])}｜最長單次：{r['longest']:.1f} km

## 本週健康均值
| 指標 | 本週 | 上週 | 變化 |
|---|---|---|---|
| 靜息心率 | {_f(h['rhr'])} bpm | {_f(hp['rhr'])} bpm | {_delta(h['rhr'], hp['rhr'])} |
| 睡眠分數 | {_f(h['sleep'])} | {_f(hp['sleep'])} | {_delta(h['sleep'], hp['sleep'])} |
| 平均壓力 | {_f(h['stress'])} | {_f(hp['stress'])} | {_delta(h['stress'], hp['stress'])} |
| Body Battery 最低 | {_f(h['bb_low'])} | {_f(hp['bb_low'])} | {_delta(h['bb_low'], hp['bb_low'])} |
| 平均步數 | {_f(h['steps'], '{:,.0f}')} | {_f(hp['steps'], '{:,.0f}')} | — |

## ⚠️ 觀察與身體警訊（依數據）
""" + "".join(f"- {w}\n" for w in warn) + f"""
## 每週回顧欄位（對接訓練計畫，主觀欄位待你補）
- 實際總里程：{r['dist']:.1f} km
- 最順利的一件事：（待你補）
- 身體警訊或疼痛：（待你補）
- 家庭/工作協調情形：（待你補）
- 下週調整：（待你補）

---
*本筆記由 garmin-running-dashboard/review.py 依實際資料自動產生；數字為已確認事實，主觀欄位待補。*
"""
    frontmatter = {
        "title": f"{tag} 跑步週復盤",
        "date": end.isoformat(),
        "type": "weekly-review",
        "tags": ["週復盤", "馬拉松", "跑步", "健康"],
        "related": "[[2026 新竹馬拉松訓練計畫]]",
    }
    return tag, frontmatter, md


def monthly_markdown(act, well, year, month):
    start = date(year, month, 1)
    end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
    roc = f"{year - 1911}-{month:02d}"

    r = running_stats(act, start, end)
    h = health_stats(well, start, end)

    md = f"""# {roc} 跑步健康月報（{year}-{month:02d}）

## 跑步
- 總里程：**{r['dist']:.1f} km**｜跑步次數：{r['runs']} 次
- 平均配速：{fmt_pace(r['pace'])}｜最長單次：{r['longest']:.1f} km

## 健康月均
| 指標 | 本月平均 |
|---|---|
| 靜息心率 | {_f(h['rhr'])} bpm |
| 睡眠分數 | {_f(h['sleep'])} |
| 平均壓力 | {_f(h['stress'])} |
| Body Battery 最低 | {_f(h['bb_low'])} |
| 每日步數 | {_f(h['steps'], '{:,.0f}')} |

## 本月小結（待你補）
- 亮點：（待你補）
- 卡住或警訊：（待你補）
- 下月方向：（待你補）

---
*本筆記由 review.py 依實際資料自動產生，供 [[{roc} 月覆盤]] 的「自我・身體」引用。*
"""
    frontmatter = {
        "title": f"{roc} 跑步健康月報",
        "date": end.isoformat(),
        "type": "running-health-monthly",
        "tags": ["月報", "馬拉松", "跑步", "健康"],
        "related": "[[2026 新竹馬拉松訓練計畫]]",
    }
    return roc, frontmatter, md


def write_note(rel_path, frontmatter, body):
    vault = os.getenv("OBSIDIAN_VAULT")
    if not vault:
        raise SystemExit("未設定 OBSIDIAN_VAULT（.env），無法寫入 vault。")
    full = os.path.join(vault, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    fm_lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, list):
            fm_lines.append(f"{k}: [{', '.join(v)}]")
        elif isinstance(v, str) and ("[" in v or ":" in v):
            # 含 [[wikilink]] 或冒號的字串要加引號，否則 YAML 解析失敗
            fm_lines.append(f'{k}: "{v}"')
        else:
            fm_lines.append(f"{k}: {v}")
    fm_lines.append("---\n")
    # newline="" 避免 Windows 把 \n 轉成 \r\n；Obsidian 的 frontmatter 需要 LF 才解析得到
    with open(full, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(fm_lines) + body)
    return full


def main():
    p = argparse.ArgumentParser(description="週/月復盤產生器")
    p.add_argument("--week", nargs="?", const="LAST", help="週復盤；可帶日期 YYYY-MM-DD，預設上一個完整週")
    p.add_argument("--month", help="月報，格式 YYYY-MM")
    args = p.parse_args()

    act, well, _ = db.load_tables()
    if act is None:
        raise SystemExit("找不到資料，請先執行 fetch_data.py。")

    if args.week is not None:
        if args.week == "LAST":
            today = date.today()
            monday = today - timedelta(days=today.weekday()) - timedelta(days=7)
        else:
            d = datetime.strptime(args.week, "%Y-%m-%d").date()
            monday = d - timedelta(days=d.weekday())
        tag, fm, md = weekly_markdown(act, well, monday)
        path = write_note(f"Myself/馬拉松/週復盤/{tag} 週復盤.md", fm, md)
        print(f"已產生週復盤：{path}")
    elif args.month:
        year, month = map(int, args.month.split("-"))
        roc, fm, md = monthly_markdown(act, well, year, month)
        path = write_note(f"Myself/馬拉松/{roc} 跑步健康月報.md", fm, md)
        print(f"已產生月報：{path}")
    else:
        p.error("請指定 --week 或 --month")


if __name__ == "__main__":
    main()
