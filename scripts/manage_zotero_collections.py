"""
Zotero 集合管理工具

用法：
  uv run python scripts/manage_zotero_collections.py <指令> [選項]

指令：
  list                      列出集合樹狀結構
  rename <舊路徑> <新名稱>  重新命名集合
  move   <集合路徑> <新父路徑|ROOT>  移動集合
  create <名稱> [父路徑]   建立新集合
  items  <集合路徑>         列出集合內文獻數
  typos                     偵測並批次修正集合名稱錯字

環境變數：
  ZOTERO_ID   Zotero user ID
  ZOTERO_KEY  Zotero API key

範例：
  uv run python scripts/manage_zotero_collections.py list
  uv run python scripts/manage_zotero_collections.py rename "Programs_Reference/side_consciousness/Neutral and Artificial General Intellegence" "Neural and Artificial General Intelligence"
  uv run python scripts/manage_zotero_collections.py move "Programs_Reference/side_consciousness/AGI" "Programs_Reference/side_consciousness/Neural and Artificial General Intelligence"
  uv run python scripts/manage_zotero_collections.py create "2026" ROOT
  uv run python scripts/manage_zotero_collections.py typos
"""

import os
import sys
import argparse
from pyzotero import zotero


# ── 常見錯字對照表（基於你的實際集合名稱）──────────────────────────
TYPO_MAP = {
    "AI assisst":                              "AI assist",
    "Cultural Evalution":                      "Cultural Evolution",
    "Neutral and Artificial General Intellegence": "Neural and Artificial General Intelligence",
    "Readibility":                             "Readability",
    "Cog_Linguist":                            "Cog_Linguistics",
    "Lxicon_Nature":                           "Lexicon_Nature",
    "Neuropsyh":                               "Neuropsych",
    "Orientation across langauges":            "Orientation across languages",
}


# ── 共用函式 ─────────────────────────────────────────────────────────

def connect() -> zotero.Zotero:
    user_id = os.environ.get("ZOTERO_ID", "").strip()
    api_key  = os.environ.get("ZOTERO_KEY", "").strip()
    if not user_id or not api_key:
        print("錯誤：請設定 ZOTERO_ID 與 ZOTERO_KEY 環境變數")
        sys.exit(1)
    return zotero.Zotero(user_id, "user", api_key)


def load_collections(zot: zotero.Zotero) -> dict:
    """回傳以 key 為索引的集合字典。"""
    raw = zot.everything(zot.collections())
    return {c["key"]: c for c in raw}


def get_path(key: str, cols: dict) -> str:
    col = cols[key]
    parent = col["data"].get("parentCollection") or None
    name = col["data"]["name"]
    if parent and parent in cols:
        return get_path(parent, cols) + "/" + name
    return name


def find_by_path(path: str, cols: dict) -> str | None:
    """依路徑字串找 collection key，找不到回傳 None。"""
    for key, col in cols.items():
        if get_path(key, cols) == path:
            return key
    return None


def build_tree(cols: dict) -> dict:
    tree: dict[str | None, list] = {}
    for key, col in cols.items():
        parent = col["data"].get("parentCollection") or None
        tree.setdefault(parent, []).append(key)
    return tree


def print_tree(node, tree, cols, prefix=""):
    children = sorted(
        tree.get(node, []),
        key=lambda k: cols[k]["data"]["name"].lower()
    )
    for i, key in enumerate(children):
        last = i == len(children) - 1
        conn = "└── " if last else "├── "
        name = cols[key]["data"]["name"]
        has_children = key in tree
        print(f"{prefix}{conn}{name}{'/' if has_children else ''}")
        print_tree(key, tree, cols, prefix + ("    " if last else "│   "))


# ── 指令實作 ─────────────────────────────────────────────────────────

def cmd_list(args):
    zot = connect()
    cols = load_collections(zot)
    tree = build_tree(cols)
    print(f"\n共 {len(cols)} 個集合\n")
    print_tree(None, tree, cols)


def cmd_rename(args):
    old_path: str = args.old_path
    new_name: str = args.new_name

    zot = connect()
    cols = load_collections(zot)
    key = find_by_path(old_path, cols)
    if not key:
        print(f"找不到集合：{old_path}")
        sys.exit(1)

    col = cols[key]
    print(f"重新命名：{old_path!r}  →  {new_name!r}")
    confirm = input("確認？(y/N) ").strip().lower()
    if confirm != "y":
        print("取消")
        return

    col["data"]["name"] = new_name
    zot.update_collection(col)
    print("完成")


def cmd_move(args):
    src_path: str = args.src_path
    dst_path: str = args.dst_path  # "ROOT" 表示移到最上層

    zot = connect()
    cols = load_collections(zot)

    src_key = find_by_path(src_path, cols)
    if not src_key:
        print(f"找不到來源集合：{src_path}")
        sys.exit(1)

    if dst_path.upper() == "ROOT":
        dst_key = False  # pyzotero 用 False 表示無父集合
        dst_label = "（最上層）"
    else:
        dst_key = find_by_path(dst_path, cols)
        if not dst_key:
            print(f"找不到目標集合：{dst_path}")
            sys.exit(1)
        dst_label = dst_path

    print(f"移動：{src_path!r}  →  {dst_label}")
    confirm = input("確認？(y/N) ").strip().lower()
    if confirm != "y":
        print("取消")
        return

    col = cols[src_key]
    col["data"]["parentCollection"] = dst_key
    zot.update_collection(col)
    print("完成")


def cmd_create(args):
    name: str = args.name
    parent_path: str | None = args.parent_path

    zot = connect()
    cols = load_collections(zot)

    if parent_path and parent_path.upper() != "ROOT":
        parent_key = find_by_path(parent_path, cols)
        if not parent_key:
            print(f"找不到父集合：{parent_path}")
            sys.exit(1)
        parent_val = parent_key
        location = parent_path
    else:
        parent_val = False
        location = "（最上層）"

    print(f"建立集合：{name!r}  於  {location}")
    confirm = input("確認？(y/N) ").strip().lower()
    if confirm != "y":
        print("取消")
        return

    payload = [{"name": name, "parentCollection": parent_val}]
    zot.create_collections(payload)
    print("完成")


def cmd_items(args):
    path: str = args.path

    zot = connect()
    cols = load_collections(zot)
    key = find_by_path(path, cols)
    if not key:
        print(f"找不到集合：{path}")
        sys.exit(1)

    items = zot.everything(zot.collection_items(key, itemType="journalArticle || conferencePaper || preprint"))
    print(f"\n集合：{path}")
    print(f"文獻數：{len(items)}\n")
    for item in items[:20]:
        title = item["data"].get("title", "(無標題)")
        year  = item["data"].get("date", "")[:4]
        print(f"  [{year}] {title[:80]}")
    if len(items) > 20:
        print(f"  ... 共 {len(items)} 筆（只顯示前 20）")


def cmd_typos(args):
    zot = connect()
    cols = load_collections(zot)

    # 找出符合錯字表的集合
    targets = []
    for key, col in cols.items():
        name = col["data"]["name"]
        if name in TYPO_MAP:
            path = get_path(key, cols)
            targets.append((key, col, name, TYPO_MAP[name], path))

    if not targets:
        print("未偵測到已知錯字")
        return

    print(f"\n偵測到 {len(targets)} 個錯字：\n")
    for _, _, old, new, path in targets:
        print(f"  {path!r}")
        print(f"    {old!r}  →  {new!r}\n")

    confirm = input("全部修正？(y/N) ").strip().lower()
    if confirm != "y":
        print("取消")
        return

    for key, col, old, new, path in targets:
        col["data"]["name"] = new
        zot.update_collection(col)
        print(f"  已修正：{old!r} → {new!r}")
    print("完成")


# ── 主程式 ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Zotero 集合管理工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出集合樹狀結構")

    p_rename = sub.add_parser("rename", help="重新命名集合")
    p_rename.add_argument("old_path", help="集合完整路徑，如 'Programs_Reference/side_consciousness/Readibility'")
    p_rename.add_argument("new_name", help="新名稱（只填名稱，不含父路徑）")

    p_move = sub.add_parser("move", help="移動集合到新位置")
    p_move.add_argument("src_path", help="來源集合完整路徑")
    p_move.add_argument("dst_path", help="目標父集合路徑，或 ROOT 移至最上層")

    p_create = sub.add_parser("create", help="建立新集合")
    p_create.add_argument("name", help="新集合名稱")
    p_create.add_argument("parent_path", nargs="?", default="ROOT", help="父集合路徑，省略或 ROOT 表示最上層")

    p_items = sub.add_parser("items", help="列出集合內的文獻")
    p_items.add_argument("path", help="集合完整路徑")

    sub.add_parser("typos", help="偵測並修正集合名稱錯字")

    args = parser.parse_args()
    {
        "list":   cmd_list,
        "rename": cmd_rename,
        "move":   cmd_move,
        "create": cmd_create,
        "items":  cmd_items,
        "typos":  cmd_typos,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
