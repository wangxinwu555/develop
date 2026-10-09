"""运行 python -m scripts.ingest，建立或复用持久化向量索引。"""

import argparse
import json

from supportflow.config import Settings
from supportflow.ingest import build_index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="强制建立新快照")
    args = parser.parse_args()
    result = build_index(Settings(), force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("已复用索引，无需再次生成文档向量。" if result["reused"] else "向量索引已建立。")


if __name__ == "__main__":
    main()
