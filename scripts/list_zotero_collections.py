"""
列出 Zotero Library 的完整集合結構，輸出可直接貼入 custom.yaml 的路徑格式。

用法：
  uv run python scripts/list_zotero_collections.py

環境變數（或直接在腳本中填入）：
  ZOTERO_ID   — Zotero user ID（數字）
  ZOTERO_KEY  — Zotero API key

輸出範例：
  [tree]
  2025/
  ├── NLP/
  │   ├── survey/          → include_path: "2025/NLP/survey/**"
  │   └── reading-group/   → include_path: "2025/NLP/reading-group/**"
  └── Neuroscience/        → include_path: "2025/Neuroscience/**"
  archive/                 → ignore_path:  "archive/**"
"""

import os
import sys
from pyzotero import zotero


def build_tree(collections: dict) -> dict:
    """把 Zotero collection 清單組成以 key 為索引的樹狀結構。"""
    tree = {}
    for key, col in collections.items():
        parent = col["data"].get("parentCollection", None)
        if parent is False:
            parent = None
        if parent not in tree:
            tree[parent] = []
        tree[parent].append(key)
    return tree


def get_path(key: str, collections: dict) -> str:
    """遞迴組出集合的完整路徑（與 executor.py 的邏輯一致）。"""
    col = collections[key]
    parent = col["data"].get("parentCollection", None)
    if parent is False:
        parent = None
    name = col["data"]["name"]
    if parent and parent in collections:
        return get_path(parent, collections) + "/" + name
    return name


def print_tree(
    node: str | None,
    tree: dict,
    collections: dict,
    prefix: str = "",
    is_last: bool = True,
    depth: int = 0,
    paths: list = None,
):
    if paths is None:
        paths = []

    children = sorted(
        tree.get(node, []),
        key=lambda k: collections[k]["data"]["name"].lower()
    )
    for i, key in enumerate(children):
        last = i == len(children) - 1
        connector = "└── " if last else "├── "
        name = collections[key]["data"]["name"]
        full_path = get_path(key, collections)
        has_children = key in tree

        # 輸出樹狀行
        suffix = "/" if has_children else ""
        print(f"{prefix}{connector}{name}{suffix}")

        # 記錄路徑供後續列印
        paths.append(full_path)

        # 遞迴子節點
        child_prefix = prefix + ("    " if last else "│   ")
        print_tree(key, tree, collections, child_prefix, last, depth + 1, paths)

    return paths


def main():
    user_id = os.environ.get("ZOTERO_ID", "").strip()
    api_key  = os.environ.get("ZOTERO_KEY", "").strip()

    if not user_id or not api_key:
        print("錯誤：請設定環境變數 ZOTERO_ID 與 ZOTERO_KEY")
        print("  Windows PowerShell:")
        print("    $env:ZOTERO_ID='你的數字ID'; $env:ZOTERO_KEY='你的APIkey'")
        print("  或直接在腳本中填入。")
        sys.exit(1)

    print("連線 Zotero...")
    zot = zotero.Zotero(user_id, "user", api_key)
    raw = zot.everything(zot.collections())
    collections = {c["key"]: c for c in raw}
    tree = build_tree(collections)

    print(f"\n共 {len(collections)} 個集合\n")
    print("=" * 60)
    print("集合樹狀結構")
    print("=" * 60)
    all_paths = print_tree(None, tree, collections)

    print("\n" + "=" * 60)
    print("可用於 custom.yaml 的路徑清單（按字母排序）")
    print("=" * 60)
    all_paths_sorted = sorted(set(all_paths))
    for p in all_paths_sorted:
        # 有子集合的路徑建議用 **，葉節點直接用路徑
        in_tree = any(
            get_path(k, collections).startswith(p + "/")
            for k in collections
        )
        glob_hint = f"{p}/**" if in_tree else p
        print(f"  {glob_hint}")

    print("\n" + "=" * 60)
    print("custom.yaml 設定範例")
    print("=" * 60)
    print("""
zotero:
  include_path:
    - "2025/**"          # 只比對 2025 年的集合
    - "reading-group/**" # 以及 reading-group 下所有文獻
  ignore_path:
    - "archive/**"       # 排除 archive 集合
""")


if __name__ == "__main__":
    main()
