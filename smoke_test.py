"""End-to-end smoke test of the core modules.
Creates a sample CSV, encrypts it, simulates AI editing, decrypts, verifies.
"""

import os
import shutil
import sys
import tempfile

import pandas as pd

# Make project importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.project import Project
from core.encrypt import encrypt_file
from core.decrypt import decrypt_file


def main():
    workdir = tempfile.mkdtemp(prefix="enigma_smoke_")
    print(f"Working in: {workdir}\n")

    # 1. Build a sample CSV
    sample = pd.DataFrame(
        {
            "游戏名称": ["原神", "王者荣耀", "原神", "蛋仔派对", "王者荣耀"],
            "国家": ["日本", "中国", "美国", "中国", "日本"],
            "玩法": ["开放世界RPG", "MOBA", "开放世界RPG", "派对游戏", "MOBA"],
            "收入": [1200, 800, 1500, 600, 950],
        }
    )
    src = os.path.join(workdir, "sales.csv")
    sample.to_csv(src, index=False, encoding="utf-8-sig")
    print("Original data:")
    print(sample.to_string(index=False))
    print()

    # 2. Create project
    keyfile = os.path.join(workdir, "myproject.keyfile")
    proj = Project.create(keyfile, name="smoke", password="hunter2")

    # 3. Encrypt
    result = encrypt_file(
        proj,
        input_path=src,
        column_prefix_map={"游戏名称": "GAME", "玩法": "TYPE"},
    )
    print(f"Encrypted -> {result.output_path}")
    print(f"  rows: {result.rows_affected}")
    print(f"  new tokens: {result.new_tokens_per_prefix}")
    print(f"  total tokens: {result.total_tokens_per_prefix}")
    print()
    encrypted_df = pd.read_csv(result.output_path, encoding="utf-8-sig")
    print("Encrypted data:")
    print(encrypted_df.to_string(index=False))
    print()
    print("Generated prompt:")
    print(result.prompt_template)
    print()

    # 4. Simulate "AI processing": edit the encrypted file
    # AI lowercases one token, adds a hallucinated new token, leaves others alone.
    ai_processed = encrypted_df.copy()
    # AI made a column "AI建议" mentioning tokens with various corruptions
    ai_processed["AI建议"] = [
        f"用户喜欢 {ai_processed['游戏名称'][0].lower()} 这种游戏",  # lowercased
        f'"{ai_processed["游戏名称"][1]}" 是 MOBA 龙头',  # quoted
        f"建议推出 GAME_FAKE 类似产品",  # hallucinated
        f"{ai_processed['玩法'][3]}.目前增长快",  # trailing punctuation
        "无特别建议",
    ]
    ai_path = os.path.join(workdir, "sales_ai.csv")
    ai_processed.to_csv(ai_path, index=False, encoding="utf-8-sig")
    print("AI-processed data:")
    print(ai_processed.to_string(index=False))
    print()

    # 5. Reopen project (simulates closing the app)
    proj2 = Project.open(keyfile, password="hunter2")
    decrypted = decrypt_file(proj2, ai_path)
    print(f"Decrypted -> {decrypted.output_path}")
    print(f"  rows: {decrypted.rows_processed}")
    print(f"  tokens restored: {decrypted.tokens_restored}")
    print(f"  unknown tokens: {decrypted.unknown_tokens}")
    print(f"  columns touched: {decrypted.columns_touched}")
    print()
    final_df = pd.read_csv(decrypted.output_path, encoding="utf-8-sig")
    print("Final restored data:")
    print(final_df.to_string(index=False))
    print()

    # 6. Assertions
    # Original two columns should be fully restored.
    assert list(final_df["游戏名称"]) == list(sample["游戏名称"]), \
        f"游戏名称 mismatch: {list(final_df['游戏名称'])}"
    assert list(final_df["玩法"]) == list(sample["玩法"]), \
        f"玩法 mismatch: {list(final_df['玩法'])}"
    # Numeric/non-encrypted columns untouched.
    assert list(final_df["国家"]) == list(sample["国家"])
    assert list(final_df["收入"]) == list(sample["收入"])
    # AI-added text should have its tokens restored.
    assert "原神" in final_df["AI建议"].iloc[0], final_df["AI建议"].iloc[0]
    assert "王者荣耀" in final_df["AI建议"].iloc[1], final_df["AI建议"].iloc[1]
    # The hallucinated token should be marked.
    assert "[⚠未识别]" in final_df["AI建议"].iloc[2], final_df["AI建议"].iloc[2]
    # Trailing punctuation case
    assert "派对游戏" in final_df["AI建议"].iloc[3], final_df["AI建议"].iloc[3]
    print("✓ All assertions passed!")

    # 7. Wrong password should fail
    try:
        Project.open(keyfile, password="wrong")
    except Exception as e:
        print(f"✓ Wrong password correctly rejected: {type(e).__name__}")
    else:
        raise AssertionError("Wrong password was accepted!")

    # 8. Multi-sheet same-column-name regression test
    #    A common real-world case: an .xlsx with sheets "2021", "2023", "2024",
    #    each with column "游戏名". Same game name across sheets must get the
    #    SAME token (project-level consistency).
    print("\n--- Multi-sheet regression test ---")
    multi_src = os.path.join(workdir, "annual.xlsx")
    with pd.ExcelWriter(multi_src) as w:
        pd.DataFrame({"游戏名": ["原神", "王者荣耀"], "流水": [100, 200]}).to_excel(
            w, sheet_name="2021", index=False
        )
        pd.DataFrame({"游戏名": ["原神", "蛋仔派对"], "流水": [150, 80]}).to_excel(
            w, sheet_name="2023", index=False
        )
        pd.DataFrame({"游戏名": ["王者荣耀", "原神"], "流水": [300, 250]}).to_excel(
            w, sheet_name="2024", index=False
        )
    proj_multi = Project.open(keyfile, password="hunter2")
    multi_result = encrypt_file(proj_multi, multi_src, {"游戏名": "GAME"})
    out_sheets = pd.read_excel(multi_result.output_path, sheet_name=None)
    # Same name -> same token across all sheets
    tok_yuanshen = out_sheets["2021"].iloc[0]["游戏名"]  # 原神 in row 0 of 2021
    assert out_sheets["2023"].iloc[0]["游戏名"] == tok_yuanshen, "原神 token mismatch across 2021/2023"
    assert out_sheets["2024"].iloc[1]["游戏名"] == tok_yuanshen, "原神 token mismatch across 2021/2024"
    # 流水 column should be untouched (numeric, not in column_prefix_map)
    assert list(out_sheets["2021"]["流水"]) == [100, 200]
    print(f"✓ Multi-sheet: 原神 -> {tok_yuanshen} consistent across all 3 sheets")

    # 9. Numeric affine transform + date offset regression test
    print("\n--- Numeric affine + date offset round-trip test ---")
    affine_src = os.path.join(workdir, "with_dates.xlsx")
    df_affine = pd.DataFrame({
        "日期": pd.to_datetime(["2024-01-15", "2024-02-20", "2024-03-25"]),
        "游戏名": ["原神", "王者荣耀", "蛋仔派对"],
        "流水": [1200.0, 800.5, 600.25],
        "DAU": [50000, 80000, 30000],
    })
    df_affine.to_excel(affine_src, index=False)

    affine_kf = os.path.join(workdir, "affine.keyfile")
    proj_affine = Project.create(affine_kf, name="affine_test", password="password123")
    # Verify a non-zero date offset was generated at creation
    assert proj_affine.date_offset_days != 0, "New project should auto-generate non-zero date offset"
    print(f"  date offset: {proj_affine.date_offset_days} days")

    affine_res = encrypt_file(
        proj_affine,
        affine_src,
        column_prefix_map={"游戏名": "GAME"},
        numeric_columns=["流水", "DAU"],
    )
    enc_df = pd.read_excel(affine_res.output_path)
    # Verify dates were shifted
    expected_shift = pd.Timedelta(days=proj_affine.date_offset_days)
    assert (enc_df["日期"][0] - df_affine["日期"][0]) == expected_shift, "Date not shifted"
    # Verify numeric columns were transformed (not equal to originals)
    assert not (enc_df["流水"] == df_affine["流水"]).all(), "流水 should be transformed"
    assert not (enc_df["DAU"] == df_affine["DAU"]).all(), "DAU should be transformed"
    # Verify game names were tokenized
    assert all(str(v).startswith("GAME_") for v in enc_df["游戏名"]), "游戏名 should be tokenized"
    print(f"  Sample row encrypted: 流水={enc_df['流水'][0]:.2f}, DAU={enc_df['DAU'][0]:.2f}, "
          f"日期={enc_df['日期'][0].date()}")
    print(f"  Generated prompt:\n{affine_res.prompt_template[:300]}...")

    # Now decrypt and verify exact recovery
    proj_affine2 = Project.open(affine_kf, password="password123")
    dec_res = decrypt_file(proj_affine2, affine_res.output_path)
    rest_df = pd.read_excel(dec_res.output_path)
    # Game names back to originals
    assert list(rest_df["游戏名"]) == list(df_affine["游戏名"]), \
        f"游戏名 not restored: {list(rest_df['游戏名'])}"
    # Numeric columns back to originals (within float precision)
    for col in ["流水", "DAU"]:
        for orig, rest in zip(df_affine[col], rest_df[col]):
            assert abs(orig - rest) < 1e-6, f"{col}: {orig} != {rest}"
    # Dates back to originals
    for orig, rest in zip(df_affine["日期"], rest_df["日期"]):
        assert orig == rest, f"date: {orig} != {rest}"
    print(f"  ✓ Affine + date round-trip: all values match (流水, DAU, 日期, 游戏名)")
    print(f"  Sample restored: 流水={rest_df['流水'][0]}, DAU={rest_df['DAU'][0]}, "
          f"日期={rest_df['日期'][0].date()}")

    print(f"\nKept workdir for inspection: {workdir}")


if __name__ == "__main__":
    main()
