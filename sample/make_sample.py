"""Generate a sample Excel file with synthetic game-industry data
to demonstrate the tool. Run once: python sample/make_sample.py
"""

import os
import random

import pandas as pd


GAMES = ["原神", "王者荣耀", "蛋仔派对", "和平精英", "崩坏：星穹铁道", "金铲铲之战"]
GENRES = ["开放世界RPG", "MOBA", "派对游戏", "战术竞技", "卡牌策略"]
COUNTRIES = ["中国", "日本", "美国", "韩国", "德国", "法国"]


def make() -> str:
    random.seed(42)
    rows = []
    for _ in range(40):
        rows.append(
            {
                "日期": f"2026-{random.randint(1, 5):02d}-{random.randint(1, 28):02d}",
                "游戏名称": random.choice(GAMES),
                "国家": random.choice(COUNTRIES),
                "玩法": random.choice(GENRES),
                "DAU": random.randint(10_000, 5_000_000),
                "收入(万)": round(random.uniform(50, 5000), 1),
                "备注": "",
            }
        )
    df = pd.DataFrame(rows)
    out_dir = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(out_dir, "sample_game_data.xlsx")
    df.to_excel(out, index=False)
    print(f"Wrote {out}")
    return out


if __name__ == "__main__":
    make()
