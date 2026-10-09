"""订单、业务资格与工单存储；业务规则由后端验证。"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from supportflow.config import ROOT


def today():
    # 使用业务时区，避免服务器部署地区影响换货期限。
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


class BusinessError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.policies = json.loads((ROOT / "data/policies.json").read_text(encoding="utf-8"))
        self.exchange_policy = next(p for p in self.policies if p["id"] == "exchange-electronics")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self):
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, product TEXT NOT NULL,
                    category TEXT NOT NULL, status TEXT NOT NULL, delivered_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
                    user_id TEXT NOT NULL, reason TEXT NOT NULL, policy_id TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    approved INTEGER NOT NULL, response TEXT
                );
            """)
            # 只在空数据上插入；重启不重置业务状态，也不刷新已存在订单的日期。
            rows = [
                ("O1001", "alice", "无线耳机", "electronics", "delivered", 3),
                ("O1002", "alice", "蓝牙音箱", "electronics", "delivered", 45),
                ("O1003", "alice", "机械键盘", "electronics", "shipped", None),
                ("O2001", "bob", "无线耳机", "electronics", "delivered", 2),
            ]
            conn.executemany(
                "INSERT OR IGNORE INTO orders VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        *row[:5],
                        (today() - timedelta(days=row[5])).isoformat()
                        if row[5] is not None
                        else None,
                    )
                    for row in rows
                ],
            )

    def get_order(self, user_id: str, order_id: str) -> dict:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE id = ? AND user_id = ?", (order_id, user_id)
            ).fetchone()
        # 不暴露其他用户的订单是否存在。
        if row is None:
            raise BusinessError("order_unavailable", "订单不存在或不属于当前用户。")
        return dict(row)

    def check_exchange(self, user_id: str, order_id: str) -> dict:
        order = self.get_order(user_id, order_id)
        rules = self.exchange_policy["rules"]
        reason = "符合时间和品类条件；是否确有质量问题需后续人工审核。"
        eligible = True
        days = None
        if order["category"] != rules["category"]:
            eligible, reason = False, "当前政策不覆盖该商品品类，请联系人工客服。"
        elif order["status"] != rules["required_status"] or not order["delivered_at"]:
            eligible, reason = False, "订单尚未签收，暂不能申请质量问题换货。"
        else:
            delivered = datetime.strptime(order["delivered_at"], "%Y-%m-%d").date()
            days = (today() - delivered).days
            if not 0 <= days <= rules["max_days"]:
                eligible, reason = False, "已超出换货期限或签收日期异常，请联系人工客服。"
        return {
            "order_id": order_id,
            "eligible": eligible,
            "days_since_delivery": days,
            "reason": reason,
            "policy_id": self.exchange_policy["id"],
            "source": self.exchange_policy["title"],
        }

    def create_ticket(
        self, user_id: str, order_id: str, reason: str, request_id: str, *, approved: bool
    ) -> dict:
        if approved is not True:
            raise BusinessError("approval_required", "必须确认申请后才能创建工单。")
        if not 2 <= len(reason.strip()) <= 500:
            raise BusinessError("invalid_reason", "请提供2至500字的问题描述。")
        # 先检查既有结果，保证跨进程重试仍然返回同一工单。
        with self.connection() as conn:
            existing = conn.execute(
                "SELECT * FROM tickets WHERE request_id = ? AND user_id = ?",
                (request_id, user_id),
            ).fetchone()
        if existing:
            if existing["order_id"] != order_id or existing["reason"] != reason:
                raise BusinessError("idempotency_conflict", "同一申请编号不能用于不同内容。")
            return dict(existing)
        check = self.check_exchange(user_id, order_id)
        if not check["eligible"]:
            raise BusinessError("not_eligible", check["reason"])
        ticket_id = "T-" + uuid4().hex[:12].upper()
        try:
            with self.connection() as conn:
                conn.execute(
                    "INSERT INTO tickets VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ticket_id,
                        request_id,
                        order_id,
                        user_id,
                        reason,
                        check["policy_id"],
                        "submitted",
                        datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            # request_id 与 order_id 均有数据库唯一约束，处理两个请求同时写入的情况。
            with self.connection() as conn:
                row = conn.execute(
                    "SELECT * FROM tickets WHERE order_id = ? AND user_id = ?",
                    (order_id, user_id),
                ).fetchone()
            if row is None:
                raise BusinessError("idempotency_conflict", "申请编号冲突，请重新发起。") from None
            return dict(row)
        return self.list_tickets(user_id, ticket_id)[0]

    def list_tickets(self, user_id: str, ticket_id: str | None = None) -> list[dict]:
        with self.connection() as conn:
            sql = "SELECT * FROM tickets WHERE user_id = ?"
            args = [user_id]
            if ticket_id:
                sql += " AND id = ?"
                args.append(ticket_id)
            return [dict(row) for row in conn.execute(sql + " ORDER BY created_at DESC", args)]

    def create_session(self, user_id: str) -> str:
        session_id = uuid4().hex
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                (session_id, user_id, datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()),
            )
        return session_id

    def require_session(self, user_id: str, session_id: str):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
            ).fetchone()
        if row is None:
            raise BusinessError("session_unavailable", "会话不存在或不属于当前用户。")

    def get_approval(self, session_id: str, approval_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM approvals WHERE id = ? AND session_id = ?",
                (approval_id, session_id),
            ).fetchone()
        return dict(row) if row else None

    def record_approval(self, session_id: str, approval_id: str, approved: bool):
        with self.connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO approvals (id, session_id, approved) VALUES (?, ?, ?)",
                (approval_id, session_id, int(approved)),
            )

    def save_approval_response(self, approval_id: str, response: dict):
        with self.connection() as conn:
            conn.execute(
                "UPDATE approvals SET response = ? WHERE id = ?",
                (json.dumps(response, ensure_ascii=False), approval_id),
            )
