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

    print(f"\nKept workdir for inspection: {workdir}")


if __name__ == "__main__":
    main()
