"""运行：python -m scripts.lesson1。只读示例，不调用模型、不创建工单。"""

import json

from supportflow.business import Store
from supportflow.config import Settings


def main():
    settings = Settings()
    store = Store(settings.database_path)
    store.initialize()
    print("第一步：查询 Alice 的订单。")
    print(json.dumps(store.get_order("alice", "O1001"), ensure_ascii=False, indent=2))
    print("第二步：用 Python 规则判断是否满足换货条件。")
    print(json.dumps(store.check_exchange("alice", "O1001"), ensure_ascii=False, indent=2))
    print("现在阅读 supportflow/business.py 的 get_order 和 check_exchange。")


if __name__ == "__main__":
    main()
