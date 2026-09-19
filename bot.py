import asyncio
from contextlib import contextmanager
import collections
import copy
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from telethon import TelegramClient, errors, events, utils
from telethon.errors import SessionPasswordNeededError
from telethon.extensions import BinaryReader
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import (
    GetDialogFiltersRequest,
    ImportChatInviteRequest,
    ReadMentionsRequest,
    UpdateDialogFilterRequest,
)
from telethon.tl.types import (
    DialogFilter,
    DialogFilterChatlist,
    InputNotifyPeer,
    InputPeerNotifySettings,
    TextWithEntities,
)

try:
    from telethon._updates.messagebox import MessageBox

    def _patched_get_channel_difference(self, chat_hashes):
        entry = next((id for id in list(self.getting_diff_for) if isinstance(id, int)), None)
        if entry is not None:
            self.end_get_diff(entry)
            self.map.pop(entry, None)
            self.possible_gaps.pop(entry, None)
        return None

    MessageBox.get_channel_difference = _patched_get_channel_difference
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"
MEDIA_DIR = STORAGE_DIR / "media"
DB_PATH = STORAGE_DIR / "bot.db"
CONFIG_PATH = BASE_DIR / "config.txt"

try:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

def init_app_storage(app_dir: str):
    global BASE_DIR, STORAGE_DIR, MEDIA_DIR, DB_PATH, CONFIG_PATH, db
    BASE_DIR = Path(app_dir).resolve()
    STORAGE_DIR = BASE_DIR / "storage"
    MEDIA_DIR = STORAGE_DIR / "media"
    DB_PATH = STORAGE_DIR / "bot.db"
    CONFIG_PATH = BASE_DIR / "config.txt"
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    db = Database(DB_PATH)
    return db

DEFAULT_CONFIG = """# ==============================================================================
# КАК ПОЛУЧИТЬ API_ID И API_HASH:
# 1. Зайди в браузере на https://my.telegram.org
# 2. Введи свой номер телефона с кодом страны (напр. +79991234567) и нажми Next.
# 3. В Telegram придет код от официального сервиса — вставь его на сайте и нажми Sign In.
# 4. Выбери пункт "API development tools".
# 5. В форме заполни два поля (любыми латинскими буквами):
#    - App title: например, Spambuster
#    - Short name: например, spambuster
#    Остальные поля оставь пустыми.
# 6. Нажми "Create application" (или Save changes).
# 7. Скопируй полученные данные:
#    - App api_id  -> вставь ниже в api_id (только цифры)
#    - App api_hash -> вставь ниже в api_hash (строка из 32 символов)
# ==============================================================================

# Показывать только ошибки (true — тихий режим, false — подробные логи)
log_errors_only = true

# ==============================================================================
# ОСНОВНОЙ АККАУНТ:
# ==============================================================================
api_id = 
api_hash = 
phone = 
session_name = session_userbot
auto_sub_folder = автосабнутое
auto_read_spam_pings = true
auto_sub_enabled = true

# ==============================================================================
# ДЛЯ ДОБАВЛЕНИЯ ВТОРОГО И ПОСЛЕДУЮЩИХ АККАУНТОВ (раскомментируйте блок ниже):
# ==============================================================================
# [account2]
# api_id = 12345678
# api_hash = 0123456789abcdef0123456789abcdef
# phone = +79997654321
# session_name = session_userbot2
# auto_sub_folder = автосабнутое
# auto_read_spam_pings = true
# auto_sub_enabled = true
"""

def load_config(config_file: Optional[Path] = None) -> Dict[str, Any]:
    target_path = config_file or CONFIG_PATH
    if not target_path.exists():
        if target_path == CONFIG_PATH:
            try:
                target_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            except Exception:
                pass
        else:
            return {"accounts": [], "log_errors_only": True}

    if not target_path.exists():
        return {"accounts": [], "log_errors_only": True}

    try:
        content = target_path.read_text(encoding="utf-8")
    except Exception:
        return {"accounts": [], "log_errors_only": True}
    lines = content.splitlines()

    has_sections = any(re.match(r"^\[.+\]$", l.strip()) for l in lines if l.strip() and not l.strip().startswith("#"))

    global_log_errors_only = True
    accounts: List[Dict[str, Any]] = []

    if has_sections:
        current_acc: Optional[Dict[str, Any]] = None
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            sec_match = re.match(r"^\[(.+)\]$", line)
            if sec_match:
                sec_name = sec_match.group(1).strip()
                current_acc = {
                    "name": sec_name,
                    "api_id": 0,
                    "api_hash": "",
                    "phone": "",
                    "session_name": f"session_{sec_name}",
                    "auto_sub_folder": "автосабнутое",
                    "auto_read_spam_pings": True,
                    "auto_sub_enabled": True,
                }
                accounts.append(current_acc)
                continue

            if "=" in line:
                k, v = line.split("=", 1)
                key = k.strip().lower()
                val = v.strip().strip('"').strip("'")
                if key == "log_errors_only":
                    global_log_errors_only = val.lower() in ("true", "1", "yes")
                elif current_acc is not None:
                    if key == "api_id":
                        current_acc["api_id"] = int(val) if val.isdigit() else 0
                    elif key == "api_hash":
                        current_acc["api_hash"] = val
                    elif key == "phone":
                        current_acc["phone"] = val
                    elif key == "session_name":
                        current_acc["session_name"] = val or f"session_{current_acc['name']}"
                    elif key == "auto_sub_folder":
                        current_acc["auto_sub_folder"] = val or "автосабнутое"
                    elif key == "auto_read_spam_pings":
                        current_acc["auto_read_spam_pings"] = val.lower() in ("true", "1", "yes")
                    elif key == "auto_sub_enabled":
                        current_acc["auto_sub_enabled"] = val.lower() in ("true", "1", "yes")
    else:
        single_acc = {
            "name": "default",
            "api_id": 0,
            "api_hash": "",
            "phone": "",
            "session_name": "session_userbot",
            "auto_sub_folder": "автосабнутое",
            "auto_read_spam_pings": True,
            "auto_sub_enabled": True,
        }
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                key = k.strip().lower()
                val = v.strip().strip('"').strip("'")
                if key == "log_errors_only":
                    global_log_errors_only = val.lower() in ("true", "1", "yes")
                elif key == "api_id":
                    single_acc["api_id"] = int(val) if val.isdigit() else 0
                elif key == "api_hash":
                    single_acc["api_hash"] = val
                elif key == "phone":
                    single_acc["phone"] = val
                elif key == "session_name":
                    single_acc["session_name"] = val or "session_userbot"
                elif key == "auto_sub_folder":
                    single_acc["auto_sub_folder"] = val or "автосабнутое"
                elif key == "auto_read_spam_pings":
                    single_acc["auto_read_spam_pings"] = val.lower() in ("true", "1", "yes")
                elif key == "auto_sub_enabled":
                    single_acc["auto_sub_enabled"] = val.lower() in ("true", "1", "yes")
        accounts.append(single_acc)

    first = accounts[0] if accounts else {}
    return {
        "accounts": accounts,
        "log_errors_only": global_log_errors_only,
        "api_id": first.get("api_id", 0),
        "api_hash": first.get("api_hash", ""),
        "phone": first.get("phone", ""),
        "session_name": first.get("session_name", "session_userbot"),
        "auto_sub_folder": first.get("auto_sub_folder", "автосабнутое"),
        "auto_read_spam_pings": first.get("auto_read_spam_pings", True),
        "auto_sub_enabled": first.get("auto_sub_enabled", True),
    }

try:
    CONFIG = load_config()
except Exception:
    CONFIG = {"accounts": [], "log_errors_only": True}

def log_info(msg: str):
    if not CONFIG.get("log_errors_only", True):
        print(msg)

def log_error(msg: str):
    print(msg)

_BOT_SYSTEM_MSG_IDS: Set[int] = set()

def is_internal_bot_message(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    # 1. Collector status notifications and interactive prompts
    if "добавлено в рассылку" in t and ("📥" in t or "Сообщение #" in t or "сообщение #" in t):
        return True
    if "Отправьте следующее сообщение или напишите" in t and (".закрыть" in t or "отменить" in t or ".отменить" in t):
        return True
    if "(для отмены .отменить)" in t or "(для отмены `.отменить`)" in t or "(для отмены отменить)" in t:
        return True
    if "Режим сбора" in t and ("активирован" in t or "начат" in t) and "📥" in t:
        return True
    if "Время ожидания сообщений (5 минут) истекло" in t:
        return True
    # 2. System command feedback
    if t.startswith("⚙️ Управление юзерботом"):
        return True
    if t.startswith("👁 Тестовый предпросмотр:"):
        return True
    if t.startswith("❌ Настройка рассылки") and "отменена" in t:
        return True
    if t.startswith("✅ Рассылка") and ("запущена" in t or "остановлена" in t):
        return True
    return False

class Database:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=30000;")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
                cursor.execute("PRAGMA temp_store=MEMORY;")
                cursor.execute("PRAGMA cache_size=-2000;")
            except Exception:
                pass
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS templates (
                    name TEXT PRIMARY KEY,
                    text TEXT,
                    entities_hex TEXT,
                    media_files TEXT,
                    bundle_json TEXT DEFAULT '',
                    created_at REAL
                )
            """)
            try:
                cursor.execute("PRAGMA table_info(templates)")
                cols = [r["name"] for r in cursor.fetchall()]
                if "bundle_json" not in cols:
                    cursor.execute("ALTER TABLE templates ADD COLUMN bundle_json TEXT DEFAULT ''")
            except Exception:
                pass

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS broadcast_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER DEFAULT 0,
                    chat_id INTEGER,
                    template_name TEXT,
                    mode TEXT,
                    interval_seconds INTEGER DEFAULT 0,
                    counter_threshold INTEGER DEFAULT 0,
                    current_count INTEGER DEFAULT 0,
                    last_sent_at REAL DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    folder_name TEXT DEFAULT '',
                    UNIQUE(account_id, chat_id, template_name)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_tasks_chat ON broadcast_tasks (chat_id, is_active, mode);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_tasks_active ON broadcast_tasks (is_active, mode, account_id);")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_rotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER DEFAULT 0,
                    chat_id INTEGER,
                    name TEXT DEFAULT '',
                    template_names TEXT,
                    current_index INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1
                )
            """)

            try:
                cursor.execute("PRAGMA table_info(broadcast_tasks)")
                cols = [r["name"] for r in cursor.fetchall()]
                if "account_id" not in cols:
                    cursor.execute("ALTER TABLE broadcast_tasks ADD COLUMN account_id INTEGER DEFAULT 0")
            except Exception:
                pass

            try:
                cursor.execute("PRAGMA table_info(chat_rotations)")
                cols = [r["name"] for r in cursor.fetchall()]
                if "account_id" not in cols:
                    cursor.execute("ALTER TABLE chat_rotations ADD COLUMN account_id INTEGER DEFAULT 0")
                if "name" not in cols:
                    cursor.execute("ALTER TABLE chat_rotations ADD COLUMN name TEXT DEFAULT ''")
            except Exception:
                pass

            # Auto-migrate any existing records with < > in names
            try:
                cursor.execute("SELECT name, text, entities_hex, media_files, created_at FROM templates")
                for r in cursor.fetchall():
                    old_name = r["name"]
                    new_name = old_name.strip("<>").strip()
                    if new_name != old_name:
                        cursor.execute("""
                            INSERT OR REPLACE INTO templates (name, text, entities_hex, media_files, created_at)
                            VALUES (?, ?, ?, ?, ?)
                        """, (new_name, r["text"], r["entities_hex"], r["media_files"], r["created_at"]))
                        cursor.execute("DELETE FROM templates WHERE name = ?", (old_name,))

                cursor.execute("SELECT id, template_name FROM broadcast_tasks")
                for r in cursor.fetchall():
                    old_name = r["template_name"]
                    new_name = old_name.strip("<>").strip()
                    if new_name != old_name:
                        cursor.execute("UPDATE broadcast_tasks SET template_name = ? WHERE id = ?", (new_name, r["id"]))

                cursor.execute("SELECT id, chat_id, template_names FROM chat_rotations")
                for r in cursor.fetchall():
                    names = json.loads(r["template_names"]) if r["template_names"] else []
                    new_names = [n.strip("<>").strip() for n in names if n.strip("<>").strip()]
                    if new_names != names:
                        cursor.execute("UPDATE chat_rotations SET template_names = ? WHERE id = ?", (json.dumps(new_names), r["id"]))
            except Exception:
                pass

    def save_template(
        self,
        name: str,
        text: str,
        entities_hex: List[str],
        media_files: List[str],
        bundle_json: str = ""
    ):
        clean_name = name.strip("<>").strip()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO templates (name, text, entities_hex, media_files, bundle_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (clean_name, text, json.dumps(entities_hex), json.dumps(media_files), bundle_json, time.time()))

    def get_template(self, name: str) -> Optional[Dict[str, Any]]:
        clean_name = name.strip("<>").strip()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM templates WHERE name = ? OR name = ? OR name = ?", (clean_name, name, f"<{clean_name}>"))
            row = cursor.fetchone()
            if not row:
                return None
            bundle_raw = ""
            if "bundle_json" in row.keys() and row["bundle_json"]:
                bundle_raw = row["bundle_json"]
            bundle = json.loads(bundle_raw) if bundle_raw else []
            if bundle:
                clean_bundle = [it for it in bundle if not is_internal_bot_message(it.get("text", ""))]
                if len(clean_bundle) != len(bundle):
                    bundle = clean_bundle
                    try:
                        cursor.execute("UPDATE templates SET bundle_json = ? WHERE name = ?", (json.dumps(bundle), row["name"]))
                    except Exception:
                        pass
            return {
                "name": row["name"],
                "text": row["text"],
                "entities_hex": json.loads(row["entities_hex"]) if row["entities_hex"] else [],
                "media_files": json.loads(row["media_files"]) if row["media_files"] else [],
                "bundle": bundle,
                "created_at": row["created_at"]
            }

    def delete_template(self, name: str):
        clean_name = name.strip("<>").strip()
        target_dir = MEDIA_DIR / clean_name
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM templates WHERE name = ? OR name = ?", (clean_name, f"<{clean_name}>"))
            cursor.execute("DELETE FROM broadcast_tasks WHERE template_name = ? OR template_name = ?", (clean_name, f"<{clean_name}>"))
            cursor.execute("SELECT id, template_names FROM chat_rotations")
            for r in cursor.fetchall():
                names = json.loads(r["template_names"]) if r["template_names"] else []
                new_names = [n for n in names if n != clean_name and n != f"<{clean_name}>"]
                if len(new_names) != len(names):
                    if new_names:
                        cursor.execute("UPDATE chat_rotations SET template_names = ?, current_index = 0 WHERE id = ?", (json.dumps(new_names), r["id"]))
                    else:
                        cursor.execute("DELETE FROM chat_rotations WHERE id = ?", (r["id"],))

    def list_templates(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM templates ORDER BY created_at DESC")
            rows = cursor.fetchall()
            results = []
            for r in rows:
                bundle_raw = r["bundle_json"] if ("bundle_json" in r.keys() and r["bundle_json"]) else ""
                bundle = json.loads(bundle_raw) if bundle_raw else []
                if bundle:
                    clean_bundle = [it for it in bundle if not is_internal_bot_message(it.get("text", ""))]
                    if len(clean_bundle) != len(bundle):
                        bundle = clean_bundle
                        try:
                            cursor.execute("UPDATE templates SET bundle_json = ? WHERE name = ?", (json.dumps(bundle), r["name"]))
                        except Exception:
                            pass
                results.append({
                    "name": r["name"],
                    "text": r["text"],
                    "entities_hex": json.loads(r["entities_hex"]) if r["entities_hex"] else [],
                    "media_files": json.loads(r["media_files"]) if r["media_files"] else [],
                    "bundle": bundle,
                    "created_at": r["created_at"]
                })
            return results

    def add_or_update_broadcast(
        self,
        chat_id: int,
        template_name: str,
        mode: str,
        interval_seconds: int = 0,
        counter_threshold: int = 0,
        folder_name: str = "",
        account_id: int = 0
    ):
        clean_name = template_name.strip("<>").strip()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM broadcast_tasks 
                WHERE chat_id = ? AND template_name = ? AND (account_id = ? OR account_id = 0)
            """, (chat_id, clean_name, account_id))
            row = cursor.fetchone()
            if row:
                cursor.execute("""
                    UPDATE broadcast_tasks SET
                        account_id = ?,
                        mode = ?,
                        interval_seconds = ?,
                        counter_threshold = ?,
                        is_active = 1,
                        folder_name = ?,
                        last_sent_at = 0.0
                    WHERE id = ?
                """, (account_id, mode, interval_seconds, counter_threshold, folder_name, row["id"]))
            else:
                cursor.execute("""
                    INSERT INTO broadcast_tasks (account_id, chat_id, template_name, mode, interval_seconds, counter_threshold, is_active, folder_name, last_sent_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0.0)
                """, (account_id, chat_id, clean_name, mode, interval_seconds, counter_threshold, folder_name))

    def get_active_interval_tasks(self, account_id: int = 0) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("""
                    SELECT * FROM broadcast_tasks 
                    WHERE is_active = 1 AND mode IN ('interval', 'hybrid') AND interval_seconds > 0
                    AND (account_id = ? OR account_id = 0)
                """, (account_id,))
            else:
                cursor.execute("""
                    SELECT * FROM broadcast_tasks 
                    WHERE is_active = 1 AND mode IN ('interval', 'hybrid') AND interval_seconds > 0
                """)
            return [dict(r) for r in cursor.fetchall()]

    def increment_and_check_counter(self, chat_id: int, account_id: int = 0) -> List[Dict[str, Any]]:
        ready_tasks = []
        now = time.time()
        with self._conn() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("""
                    SELECT * FROM broadcast_tasks 
                    WHERE chat_id = ? AND is_active = 1 AND mode IN ('counter', 'hybrid')
                    AND (account_id = ? OR account_id = 0)
                """, (chat_id, account_id))
            else:
                cursor.execute("""
                    SELECT * FROM broadcast_tasks 
                    WHERE chat_id = ? AND is_active = 1 AND mode IN ('counter', 'hybrid')
                """, (chat_id,))
            tasks = cursor.fetchall()
            for row in tasks:
                t = dict(row)
                new_count = t["current_count"] + 1
                mode = t.get("mode", "counter")
                threshold = t["counter_threshold"]
                log_info(f"[COUNT] Чат {chat_id}: сообщение #{new_count}/{threshold} для рассылки '{t['template_name']}' ({mode})")

                if mode == "hybrid":
                    interval = t.get("interval_seconds", 0)
                    last_sent = t.get("last_sent_at", 0.0) or 0.0
                    time_ready = (now - last_sent) >= interval
                    if new_count >= threshold and time_ready:
                        ready_tasks.append(t)
                        cursor.execute("""
                            UPDATE broadcast_tasks 
                            SET current_count = 0, last_sent_at = ? 
                            WHERE id = ?
                        """, (now, t["id"]))
                    else:
                        cursor.execute("""
                            UPDATE broadcast_tasks 
                            SET current_count = ? 
                            WHERE id = ?
                        """, (new_count, t["id"]))
                else:
                    if new_count >= threshold:
                        ready_tasks.append(t)
                        cursor.execute("""
                            UPDATE broadcast_tasks 
                            SET current_count = 0, last_sent_at = ? 
                            WHERE id = ?
                        """, (now, t["id"]))
                    else:
                        cursor.execute("""
                            UPDATE broadcast_tasks 
                            SET current_count = ? 
                            WHERE id = ?
                        """, (new_count, t["id"]))
        return ready_tasks

    def reset_hybrid_task(self, task_id: int):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE broadcast_tasks SET current_count = 0, last_sent_at = ? WHERE id = ?", (time.time(), task_id))

    def update_task_last_sent(self, task_id: int):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE broadcast_tasks SET last_sent_at = ? WHERE id = ?", (time.time(), task_id))

    def restore_counter(self, task_id: int, count: int, last_sent_at: Optional[float] = None):
        with self._conn() as conn:
            cursor = conn.cursor()
            if last_sent_at is not None:
                cursor.execute("UPDATE broadcast_tasks SET current_count = ?, last_sent_at = ? WHERE id = ?", (count, last_sent_at, task_id))
            else:
                cursor.execute("UPDATE broadcast_tasks SET current_count = ? WHERE id = ?", (count, task_id))

    def count_tasks_for_template(self, template_name: str, account_id: int = 0) -> int:
        clean_name = template_name.strip("<>").strip()
        with self._conn() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM broadcast_tasks 
                    WHERE (template_name = ? OR template_name = ?) 
                    AND (account_id = ? OR account_id = 0)
                """, (clean_name, f"<{clean_name}>", account_id))
            else:
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM broadcast_tasks 
                    WHERE (template_name = ? OR template_name = ?)
                """, (clean_name, f"<{clean_name}>"))
            row = cursor.fetchone()
            return row["cnt"] if row else 0

    def set_broadcast_status(
        self,
        name: Optional[str] = None,
        chat_id: Optional[int] = None,
        chat_ids: Optional[List[int]] = None,
        folder_name: Optional[str] = None,
        is_active: int = 1,
        account_id: int = 0
    ) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            query = "UPDATE broadcast_tasks SET is_active = ?"
            params: List[Any] = [is_active]

            conditions = []
            if account_id:
                conditions.append("(account_id = ? OR account_id = 0)")
                params.append(account_id)

            if name and name.lower() not in ("все", "all"):
                clean_name = name.strip("<>").strip()
                conditions.append("(template_name = ? OR template_name = ?)")
                params.extend([clean_name, f"<{clean_name}>"])

            if folder_name or chat_ids:
                sub = []
                if folder_name:
                    sub.append("folder_name = ?")
                    params.append(folder_name)
                if chat_ids:
                    placeholders = ",".join("?" for _ in chat_ids)
                    sub.append(f"chat_id IN ({placeholders})")
                    params.extend(chat_ids)
                conditions.append(f"({' OR '.join(sub)})")
            elif chat_id is not None:
                conditions.append("chat_id = ?")
                params.append(chat_id)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            cursor.execute(query, params)
            return cursor.rowcount

    def delete_broadcasts(
        self,
        name: Optional[str] = None,
        chat_id: Optional[int] = None,
        chat_ids: Optional[List[int]] = None,
        folder_name: Optional[str] = None,
        account_id: int = 0
    ) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            query = "DELETE FROM broadcast_tasks"
            params: List[Any] = []

            conditions = []
            if account_id:
                conditions.append("(account_id = ? OR account_id = 0)")
                params.append(account_id)

            if name and name.lower() not in ("все", "all"):
                clean_name = name.strip("<>").strip()
                conditions.append("(template_name = ? OR template_name = ?)")
                params.extend([clean_name, f"<{clean_name}>"])

            if folder_name or chat_ids:
                sub = []
                if folder_name:
                    sub.append("folder_name = ?")
                    params.append(folder_name)
                if chat_ids:
                    placeholders = ",".join("?" for _ in chat_ids)
                    sub.append(f"chat_id IN ({placeholders})")
                    params.extend(chat_ids)
                conditions.append(f"({' OR '.join(sub)})")
            elif chat_id is not None:
                conditions.append("chat_id = ?")
                params.append(chat_id)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            cursor.execute(query, params)
            return cursor.rowcount

    def get_chat_broadcasts(self, chat_id: int, account_id: int = 0) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("SELECT * FROM broadcast_tasks WHERE chat_id = ? AND (account_id = ? OR account_id = 0) ORDER BY id DESC", (chat_id, account_id))
            else:
                cursor.execute("SELECT * FROM broadcast_tasks WHERE chat_id = ? ORDER BY id DESC", (chat_id,))
            return [dict(r) for r in cursor.fetchall()]

    def set_chat_rotation(self, chat_id: int, template_names: List[str], account_id: int = 0):
        clean_names = [n.strip("<>").strip() for n in template_names if n.strip("<>").strip()]
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM chat_rotations WHERE chat_id = ? AND (name = '' OR name IS NULL) AND (account_id = ? OR account_id = 0)", (chat_id, account_id))
            row = cursor.fetchone()
            if row:
                cursor.execute("""
                    UPDATE chat_rotations SET template_names = ?, current_index = 0, is_active = 1, account_id = ?
                    WHERE id = ?
                """, (json.dumps(clean_names), account_id, row["id"]))
            else:
                cursor.execute("""
                    INSERT INTO chat_rotations (account_id, chat_id, name, template_names, current_index, is_active)
                    VALUES (?, ?, '', ?, 0, 1)
                """, (account_id, chat_id, json.dumps(clean_names)))

    def set_named_rotation(self, name: str, template_names: List[str], chat_id: Optional[int] = None, account_id: int = 0):
        clean_name = name.strip("<>").strip()
        clean_names = [n.strip("<>").strip() for n in template_names if n.strip("<>").strip()]
        with self._conn() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("SELECT id FROM chat_rotations WHERE (name = ? OR name = ?) AND (account_id = ? OR account_id = 0)", (clean_name, f"<{clean_name}>", account_id))
            else:
                cursor.execute("SELECT id FROM chat_rotations WHERE name = ? OR name = ?", (clean_name, f"<{clean_name}>"))
            row = cursor.fetchone()
            if row:
                cursor.execute("""
                    UPDATE chat_rotations SET name = ?, template_names = ?, current_index = 0, is_active = 1, account_id = ?, chat_id = ?
                    WHERE id = ?
                """, (clean_name, json.dumps(clean_names), account_id, chat_id, row["id"]))
            else:
                cursor.execute("""
                    INSERT INTO chat_rotations (account_id, chat_id, name, template_names, current_index, is_active)
                    VALUES (?, ?, ?, ?, 0, 1)
                """, (account_id, chat_id, clean_name, json.dumps(clean_names)))

    def delete_named_rotation(self, name: str, account_id: int = 0) -> bool:
        clean_name = name.strip("<>").strip()
        with self._conn() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("DELETE FROM chat_rotations WHERE (name = ? OR name = ?) AND (account_id = ? OR account_id = 0)", (clean_name, f"<{clean_name}>", account_id))
                deleted = cursor.rowcount > 0
                cursor.execute("DELETE FROM broadcast_tasks WHERE (template_name = ? OR template_name = ?) AND (account_id = ? OR account_id = 0)", (clean_name, f"<{clean_name}>", account_id))
            else:
                cursor.execute("DELETE FROM chat_rotations WHERE name = ? OR name = ?", (clean_name, f"<{clean_name}>"))
                deleted = cursor.rowcount > 0
                cursor.execute("DELETE FROM broadcast_tasks WHERE template_name = ? OR template_name = ?", (clean_name, f"<{clean_name}>"))
            return deleted

    def get_named_rotation(self, name: str, account_id: int = 0) -> Optional[Dict[str, Any]]:
        clean_name = name.strip("<>").strip()
        with self._conn() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("SELECT * FROM chat_rotations WHERE (name = ? OR name = ?) AND is_active = 1 AND (account_id = ? OR account_id = 0)", (clean_name, f"<{clean_name}>", account_id))
            else:
                cursor.execute("SELECT * FROM chat_rotations WHERE (name = ? OR name = ?) AND is_active = 1", (clean_name, f"<{clean_name}>"))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "account_id": row["account_id"],
                "chat_id": row["chat_id"],
                "name": row["name"],
                "template_names": json.loads(row["template_names"]) if row["template_names"] else [],
                "current_index": row["current_index"],
                "is_active": row["is_active"]
            }

    def list_named_rotations(self, account_id: int = 0) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("SELECT * FROM chat_rotations WHERE name != '' AND (account_id = ? OR account_id = 0) ORDER BY id ASC", (account_id,))
            else:
                cursor.execute("SELECT * FROM chat_rotations WHERE name != '' ORDER BY id ASC")
            res = []
            for r in cursor.fetchall():
                res.append({
                    "id": r["id"],
                    "account_id": r["account_id"],
                    "chat_id": r["chat_id"],
                    "name": r["name"],
                    "template_names": json.loads(r["template_names"]) if r["template_names"] else [],
                    "current_index": r["current_index"],
                    "is_active": r["is_active"]
                })
            return res

    def delete_all_named_rotations(self, account_id: int = 0):
        with self._conn() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("DELETE FROM chat_rotations WHERE name != '' AND (account_id = ? OR account_id = 0)", (account_id,))
            else:
                cursor.execute("DELETE FROM chat_rotations WHERE name != ''")

    def delete_chat_rotation(self, chat_id: Optional[int] = None, chat_ids: Optional[List[int]] = None, account_id: int = 0):
        with self._conn() as conn:
            cursor = conn.cursor()
            conditions = []
            params = []
            if account_id:
                conditions.append("(account_id = ? OR account_id = 0)")
                params.append(account_id)
            if chat_ids:
                placeholders = ",".join("?" for _ in chat_ids)
                conditions.append(f"chat_id IN ({placeholders})")
                params.extend(chat_ids)
            elif chat_id is not None:
                conditions.append("chat_id = ?")
                params.append(chat_id)
            where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            cursor.execute(f"DELETE FROM chat_rotations{where_clause}", params)

    def set_rotation_status(self, chat_id: Optional[int] = None, chat_ids: Optional[List[int]] = None, is_active: int = 1, account_id: int = 0):
        with self._conn() as conn:
            cursor = conn.cursor()
            conditions = []
            params: List[Any] = [is_active]
            if account_id:
                conditions.append("(account_id = ? OR account_id = 0)")
                params.append(account_id)
            if chat_ids:
                placeholders = ",".join("?" for _ in chat_ids)
                conditions.append(f"chat_id IN ({placeholders})")
                params.extend(chat_ids)
            elif chat_id is not None:
                conditions.append("chat_id = ?")
                params.append(chat_id)
            where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            cursor.execute(f"UPDATE chat_rotations SET is_active = ?{where_clause}", params)

    def remove_template_from_rotations(self, template_name: str):
        clean_name = template_name.strip("<>").strip()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, template_names FROM chat_rotations")
            for r in cursor.fetchall():
                names = json.loads(r["template_names"]) if r["template_names"] else []
                new_names = [n for n in names if n != clean_name and n != f"<{clean_name}>"]
                if len(new_names) != len(names):
                    if new_names:
                        cursor.execute("UPDATE chat_rotations SET template_names = ?, current_index = 0 WHERE id = ?", (json.dumps(new_names), r["id"]))
                    else:
                        cursor.execute("DELETE FROM chat_rotations WHERE id = ?", (r["id"],))

    def get_chat_rotation(self, chat_id: int, account_id: int = 0) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute("SELECT * FROM chat_rotations WHERE chat_id = ? AND (name = '' OR name IS NULL) AND is_active = 1 AND (account_id = ? OR account_id = 0)", (chat_id, account_id))
            else:
                cursor.execute("SELECT * FROM chat_rotations WHERE chat_id = ? AND (name = '' OR name IS NULL) AND is_active = 1", (chat_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "chat_id": row["chat_id"],
                "name": row["name"],
                "template_names": json.loads(row["template_names"]) if row["template_names"] else [],
                "current_index": row["current_index"],
                "is_active": row["is_active"]
            }

    def get_next_rotation_template(self, chat_id: Optional[int] = None, account_id: int = 0, rotation_name: Optional[str] = None) -> Optional[str]:
        with self._conn() as conn:
            cursor = conn.cursor()
            row = None
            if rotation_name:
                clean_rot = rotation_name.strip("<>").strip()
                if account_id:
                    cursor.execute("SELECT * FROM chat_rotations WHERE (name = ? OR name = ?) AND is_active = 1 AND (account_id = ? OR account_id = 0)", (clean_rot, f"<{clean_rot}>", account_id))
                else:
                    cursor.execute("SELECT * FROM chat_rotations WHERE (name = ? OR name = ?) AND is_active = 1", (clean_rot, f"<{clean_rot}>"))
                row = cursor.fetchone()

            if not row and chat_id is not None:
                if account_id:
                    cursor.execute("SELECT * FROM chat_rotations WHERE chat_id = ? AND (name = '' OR name IS NULL) AND is_active = 1 AND (account_id = ? OR account_id = 0)", (chat_id, account_id))
                else:
                    cursor.execute("SELECT * FROM chat_rotations WHERE chat_id = ? AND (name = '' OR name IS NULL) AND is_active = 1", (chat_id,))
                row = cursor.fetchone()

            if not row:
                return None

            names = json.loads(row["template_names"]) if row["template_names"] else []
            if not names:
                return None

            valid_names = []
            for n in names:
                clean = n.strip("<>").strip()
                cursor.execute("SELECT 1 FROM templates WHERE name = ? OR name = ?", (clean, f"<{clean}>"))
                if cursor.fetchone():
                    valid_names.append(clean)

            if not valid_names:
                return None

            if len(valid_names) != len(names):
                cursor.execute("UPDATE chat_rotations SET template_names = ? WHERE id = ?", (json.dumps(valid_names), row["id"]))

            idx = row["current_index"] % len(valid_names)
            template_name = valid_names[idx]
            next_idx = (idx + 1) % len(valid_names)
            cursor.execute("UPDATE chat_rotations SET current_index = ? WHERE id = ?", (next_idx, row["id"]))
            return template_name

try:
    db = Database()
except Exception:
    db = None
ACTIVE_COLLECTORS: Dict[Any, Dict[str, Any]] = {}

SPAM_SIGNATURES = [
    # 1. Any broadcaster / userbot watermarks (Russian & English):
    r"(?:отправлено|рассылаю|рассылка|рассылается|передано|переслано|опубликовано|запощено|размещено|сделано|постинг|автопост|автопостинг|создано|работает)\s+(?:с\s+помощью|через|с|в)\s+@?[a-zA-Z0-9_]{3,32}",
    r"(?:sent|posted|broadcasted|forwarded|powered|created)\s+(?:via|by|with|through)\s+@?[a-zA-Z0-9_]{3,32}",

    # 2. Promotional software / bot mentions
    r"(?:скрипт|софт|программ\w*|бот)\s+(?:для\s+)?(?:рассыл|спам|автопост|трафик|пиар|реклам|инвайт)\w*\s*[:\-—]?\s*@?\w+",
    r"@[a-zA-Z0-9_]*(?:autopost|sender|spambot|postbot|mailer|blast|repost|prbot|trafficbot|mailing)[a-zA-Z0-9_]*",
    r"заказать\s+(?:рассылку|спам|рекламу|трафик|инвайтинг)",

    # 3. Seller routing & channels lists
    r"(?:пишите|писать|связь|обращаться|отпишите)\s+(?:в\s+лс|в\s+личку|менеджеру|сюда)?\s*[:\-—]?\s*@\w+",
    r"(?:по\s+поводу\s+(?:рекламы|покупки|сотрудничества)|для\s+заказа)\s*[:\-—]?\s*@\w+",
    r"(?:наш\s+(?:канал|чат|бот|шоп|магазин)|вход\s+в\s+чат)\s*[:\-—]?\s*(?:https?://)?t\.me/\+?[a-zA-Z0-9_]+",
    r"(?:📩|👉|📲|💬|✍️|✉️)\s*(?:в\s+лс|пишите|связь|обращаться)?\s*@\w+",

    # 4. Recruitment and manager ads
    r"ищу\s+менеджер\w*",
    r"менеджер\w*\s+по\s+продажам",
    r"в\s+(?:мой|наш)\s+канал\s*[:\-—]?\s*@\w+",

    # 5. Catalog bullet points with @ channels or links
    r"[1-9]️⃣\s*@\w+",
    r"[①-⑩]\s*@\w+",

    # 6. Product/service selling
    r"продам\s+(?:канал[ыа]?|чат[ыа]?)",
    r"продам\s+(?:акк[иа]?|аккаунт[ыа]?|сетку|базу|групп[уыа]|бота|клики)",
    r"прода[юе]тся\s+(?:канал[ыа]?|чат[ыа]?)",
    r"(?:эро|азартн\w*|казино|крипт\w*|гемблинг)\s+тематик\w*",
    r"скупаю\s+(?:канал[ыа]?|акк[иа]?|аккаунт[ыа]?|групп[уыа]|голду|штукенции)",
    r"(?:купишь|купите|покупайте|покупай)\s+(?:рекламу|канал[ыа]?|чат[ыа]?|акк[иа]?|групп[уыа]|бота)",
    r"без\s+спам\s*блока",
    r"без\s+пароля",
    r"\d+\s*кликов\s*[-–—:]",
    r"клики\s*:",
    r"телеграмм\s+аккаунты\s*:",
    r"купить\s+можно\s+за\s+звезды",
    r"прода[южа]\s+зв[её]зд",
    r"казино|гемблинг|азарт|1win|stake",
    r"подарки\s+ниже\s+флора",
]

SPAM_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SPAM_SIGNATURES]
COUNTRY_FLAGS = ["🇺🇸", "🇮🇳", "🇮🇩", "🇨🇴", "🇲🇲", "🇧🇩", "🇧🇷", "🇨🇦", "🇲🇽", "🇹🇷", "🇨🇱", "🇿🇦", "🇷🇺"]

BUYER_QUESTION_TRIGGERS = [
    r"\bпоч[её]м\b",
    r"\bцена\b",
    r"\bцену\b",
    r"\bпрайс\b",
    r"\bактуально\b",
    r"\bсвободно\b",
    r"\bстат[уа]\b",
    r"\bстатистик[ау]\b",
    r"\bохват\b",
    r"\bчекни\s+лс\b",
    r"\bответь(?:те)?\s+в\s+лс\b",
    r"\bотпиши\b",
    r"\bкуплю\s+рекламу\b",
    r"\bхочу\s+купить\b",
    r"\bзакреп\b",
]

BUYER_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BUYER_QUESTION_TRIGGERS]

GATEKEEPER_TRIGGERS = [
    r"чтобы\s+писать\s+в\s+чат",
    r"необходимо\s+подписаться",
    r"подпишитесь\s+на\s+канал",
    r"обязательная\s+подписка",
    r"для\s+доступа\s+к\s+чату",
    r"пройдите\s+капчу",
    r"подтвердите,\s*что\s+вы\s+не\s+бот",
]

GATEKEEPER_PATTERNS = [re.compile(p, re.IGNORECASE) for p in GATEKEEPER_TRIGGERS]

VERIFY_BUTTON_TEXTS = [
    "подписался", "я подписался", "проверить", "готово", "подтвердить",
    "вступил", "продолжить", "check", "done", "verify", "i subscribed"
]

def _utf16_slice(text: str, offset: int, length: int) -> str:
    try:
        encoded = text.encode("utf-16-le")
        return encoded[offset * 2 : (offset + length) * 2].decode("utf-16-le", errors="ignore")
    except Exception:
        return text[offset : offset + length]

def calculate_spam_score(text: str, message: Any = None) -> Tuple[int, str, List[str]]:
    """
    Advanced Heuristic Spam Recognition Engine.
    Evaluates message structure, layout typography, entity metadata,
    routing CTAs, and conversational dampeners.
    
    Returns (score, primary_trigger, matched_factors).
    Threshold for spam classification is score >= 50.
    """
    if not text:
        return 0, "", []

    factors: List[str] = []
    clean_text = text.lower()
    raw_lines = [l.strip() for l in text.splitlines() if l.strip()]
    clean_lines = [l.strip() for l in clean_text.splitlines() if l.strip()]

    # =========================================================================
    # 1. HARD TRIGGERS (Immediate 100 points)
    # =========================================================================
    # 1.1 Inline posting bot watermark / auto-poster
    if message and getattr(message, "via_bot_id", None):
        return 100, "sent_via_inline_bot", ["inline_bot"]

    # 1.2 Invisible ghost ping characters (zero-width characters used for hidden mass-tagging)
    if re.search(r"[\u200b\u200c\u200e\u200f\u2060-\u206f\ufeff]", text):
        return 100, "invisible_ghost_ping", ["zero_width_ghost_chars"]

    # 1.3 Entity inspection: hidden ghost mentions & mass tagging
    if message and getattr(message, "entities", None):
        from telethon.tl.types import (
            MessageEntityMentionName,
            MessageEntityMention,
            MessageEntityTextUrl
        )
        mention_names = 0
        text_urls_user = 0
        total_mentions = 0
        has_hidden_mention = False

        for ent in message.entities:
            if isinstance(ent, MessageEntityMentionName):
                mention_names += 1
                total_mentions += 1
                try:
                    ent_text = _utf16_slice(text, ent.offset, ent.length)
                    if not ent_text.strip() or not re.search(r'\w', ent_text):
                        has_hidden_mention = True
                except Exception:
                    has_hidden_mention = True
            elif isinstance(ent, MessageEntityMention):
                total_mentions += 1
            elif isinstance(ent, MessageEntityTextUrl):
                if ent.url and "tg://user?id=" in ent.url.lower():
                    text_urls_user += 1
                    total_mentions += 1
                    try:
                        ent_text = _utf16_slice(text, ent.offset, ent.length)
                        if not ent_text.strip() or not re.search(r'\w', ent_text):
                            has_hidden_mention = True
                    except Exception:
                        has_hidden_mention = True

        if mention_names >= 2 or text_urls_user >= 2 or total_mentions >= 3:
            return 100, "mass_entity_tagging", [f"mentions_{total_mentions}"]

        if has_hidden_mention:
            return 100, "hidden_ghost_mention", ["mention_under_emoji_or_space"]

    # 1.4 Direct signature matching (watermarks, spam tools, seller solicitations)
    for pat in SPAM_PATTERNS:
        if pat.search(clean_text):
            return 100, pat.pattern, ["signature_match"]

    # =========================================================================
    # 2. HEURISTIC ACCUMULATIVE SCORING
    # =========================================================================
    score = 0

    # 2.1 Layout & Visual Structure
    # Banner / promo emojis at line starts (🔥, ⚡️, 📢, 💎, 💰, 🚀, 🚨, ❗️, ⚠️, ⭐️, 1️⃣, 2️⃣)
    banner_emoji_lines = sum(
        1 for l in raw_lines
        if re.match(r"^(?:🔥|⚡️|📢|💎|💰|🚀|🚨|❗️|⚠️|⭐️|🌟|💥|🎯|👉|📩|📲|💬)", l)
    )
    if banner_emoji_lines >= 1:
        pts = 15 if banner_emoji_lines == 1 else 25
        score += pts
        factors.append(f"banner_emojis(+{pts})")

    # Itemized catalog bullet points (·, •, -, —, 1️⃣, 2️⃣, ①, ②)
    bullet_lines = sum(
        1 for l in raw_lines
        if re.match(r"^(?:[·•\-\—\*]|\d+[\.\)\-\—]|[1-9]️⃣|[①-⑩])\s*", l)
    )
    if bullet_lines >= 1:
        pts = 15 if bullet_lines == 1 else 25
        score += pts
        factors.append(f"bullet_points(+{pts})")

    # Multi-block layout (structured advertisement with multiple separated sections)
    if len(raw_lines) >= 3 or ("\n\n" in text and len(raw_lines) >= 2):
        score += 15
        factors.append("multiblock_layout(+15)")

    # 2.2 Contacts, Routing & CTA (Call to Action)
    # Contact block directed via emoji (e.g. 📩@SuKoW, 👉@user, 👉Пишите в лс @user)
    has_emoji_contact = bool(re.search(
        r'(?:📩|👉|📲|💬|✍️|✉️)\s*(?:в\s+лс|пишите|связь|обращаться)?\s*@\w+',
        clean_text
    ))
    if has_emoji_contact:
        score += 30
        factors.append("emoji_contact_routing(+30)")

    # Textual seller routing (пишите в лс @..., связь @...)
    has_text_seller_contact = bool(re.search(
        r'(?:пишите|писать|связь|обращаться|отпишите|в\s+лс|в\s+личку|менеджеру)\s*[:\-—]?\s*@\w+',
        clean_text
    ))
    if has_text_seller_contact and not has_emoji_contact:
        score += 25
        factors.append("text_seller_routing(+25)")

    # Channel links (t.me/+, joinchat, or t.me/username)
    promo_links = re.findall(r't\.me/(?:\+|joinchat/|[a-zA-Z0-9_]{5,})', clean_text)
    if promo_links:
        pts = 25 if any("+" in link or "joinchat" in link for link in promo_links) else 15
        score += pts
        factors.append(f"promo_links(+{pts})")

    # Multiple mentions / channel handles (@channel, @manager)
    usernames = re.findall(r'@([a-zA-Z0-9_]{4,32})', text)
    if len(usernames) >= 2:
        score += 15
        factors.append(f"multiple_usernames_{len(usernames)}(+15)")

    # 2.3 Commercial Intent & Asset Description
    # Commercial offer/recruitment pitch (продам канал, продам чат, ищу менеджеров по продажам)
    has_commercial_pitch = bool(re.search(
        r'\b(?:продам|продаю|прода[её]тся|скупаю|продажа|ищу\s+менеджер\w*|менеджер\w*\s+по\s+продажам)\b',
        clean_text
    ))
    if has_commercial_pitch:
        score += 25
        factors.append("commercial_pitch(+25)")

    # Asset specifiers (канал, чат, эро тематики, азартной тематики, казино, гемблинг, аккаунты, клики)
    has_asset_specifiers = bool(re.search(
        r'\b(?:канал[ыа]?|чат[ыа]?|сетку|базу|аккаунт[ыа]?|клики|тематик\w*|казино|гемблинг|азарт)\b',
        clean_text
    ))
    if has_asset_specifiers:
        score += 15
        factors.append("asset_specifiers(+15)")

    # Mass account lists with country flags
    flag_count = sum(1 for flag in COUNTRY_FLAGS if flag in text)
    if flag_count >= 3:
        score += 35
        factors.append(f"country_flags_{flag_count}(+35)")

    # =========================================================================
    # 3. CONVERSATIONAL DAMPENERS (Protects regular users)
    # =========================================================================
    # Question mark without commercial routing indicates genuine inquiry
    has_q_mark = "?" in clean_text
    if has_q_mark and not (has_emoji_contact or has_text_seller_contact):
        score -= 30
        factors.append("question_mark(-30)")

    # Short single-line conversational messages (< 70 chars)
    if len(clean_text) < 70 and len(raw_lines) == 1:
        score -= 30
        factors.append("short_single_line(-30)")

    # Conversational speech markers (я, мне, мы, ты, тебе, привет, спасибо, ку, хаха, лол)
    conversational_markers = bool(re.search(
        r'\b(?:я|мне|меня|мы|нас|ты|тебе|тебя|привет|ку|здравствуйте|спасибо|спс|лол|хах|хд|ладно|норм|почему|зачем)\b',
        clean_text
    ))
    if conversational_markers and not (has_emoji_contact or bullet_lines >= 1):
        score -= 20
        factors.append("conversational_markers(-20)")

    # Determine primary trigger
    primary_trigger = factors[0] if factors else "low_score"
    return max(0, score), primary_trigger, factors

def is_spam_message(text: str, message: Any = None) -> Tuple[bool, str]:
    if not text:
        return False, ""

    score, primary_trigger, factors = calculate_spam_score(text, message)
    if score >= 50:
        return True, f"{primary_trigger} (score={score})"
    return False, ""

def is_genuine_buyer_question(text: str) -> bool:
    if not text:
        return False
    clean_text = text.lower().strip()

    # Never consider a message a buyer question if it contains seller contact directing to another username
    if re.search(r'(?:пишите|писать|связь|обращаться|отпишите)\s*[:\-—]?\s*@\w+|(?:📩|👉|📲|💬|✍️|✉️)\s*@\w+', clean_text):
        return False

    # Never consider catalog/product lists as buyer questions
    if re.search(r'[1-9]️⃣|[①-⑩]', clean_text):
        return False

    # Never consider mass price lists as buyer questions
    if sum(1 for flag in COUNTRY_FLAGS if flag in text) >= 2:
        return False

    has_q_mark = "?" in clean_text
    buyer_triggers = [
        r"\bпоч[её]м\b", r"\bцена\b", r"\bцену\b", r"\bпрайс\b", r"\bактуально\b",
        r"\bсвободно\b", r"\bстат[уа]\b", r"\bстатистик[ау]\b", r"\bохват\b",
        r"\bчекни\s+лс\b", r"\bответь(?:те)?\s+в\s+лс\b", r"\bкуплю\b", r"\bхочу\s+купить\b",
        r"\bпродашь\b", r"\bпрода[её]шь\b", r"\bпрода[её]те\b",
        r"\bможно\s+(?:купить|взять|разместить|заказать)\b",
        r"\bместо\s+есть\b", r"\bесть\s+место\b"
    ]
    matches_buyer_trigger = any(re.search(p, clean_text) for p in buyer_triggers)

    # Conversational questions are short (< 220 chars) and either have ? or clear buyer trigger phrase
    if (has_q_mark or matches_buyer_trigger) and len(clean_text) < 220:
        # If it's a broadcast offering goods for sale ("Продам каналы") or asking user to buy ("Купишь рекламу?"), it's not a buyer asking
        if re.search(r'\b(?:продам|продаю|скупаю|продажа|купишь|купите)\s+(?:канал|чат|акк|сетк|групп|баз|бот|реклам)', clean_text):
            return False
        return True

    return False

def should_auto_read(text: str, message: Any = None) -> bool:
    if is_genuine_buyer_question(text):
        return False
    is_spam, _ = is_spam_message(text, message)
    return is_spam

def is_gatekeeper_text(text: str) -> bool:
    if not text:
        return False
    clean = text.lower()
    return any(p.search(clean) for p in GATEKEEPER_PATTERNS)

def extract_channel_links(text: str) -> List[str]:
    if not text:
        return []
    targets = set()
    for m in re.findall(r"(?:https?://)?(?:t\.me|telegram\.me)/(?:joinchat/|\+)?([a-zA-Z0-9_+-]+)", text):
        clean = m.strip("/")
        if clean:
            targets.add(clean)
    for m in re.findall(r"@([a-zA-Z0-9_]{4,32})", text):
        targets.add(m)
    return list(targets)

def is_verify_button(text: str) -> bool:
    if not text:
        return False
    clean = text.lower().strip()
    return any(v in clean for v in VERIFY_BUTTON_TEXTS)

def serialize_entities(entities: Optional[List[Any]]) -> List[str]:
    if not entities:
        return []
    return [bytes(e).hex() for e in entities]

def deserialize_entities(hex_list: Optional[List[str]]) -> List[Any]:
    if not hex_list:
        return []
    entities = []
    for h in hex_list:
        try:
            reader = BinaryReader(bytes.fromhex(h))
            entities.append(reader.tgread_object())
        except Exception:
            continue
    return entities

def extract_payload_and_entities(message: Any, raw_text: str, folder_name: str = "") -> Tuple[str, List[str]]:
    lines = raw_text.splitlines()
    if len(lines) > 1:
        first_nl = raw_text.find("\n")
        raw_prefix = raw_text[:first_nl + 1]
        raw_payload = raw_text[first_nl + 1:]
        stripped = raw_payload.strip()
        if not stripped:
            return "", []

        leading_ws = raw_payload[:len(raw_payload) - len(raw_payload.lstrip())]
        m = re.search(r'^"(.*)"$', stripped, re.DOTALL)
        if m:
            text = m.group(1)
            prefix_before_text = raw_prefix + leading_ws + '"'
        else:
            text = stripped
            prefix_before_text = raw_prefix + leading_ws

        prefix_len = len(prefix_before_text.encode("utf-16le")) // 2
        text_len = len(text.encode("utf-16le")) // 2
        out_entities = []
        if getattr(message, "entities", None):
            for e in message.entities:
                off = e.offset - prefix_len
                if off >= 0 and (off + e.length) <= text_len:
                    ne = copy.copy(e)
                    ne.offset = off
                    try:
                        out_entities.append(bytes(ne).hex())
                    except Exception:
                        pass
        return text, out_entities
    else:
        quoted = re.findall(r'"([^"]+)"', raw_text)
        text = ""
        if quoted:
            if folder_name and len(quoted) > 1:
                text = quoted[1] if quoted[0] == folder_name else quoted[0]
            elif not folder_name:
                text = quoted[0]

        if not text:
            return "", []

        quote_target = f'"{text}"'
        pos = raw_text.find(quote_target)
        if pos == -1:
            pos = raw_text.find(text)
            prefix = raw_text[:pos]
        else:
            prefix = raw_text[:pos + 1]

        prefix_len = len(prefix.encode("utf-16le")) // 2
        text_len = len(text.encode("utf-16le")) // 2
        out_entities = []
        if getattr(message, "entities", None):
            for e in message.entities:
                off = e.offset - prefix_len
                if off >= 0 and (off + e.length) <= text_len:
                    ne = copy.copy(e)
                    ne.offset = off
                    try:
                        out_entities.append(bytes(ne).hex())
                    except Exception:
                        pass
        return text, out_entities

async def save_media_from_message(
    client: TelegramClient,
    message: Any,
    template_name: str,
    item_index: int = 0,
    clear_existing: bool = False
) -> List[str]:
    clean_name = re.sub(r'[\\/*?:"<>|]', '_', template_name.strip("<>").strip())
    target_dir = MEDIA_DIR / clean_name
    if clear_existing and target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    if getattr(message, "grouped_id", None):
        album_messages = []
        async for m in client.iter_messages(message.chat_id, limit=20):
            if getattr(m, "grouped_id", None) == message.grouped_id:
                album_messages.append(m)
        album_messages.sort(key=lambda x: x.id)

        for idx, m in enumerate(album_messages):
            if m.media:
                file_path = await client.download_media(m, file=str(target_dir / f"item_{item_index}_{idx}"))
                if file_path:
                    saved_paths.append(str(file_path))
    elif getattr(message, "media", None):
        file_path = await client.download_media(message, file=str(target_dir / f"item_{item_index}_0"))
        if file_path:
            saved_paths.append(str(file_path))

    return saved_paths

async def send_single_item(
    client: TelegramClient,
    chat_peer: Any,
    text: str,
    entities_hex: List[str],
    media_files: List[str]
):
    entities = deserialize_entities(entities_hex)
    existing_media = [f for f in media_files if os.path.exists(f)]
    fmt_entities = entities if entities else None

    if not text and not existing_media:
        log_error("[WARN] Пустой пост: нет текста и медиа-файлы не найдены.")
        return

    if existing_media:
        if len(existing_media) == 1:
            await client.send_file(
                chat_peer,
                file=existing_media[0],
                caption=text or None,
                formatting_entities=fmt_entities
            )
        else:
            await client.send_file(
                chat_peer,
                file=existing_media,
                caption=text or None,
                formatting_entities=fmt_entities
            )
    else:
        await client.send_message(
            chat_peer,
            message=text,
            formatting_entities=fmt_entities
        )

async def send_broadcast_post(
    client: TelegramClient,
    chat_peer: Any,
    text: str = "",
    entities_hex: Optional[List[str]] = None,
    media_files: Optional[List[str]] = None,
    bundle: Optional[List[Dict[str, Any]]] = None
):
    if bundle:
        clean_bundle = [item for item in bundle if not is_internal_bot_message(item.get("text", ""))]
        for idx, item in enumerate(clean_bundle):
            if idx > 0:
                await asyncio.sleep(1.0)

            if item.get("is_forward") and item.get("forward_chat_id") and item.get("forward_msg_ids"):
                try:
                    await client.forward_messages(
                        chat_peer,
                        messages=item["forward_msg_ids"],
                        from_peer=item["forward_chat_id"]
                    )
                    continue
                except (errors.SlowModeWaitError, errors.FloodWaitError, errors.UserBannedInChannelError, errors.ChannelPrivateError, errors.ChatWriteForbiddenError, asyncio.CancelledError):
                    raise
                except Exception as e:
                    print(f"[WARN] Не удалось переслать сообщение: {e}. Отправляю сохраненную копию.")

            await send_single_item(
                client,
                chat_peer,
                text=item.get("text", ""),
                entities_hex=item.get("entities_hex", []),
                media_files=item.get("media_files", [])
            )
    else:
        if is_internal_bot_message(text):
            return
        await send_single_item(
            client,
            chat_peer,
            text=text,
            entities_hex=entities_hex or [],
            media_files=media_files or []
        )

def _extract_folder_title(dialog_filter: Any) -> str:
    if hasattr(dialog_filter, "title"):
        title = dialog_filter.title
        if hasattr(title, "text"):
            return str(title.text).strip()
        return str(title).strip()
    return ""

async def get_chats_in_folder(client: TelegramClient, folder_name: str) -> List[int]:
    result = await client(GetDialogFiltersRequest())
    target_clean = folder_name.lower().strip()
    target_filter = None

    for f in getattr(result, "filters", []):
        if isinstance(f, (DialogFilter, DialogFilterChatlist)):
            title = _extract_folder_title(f).lower()
            if title == target_clean:
                target_filter = f
                break

    if not target_filter:
        return []

    chat_ids = set()
    for peer in list(getattr(target_filter, "include_peers", [])) + list(getattr(target_filter, "pinned_peers", [])):
        try:
            cid = utils.get_peer_id(peer)
            chat_ids.add(cid)
        except Exception:
            pass

    exclude_ids = set()
    for peer in getattr(target_filter, "exclude_peers", []):
        try:
            cid = utils.get_peer_id(peer)
            exclude_ids.add(cid)
        except Exception:
            pass

    has_flags = any([
        getattr(target_filter, "groups", False),
        getattr(target_filter, "broadcasts", False),
        getattr(target_filter, "contacts", False),
        getattr(target_filter, "non_contacts", False),
        getattr(target_filter, "bots", False),
    ])

    if has_flags:
        try:
            async for dialog in client.iter_dialogs(limit=300):
                cid = dialog.id
                if cid in exclude_ids:
                    continue
                if getattr(target_filter, "exclude_muted", False) and getattr(getattr(dialog, "dialog", None), "notify_settings", None) and getattr(dialog.dialog.notify_settings, "silent", False):
                    continue
                if getattr(target_filter, "exclude_archived", False) and getattr(dialog, "archived", False):
                    continue

                matched = False
                if getattr(target_filter, "groups", False) and (dialog.is_group or (dialog.is_channel and not getattr(dialog.entity, "broadcast", False))):
                    matched = True
                elif getattr(target_filter, "broadcasts", False) and dialog.is_channel and getattr(dialog.entity, "broadcast", False):
                    matched = True
                elif getattr(target_filter, "contacts", False) and dialog.is_user and getattr(getattr(dialog.entity, "contact", None), "contact", False):
                    matched = True
                elif getattr(target_filter, "non_contacts", False) and dialog.is_user and not getattr(getattr(dialog.entity, "contact", None), "contact", False):
                    matched = True
                elif getattr(target_filter, "bots", False) and dialog.is_user and getattr(dialog.entity, "bot", False):
                    matched = True

                if matched:
                    chat_ids.add(cid)
        except Exception:
            pass

    return [cid for cid in chat_ids if cid not in exclude_ids]

async def mute_peer(client: TelegramClient, peer: Any):
    try:
        input_peer = await client.get_input_entity(peer)
        await client(UpdateNotifySettingsRequest(
            peer=InputNotifyPeer(input_peer),
            settings=InputPeerNotifySettings(mute_until=2147483647)
        ))
    except Exception:
        pass

async def add_peer_to_folder(client: TelegramClient, folder_name: str, peer: Any):
    try:
        input_peer = await client.get_input_entity(peer)
        result = await client(GetDialogFiltersRequest())
        filters = getattr(result, "filters", [])
        target_clean = folder_name.lower().strip()

        target_filter = None
        max_id = 1

        for f in filters:
            fid = getattr(f, "id", 0)
            if fid > max_id:
                max_id = fid
            if isinstance(f, (DialogFilter, DialogFilterChatlist)) and _extract_folder_title(f).lower() == target_clean:
                target_filter = f
                break

        if target_filter:
            current_peers = list(getattr(target_filter, "include_peers", []))
            peer_ids = {utils.get_peer_id(p) for p in current_peers}
            new_id = utils.get_peer_id(input_peer)
            if new_id not in peer_ids:
                current_peers.append(input_peer)
                target_filter.include_peers = current_peers
                await client(UpdateDialogFilterRequest(
                    id=target_filter.id,
                    filter=target_filter
                ))
        else:
            new_id = max_id + 1
            new_filter = DialogFilter(
                id=new_id,
                title=TextWithEntities(text=folder_name, entities=[]),
                pinned_peers=[],
                include_peers=[input_peer],
                exclude_peers=[]
            )
            await client(UpdateDialogFilterRequest(id=new_id, filter=new_filter))
    except Exception:
        pass

async def join_and_silence_target(
    client: TelegramClient,
    target: str,
    folder_name: str
) -> bool:
    try:
        entity = None
        clean = target.strip()
        if clean.startswith("+") or "joinchat/" in clean:
            invite_hash = clean.split("+")[-1].split("joinchat/")[-1]
            updates = await client(ImportChatInviteRequest(invite_hash))
            chats = getattr(updates, "chats", [])
            if chats:
                entity = chats[0]
        else:
            username = clean.lstrip("@").split("/")[-1].split("?")[0]
            entity = await client.get_entity(username)
            if getattr(entity, "bot", False):
                start_param = ""
                if "?start=" in clean:
                    start_param = clean.split("?start=")[-1]
                await client.send_message(entity, f"/start {start_param}".strip())
            else:
                await client(JoinChannelRequest(entity))

        if entity:
            await mute_peer(client, entity)
            await add_peer_to_folder(client, folder_name, entity)
            return True
    except Exception:
        pass
    return False

async def clear_spam_mention(client: TelegramClient, chat_peer: Any, message: Any):
    input_peer = None
    from telethon.tl.types import (
        TypeInputPeer,
        InputPeerChannel,
        InputPeerChat,
        InputPeerUser,
        InputChannel,
    )
    if isinstance(chat_peer, (TypeInputPeer, InputPeerChannel, InputPeerChat, InputPeerUser)):
        input_peer = chat_peer
    elif hasattr(message, "get_input_chat"):
        try:
            input_peer = await message.get_input_chat()
        except Exception:
            pass
    if not input_peer and hasattr(message, "input_chat"):
        input_peer = message.input_chat
    if not input_peer:
        try:
            input_peer = await client.get_input_entity(chat_peer)
        except Exception:
            try:
                input_peer = await client.get_entity(chat_peer)
            except Exception:
                pass
    if not input_peer:
        return

    msg_id = getattr(message, "id", 0)

    # Forum topic detection (support both reply_to_top_id and reply_to_msg_id when forum_topic is true)
    reply_to = getattr(message, "reply_to", None)
    top_id = getattr(reply_to, "reply_to_top_id", None)
    if not top_id and getattr(reply_to, "forum_topic", False):
        top_id = getattr(reply_to, "reply_to_msg_id", None)

    # 1. Clear mentions
    try:
        if top_id:
            await client(ReadMentionsRequest(peer=input_peer, top_msg_id=top_id))
        await client(ReadMentionsRequest(peer=input_peer))
    except Exception:
        pass

    # 2. Clear reactions
    try:
        from telethon.tl.functions.messages import ReadReactionsRequest
        if top_id:
            await client(ReadReactionsRequest(peer=input_peer, top_msg_id=top_id))
        await client(ReadReactionsRequest(peer=input_peer))
    except Exception:
        pass

    # 3. Mark read history
    if msg_id:
        try:
            from telethon import utils
            if isinstance(input_peer, (InputPeerChannel, InputChannel)):
                input_chan = utils.get_input_channel(input_peer)
                from telethon.tl.functions.channels import ReadHistoryRequest as ChannelReadHistoryRequest
                await client(ChannelReadHistoryRequest(channel=input_chan, max_id=msg_id))
            else:
                from telethon.tl.functions.messages import ReadHistoryRequest as MessagesReadHistoryRequest
                await client(MessagesReadHistoryRequest(peer=input_peer, max_id=msg_id))
        except Exception:
            pass

    # 4. High-level acknowledge with mention and reaction clearing
    try:
        await client.send_read_acknowledge(input_peer, max_id=msg_id, clear_mentions=True, clear_reactions=True)
    except Exception:
        pass

class BroadcasterService:
    _ACTIVE_INSTANCES: Dict[int, 'BroadcasterService'] = {}

    def __init__(self, client: TelegramClient, account_id: int = 0):
        self.client = client
        self.account_id = account_id
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self._sending_chats: Set[int] = set()
        self._last_sent_chat: Dict[int, float] = {}

    def start(self):
        # Stop any previous active instance for this account to prevent duplicate background loops
        old = BroadcasterService._ACTIVE_INSTANCES.get(self.account_id)
        if old and old is not self:
            log_info(f"[LIFECYCLE] Остановка предыдущего экземпляра BroadcasterService для аккаунта {self.account_id}")
            old.stop()
        BroadcasterService._ACTIVE_INSTANCES[self.account_id] = self

        if self.is_running and self._task and not self._task.done():
            log_info(f"[LIFECYCLE] BroadcasterService уже запущен для аккаунта {self.account_id}.")
            return
        self.is_running = True
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._loop())
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    self._task = loop.create_task(self._loop())
            except Exception:
                pass

    def stop(self):
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._sending_chats.clear()
        if BroadcasterService._ACTIVE_INSTANCES.get(self.account_id) is self:
            BroadcasterService._ACTIVE_INSTANCES.pop(self.account_id, None)

    async def _loop(self):
        import gc
        last_gc = time.time()
        last_collector_clean = time.time()

        while self.is_running:
            try:
                now = time.time()

                # Clean expired collector sessions every 30s instead of every second
                if now - last_collector_clean > 30:
                    expired = [k for k, s in ACTIVE_COLLECTORS.items() if now - s.get("last_active", 0) > 300]
                    for k in expired:
                        ACTIVE_COLLECTORS.pop(k, None)
                    last_collector_clean = now

                # Periodic memory cleanup for mobile (every 10 minutes)
                if now - last_gc > 600:
                    gc.collect()
                    last_gc = now

                tasks = db.get_active_interval_tasks(account_id=self.account_id)
                for task in tasks:
                    mode = task.get("mode", "interval")
                    last_sent = task.get("last_sent_at", 0)
                    interval = task.get("interval_seconds", 0)
                    chat_id = task["chat_id"]

                    # Skip if chat is currently in-flight
                    if chat_id in self._sending_chats:
                        continue

                    if mode == "hybrid":
                        current_count = task.get("current_count", 0)
                        threshold = task.get("counter_threshold", 1)
                        if (now - last_sent >= interval) and (current_count >= threshold):
                            template_name = task["template_name"]

                            rotation_tpl = db.get_next_rotation_template(chat_id, account_id=self.account_id, rotation_name=template_name)
                            effective_name = rotation_tpl if rotation_tpl else template_name

                            # ATOMIC PRE-RESET: atomically reset count and update last_sent_at before network I/O
                            # so handle_counter_event does not trigger simultaneously during send_broadcast_post
                            db.reset_hybrid_task(task["id"])

                            log_info(f"[HYBRID] Чат {chat_id}: набрано {current_count}/{threshold} соо и прошло {interval}с. Отправляю '{effective_name}'...")
                            success = await self._send_task(task["id"], chat_id, effective_name, task=task)
                            if not success:
                                db.restore_counter(task["id"], threshold, last_sent_at=now - interval + 15)
                            await asyncio.sleep(1.5)
                    else:
                        if now - last_sent >= interval:
                            template_name = task["template_name"]

                            rotation_tpl = db.get_next_rotation_template(chat_id, account_id=self.account_id, rotation_name=template_name)
                            effective_name = rotation_tpl if rotation_tpl else template_name

                            success = await self._send_task(task["id"], chat_id, effective_name, task=task)
                            if success:
                                db.update_task_last_sent(task["id"])
                            else:
                                with db._conn() as conn:
                                    conn.execute(
                                        "UPDATE broadcast_tasks SET last_sent_at = ? WHERE id = ?",
                                        (now - interval + 15, task["id"])
                                    )
                            # Small delay between different chats in interval batch to avoid flood ban
                            await asyncio.sleep(1.5)

                # Dynamic sleep to prevent CPU/battery drain on phone:
                # If there are no interval/hybrid tasks, sleep 5.0 seconds.
                # If tasks exist, sleep dynamically up to 5.0 seconds based on when the next task is due.
                if not tasks:
                    await asyncio.sleep(5.0)
                else:
                    min_wait = 5.0
                    for task in tasks:
                        interval = task.get("interval_seconds", 0)
                        last_sent = task.get("last_sent_at", 0)
                        time_left = interval - (time.time() - last_sent)
                        if time_left <= 0:
                            min_wait = 1.0
                            break
                        else:
                            min_wait = min(min_wait, time_left)
                    await asyncio.sleep(max(1.0, min(min_wait, 5.0)))

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(3.0)

    async def handle_counter_event(self, chat_id: int):
        # Skip counter event check if chat is already in-flight sending
        if chat_id in self._sending_chats:
            log_info(f"[DEDUP] Чат {chat_id}: пропуск события счетчика (отправка уже в процессе).")
            return

        ready_tasks = db.increment_and_check_counter(chat_id, account_id=self.account_id)
        for task in ready_tasks:
            template_name = task["template_name"]
            rotation_tpl = db.get_next_rotation_template(chat_id, account_id=self.account_id, rotation_name=template_name)
            effective_name = rotation_tpl if rotation_tpl else template_name

            mode_label = "ГИБРИД" if task.get("mode") == "hybrid" else "СЧЕТЧИК"
            log_info(f"[{mode_label}] Чат {chat_id}: условие выполнено ({task['counter_threshold']} соо). Отправляю пост '{effective_name}'...")
            success = await self._send_task(task["id"], chat_id, effective_name, task=task)
            if not success:
                db.restore_counter(task["id"], task["counter_threshold"])

    async def _send_task(self, task_id: int, chat_id: int, template_name: str, task: Optional[Dict[str, Any]] = None) -> bool:
        template = db.get_template(template_name)
        if not template:
            log_error(f"[ERROR] Шаблон '{template_name}' не найден в базе данных")
            return False

        # --- DEDUPLICATION GUARD 1: In-flight chat lock ---
        if chat_id in self._sending_chats:
            log_info(f"[DEDUP] Чат {chat_id}: отправка уже выполняется! Блокирую параллельный дубликат.")
            return False

        # --- DEDUPLICATION GUARD 2: Cooldown debounce per chat (minimum 4.0s between broadcasts) ---
        now_ts = time.time()
        last_sent_ts = self._last_sent_chat.get(chat_id, 0.0)
        if (now_ts - last_sent_ts) < 4.0:
            log_info(f"[DEBOUNCE] Чат {chat_id}: прошло всего {now_ts - last_sent_ts:.1f}с с прошлой отправки. Блокирую дубликат.")
            return False

        self._sending_chats.add(chat_id)
        try:
            try:
                peer = await self.client.get_input_entity(chat_id)
            except Exception:
                peer = await self.client.get_entity(chat_id)
            await send_broadcast_post(
                self.client,
                peer,
                text=template["text"],
                entities_hex=template["entities_hex"],
                media_files=template["media_files"],
                bundle=template.get("bundle")
            )
            self._last_sent_chat[chat_id] = time.time()
            return True
        except errors.SlowModeWaitError as e:
            log_error(f"[SLOWMODE] Медленный режим в чате {chat_id}: нужно подождать {e.seconds}с")
            if task and task.get("mode") in ("interval", "hybrid"):
                try:
                    with db._conn() as conn:
                        conn.execute("UPDATE broadcast_tasks SET last_sent_at = ? WHERE id = ?", (time.time() - task.get("interval_seconds", 0) + e.seconds + 2, task_id))
                except Exception:
                    pass
            return False
        except errors.FloodWaitError as e:
            log_error(f"[FLOOD] Задержка FloodWait {e.seconds}с в чате {chat_id}")
            await asyncio.sleep(min(e.seconds + 1, 60))
            return False
        except (errors.UserBannedInChannelError, errors.ChannelPrivateError) as e:
            log_error(f"[PERM] Аккаунт удален/забанен в чате {chat_id} ({type(e).__name__}). Рассылка отключена.")
            db.set_broadcast_status(name=template_name, chat_id=chat_id, is_active=0, account_id=self.account_id)
            return False
        except errors.ChatWriteForbiddenError as e:
            log_error(f"[RESTRICT] Чат {chat_id}: отправка временно ограничена администрацией ({e}). Рассылка не отключена, повтор позже.")
            return False
        except Exception as e:
            log_error(f"[ERROR] Ошибка отправки рассылки в чат {chat_id}: {e}")
            return False
        finally:
            self._sending_chats.discard(chat_id)

HELP_TEXT = """
**⚙️ Управление юзерботом**

**1. Обычная рассылка (одно сообщение или альбом):**
• `.рассыл каждое 15 секунд <название>`
• `.рассыл каждые 10 минут <название> "папка"`
• `.рассыл через 1 <название>`
• `.рассыл через 3 <название> "папка"`
• `.рассыл через 10 соо минимум 10 минут <название>` — **совмещенный (гибридный) режим**: отправка через 10 сообщений, но не чаще раза в 10 минут
• `.рассыл комби 10 10м <название> "папка"` — краткая форма комбинированного режима
• Английские аналоги: `.broadcast every 15s <name>`, `.broadcast after 10 <name>`, `.broadcast hybrid 10 10m <name>`
*(Текст можно писать со 2-й строки или отправить команду в ответ на готовое сообщение)*

**2. Рассылка пересланных сообщений (с каналов / чатов):**
• `.рассыл-пересланное каждое 15 секунд <название>`
• `.рассыл-пересланное через 10 соо минимум 10 минут <название>`
• Английский аналог: `.broadcast-forwarded`
*(Бот включит сбор: пересылайте любые посты в чат, затем отправьте `.закрыть`)*

**3. Рассылка нескольких сообщений подряд (пачкой):**
• `.рассыл через 10 10м <название> "папка" -мульти` — быстрый запуск сбора пачки постов
• `.рассыл-несколько каждое 15 секунд <название>`
• `.рассыл-несколько через 10 соо минимум 10 минут <название>`
• Английские аналоги: `.broadcast-multi`, `.broadcast-multiple`
*(Бот включит сбор: отправляйте любые сообщения, медиа, премиум-эмодзи, затем отправьте `.закончить`)*

**4. Завершение и отмена сбора:**
• `.закончить` (или `.закрыть`, `.close`) — сохранить сообщения и запустить рассылку
• `.отменить` (или `.cancel`) — отменить текущую сессию сбора
*(Тайм-аут бездействия: 5 минут)*

**5. Чередование постов (ротации):**
• `.чередовать <название> <пост1> <пост2>...` — создать именованное чередование
• `.чередование <название> <пост1> <пост2>...`
• `.чередования` (или `.ротации`, `.rotations`) — посмотреть все чередования и их порядок
• `.удалить-чередование <название>` (или `.delete-rotation <name>`) — удалить чередование
• `.чередовать` (со следующей строки названия постов) — настроить чередование только для текущего чата
*(Имя чередования можно сразу передавать в `.рассыл`, например: `.рассыл через 10 соо минимум 10 минут связка1 "папка"`)*

**6. Управление статусом:**
• `.стоп <название>` / `.stop <name>`
• `.стоп <название> "папка"`
• `.стоп все` / `.stop all`
• `.продолжить <название>` / `.resume <name>`
• `.продолжить все` / `.resume all`
• `.удалить <название>` / `.delete <name>`
• `.удалить все` / `.delete all`

**7. Просмотр и статистика:**
• `.просмотр <название>` / `.view <name>` — тестовая отправка поста со всеми медиа (или проверка чередования)
• `.просмотр все` / `.view all` — список всех рассылок и чередований в этом чате

**8. Антиспам и авто-подписка:**
• Моментально гасит спам-пинги и пуши на телефон от скупов и авто-ботов.
• Автоматически вступает в каналы/боты по требованию капчи админов, мьютит их и убирает в папку `автосабнутое`.
"""

def _extract_collector_flag(text: str) -> Tuple[Optional[str], str]:
    flag_fwd = re.compile(r'(?:^|\s)(?:--?пересланн\w*|--?fwd|--?forwarded)(?=\s|$)', re.IGNORECASE)
    flag_multi = re.compile(r'(?:^|\s)(?:--?мульти|--?multi|--?пачк\w*|--?несколько)(?=\s|$)', re.IGNORECASE)

    if flag_fwd.search(text):
        cleaned = flag_fwd.sub(' ', text).strip()
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return 'forwarded', cleaned

    if flag_multi.search(text):
        cleaned = flag_multi.sub(' ', text).strip()
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return 'multi', cleaned

    return None, text

def _extract_quoted_folder(text: str) -> Tuple[str, str]:
    match = re.search(r'"([^"]+)"', text)
    if match:
        folder = match.group(1).strip()
        cleaned = text[:match.start()] + text[match.end():]
        return folder, cleaned.strip()
    return "", text.strip()

def _format_seconds(sec: int) -> str:
    if sec <= 0:
        return "0с"
    if sec % 86400 == 0:
        return f"{sec // 86400} д"
    if sec % 3600 == 0:
        return f"{sec // 3600} ч"
    if sec % 60 == 0:
        return f"{sec // 60} мин"
    return f"{sec}с"

def _parse_time_unit(val_str: str, unit_str: Optional[str]) -> Optional[int]:
    val = int(val_str)
    if not unit_str:
        return val
    u = unit_str.lower()
    if u.startswith(("с", "s")):
        return val
    if u.startswith(("м", "m")):
        return val * 60
    if u.startswith(("ч", "h")):
        return val * 3600
    if u.startswith(("д", "d")):
        return val * 86400
    return val

def _parse_interval_str(s: str) -> Optional[int]:
    clean = s.strip().lower()
    m = re.match(r"^(\d+)\s*(секунд\w*|сек|с|s|минут\w*|мин|м|m|час\w*|ч|h|дн\w*|день|д|d)?$", clean)
    if m:
        return _parse_time_unit(m.group(1), m.group(2))
    return None

def parse_broadcast_params(first_line: str) -> Tuple[str, int, int, str, str, str]:
    _, unflagged_line = _extract_collector_flag(first_line)
    folder_name, remaining_first_line = _extract_quoted_folder(unflagged_line)
    text = remaining_first_line.strip()
    tokens = text.split()

    if len(tokens) < 3:
        return "", 0, 0, "", "", "⚠️ Ошибка синтаксиса. Напишите `.инструкция`"

    sub_cmd = tokens[1].lower()
    mode = ""
    interval_sec = 0
    threshold_count = 0
    template_name = ""

    time_units = r"секунд\w*|сек|с|s|минут\w*|мин|м|m|час\w*|ч|h|дн\w*|день|д|d"

    # Match hybrid/combined broadcast pattern first:
    # e.g.:
    # .рассыл через 10 соо минимум 10 минут <название>
    # .рассыл через 10 соо кд 10м <название>
    # .рассыл через 10 10м <название>
    # .рассыл комби 10 10м <название>
    # .рассыл гибрид 10 10м <название>
    pattern_hybrid_kw = re.compile(
        rf"^(?:через|after)\s+(\d+)(?:\s*(?:соо\w*|сообщени\w*|messages?|msg\w*))?"
        rf"\s+(?:но\s+что\s*бы\s+|но\s+|что\s*бы\s+)?(?:кд|cd|минимум|min|пауза|задержка|от|не\s+чаще)\s*(\d+)\s*({time_units})?"
        rf"(?:\s+(?:прошло|пройдет|было|истекло))?"
        rf"\s+(.+)$",
        re.IGNORECASE
    )
    pattern_hybrid_unit = re.compile(
        rf"^(?:через|after)\s+(\d+)(?:\s*(?:соо\w*|сообщени\w*|messages?|msg\w*))?"
        rf"\s+(\d+)\s*({time_units})"
        rf"(?:\s+(?:прошло|пройдет|было|истекло))?"
        rf"\s+(.+)$",
        re.IGNORECASE
    )
    pattern_hybrid_direct = re.compile(
        rf"^(?:комби|комбинированное|гибрид|hybrid)\s+(\d+)(?:\s*(?:соо\w*|сообщени\w*|messages?|msg\w*))?"
        rf"\s+(?:(?:но\s+что\s*бы\s+|но\s+|что\s*бы\s+)?(?:кд|cd|минимум|min|пауза|задержка|от|не\s+чаще)\s*)?(\d+)\s*({time_units})?"
        rf"(?:\s+(?:прошло|пройдет|было|истекло))?"
        rf"\s+(.+)$",
        re.IGNORECASE
    )

    args_text = " ".join(tokens[1:])
    m_hyb = pattern_hybrid_kw.match(args_text) or pattern_hybrid_unit.match(args_text) or pattern_hybrid_direct.match(args_text)
    if m_hyb:
        count_str, time_str, unit_str, name_part = m_hyb.groups()
        try:
            threshold_count = int(count_str)
        except ValueError:
            return "", 0, 0, "", "", "⚠️ Число сообщений должно быть числом."
        interval_sec = _parse_time_unit(time_str, unit_str)
        if threshold_count <= 0:
            return "", 0, 0, "", "", "⚠️ Число сообщений должно быть > 0."
        if not interval_sec or interval_sec <= 0:
            return "", 0, 0, "", "", "⚠️ Неверно указано минимальное время задержки."
        template_name = name_part.strip("<>\"'").strip()
        if not template_name:
            return "", 0, 0, "", "", "⚠️ Не указано название рассылки."
        return "hybrid", interval_sec, threshold_count, template_name, folder_name, ""

    if sub_cmd in ("каждое", "каждые", "каждый", "every"):
        time_part = tokens[2]
        name_idx = 3
        if len(tokens) >= 4 and not tokens[3].startswith('"'):
            if not re.search(r"[a-zA-Zа-яА-Я]", time_part):
                time_part = f"{tokens[2]} {tokens[3]}"
                name_idx = 4

        interval_sec = _parse_interval_str(time_part)
        if not interval_sec or interval_sec <= 0:
            return "", 0, 0, "", "", "⚠️ Неверно указано время интервала."

        mode = "interval"
        if len(tokens) > name_idx:
            template_name = tokens[name_idx].strip("<>").strip()

    elif sub_cmd in ("через", "after"):
        try:
            threshold_count = int(tokens[2])
        except ValueError:
            return "", 0, 0, "", "", "⚠️ Число сообщений должно быть числом."

        if threshold_count <= 0:
            return "", 0, 0, "", "", "⚠️ Число сообщений должно быть > 0."

        mode = "counter"
        name_idx = 3
        if len(tokens) > 3 and re.match(r"^(?:соо\w*|сообщени\w*|messages?|msg\w*)$", tokens[3], re.IGNORECASE):
            name_idx = 4
        if len(tokens) > name_idx:
            template_name = tokens[name_idx].strip("<>").strip()
    else:
        return "", 0, 0, "", "", "⚠️ Неизвестный режим рассылки (ожидается: каждое / через / комби)."

    if not template_name:
        return "", 0, 0, "", "", "⚠️ Не указано название рассылки."

    return mode, interval_sec, threshold_count, template_name, folder_name, ""

async def _notify(event: Any, text: str, auto_delete: int = 5, force_respond: bool = False):
    msg = None
    try:
        if getattr(event, "out", False) and not getattr(event.message, "media", None) and not force_respond:
            msg = await event.edit(text)
        else:
            msg = await event.respond(text)
    except Exception:
        try:
            msg = await event.respond(text)
        except Exception as e:
            log_error(f"[ERROR] Ошибка отправки ответа на команду: {e}")

    if msg and hasattr(msg, "id"):
        _BOT_SYSTEM_MSG_IDS.add(msg.id)
        if len(_BOT_SYSTEM_MSG_IDS) > 2000:
            try:
                _BOT_SYSTEM_MSG_IDS.pop()
            except Exception:
                pass

    if auto_delete > 0 and msg:
        async def _delayed_delete(target_msg: Any, delay: int):
            try:
                await asyncio.sleep(delay)
                await target_msg.delete()
            except Exception:
                pass
        asyncio.create_task(_delayed_delete(msg, auto_delete))

class MessageDedupRing:
    """O(1) ring buffer for message deduplication across reconnects and burst updates."""
    def __init__(self, maxsize: int = 2000):
        self.maxsize = maxsize
        self._deque: Deque[Tuple[int, int]] = collections.deque()
        self._set: Set[Tuple[int, int]] = set()

    def check_and_add(self, chat_id: int, msg_id: int) -> bool:
        """Returns True if already seen (duplicate), False if new."""
        key = (chat_id, msg_id)
        if key in self._set:
            return True
        if len(self._deque) >= self.maxsize:
            old = self._deque.popleft()
            self._set.discard(old)
        self._deque.append(key)
        self._set.add(key)
        return False

_GLOBAL_DEDUP_RING = MessageDedupRing(2000)

def register_events(client: TelegramClient, broadcaster: BroadcasterService, my_id: int, acc_cfg: Optional[Dict[str, Any]] = None):
    # Always keep client's broadcaster reference current
    client._spambuster_broadcaster = broadcaster

    # Guard against duplicate handler registration on client reconnection or re-init
    if getattr(client, "_spambuster_events_registered", False):
        log_info(f"[LIFECYCLE] Обработчики событий уже зарегистрированы для клиента {my_id}. Пропуск повторной регистрации.")
        return

    client._spambuster_events_registered = True

    if acc_cfg is None:
        acc_cfg = CONFIG

    auto_read_spam = acc_cfg.get("auto_read_spam_pings", True)
    auto_sub = acc_cfg.get("auto_sub_enabled", True)
    auto_folder = acc_cfg.get("auto_sub_folder", "автосабнутое")

    @client.on(events.NewMessage(incoming=True))
    async def incoming_handler(event: Any):
        chat_id = event.chat_id
        msg_id = getattr(event, "id", None)
        if msg_id and chat_id:
            if _GLOBAL_DEDUP_RING.check_and_add(chat_id, msg_id):
                return

        active_broadcaster = getattr(client, "_spambuster_broadcaster", broadcaster)
        await active_broadcaster.handle_counter_event(chat_id)

        if event.is_private:
            return

        text = event.raw_text or ""

        if auto_read_spam:
            is_relevant_ping = event.mentioned
            if not is_relevant_ping and event.is_reply:
                try:
                    reply_msg = await event.get_reply_message()
                    if reply_msg and reply_msg.sender_id == my_id:
                        is_relevant_ping = True
                except Exception:
                    pass

            # Auto-clear if the message is detected as spam, or if it pinged us and matches spam checks
            target_peer = getattr(event, "input_chat", None) or chat_id
            if should_auto_read(text, event.message):
                await clear_spam_mention(client, target_peer, event.message)
            elif is_relevant_ping and is_spam_message(text, event.message)[0]:
                await clear_spam_mention(client, target_peer, event.message)

        if auto_sub and is_gatekeeper_text(text):
            is_targeted = event.mentioned
            if not is_targeted and event.is_reply:
                try:
                    reply_msg = await event.get_reply_message()
                    if reply_msg and reply_msg.sender_id == my_id:
                        is_targeted = True
                except Exception:
                    pass

            if is_targeted or str(my_id) in text:
                targets = set(extract_channel_links(text))
                verify_coords = None

                if event.message.reply_markup and hasattr(event.message.reply_markup, "rows"):
                    for r_idx, row in enumerate(event.message.reply_markup.rows):
                        for c_idx, button in enumerate(row.buttons):
                            url = getattr(button, "url", None)
                            if url:
                                targets.update(extract_channel_links(url))
                            btn_text = getattr(button, "text", "")
                            if is_verify_button(btn_text):
                                verify_coords = (r_idx, c_idx)

                for target in targets:
                    await join_and_silence_target(client, target, auto_folder)

                if verify_coords:
                    try:
                        await event.message.click(*verify_coords)
                    except Exception:
                        pass

    @client.on(events.NewMessage())
    async def command_dispatcher(event: Any):
        if not getattr(event, "out", False) and event.sender_id != my_id:
            return

        msg_id = getattr(event, "id", None)
        if msg_id and msg_id in _BOT_SYSTEM_MSG_IDS:
            return

        raw = event.raw_text or ""
        if is_internal_bot_message(raw):
            if msg_id:
                _BOT_SYSTEM_MSG_IDS.add(msg_id)
            return

        chat_id = event.chat_id
        collector_key = (my_id, chat_id)

        if collector_key in ACTIVE_COLLECTORS or chat_id in ACTIVE_COLLECTORS:
            c_key = collector_key if collector_key in ACTIVE_COLLECTORS else chat_id
            session = ACTIVE_COLLECTORS[c_key]
            if time.time() - session.get("last_active", 0) > 300:
                ACTIVE_COLLECTORS.pop(c_key, None)
                if raw.strip().lower() in (
                    ".закрыть", ".close", "закрыть", "close",
                    ".закончить", ".завершить", "закончить", "завершить",
                    ".готово", "готово", ".done", "done",
                    ".отменить", ".cancel", "отменить", "cancel", ".отмена", "отмена"
                ):
                    await _notify(event, "⚠️ Время ожидания сообщений (5 минут) истекло. Сессия сброшена.")
                    return
            else:
                low = raw.strip().lower()
                if low in (
                    ".закрыть", ".close", "закрыть", "close",
                    ".закончить", ".завершить", "закончить", "завершить",
                    ".готово", "готово", ".done", "done",
                    ".finish", "finish", ".end", "end"
                ):
                    await finish_collector_session(event, session, c_key)
                    return
                elif low in (
                    ".отменить", ".cancel", "отменить", "cancel",
                    ".отмена", "отмена", ".стоп", "стоп"
                ):
                    template_name = session["template_name"]
                    target_dir = MEDIA_DIR / re.sub(r'[\\/*?:"<>|]', '_', template_name.strip("<>").strip())
                    if target_dir.exists():
                        shutil.rmtree(target_dir, ignore_errors=True)
                    ACTIVE_COLLECTORS.pop(c_key, None)
                    await _notify(event, f"❌ Настройка рассылки **{template_name}** отменена.")
                    return
                elif raw.startswith(".") and any(raw.lower().startswith(c) for c in (
                    ".рассыл", ".broadcast", ".чередовать", ".чередование", ".ротация", ".rotate", ".стоп", ".stop",
                    ".продолжить", ".resume", ".удалить", ".delete", ".просмотр", ".view",
                    ".инструкция", ".help", ".чередования", ".ротации", ".rotations",
                    ".закончить", ".завершить", ".готово"
                )):
                    ACTIVE_COLLECTORS.pop(c_key, None)
                else:
                    await collect_message_to_session(event, session)
                    return

        if not raw.startswith("."):
            return

        first_line = raw.splitlines()[0].strip()
        parts = first_line.split()
        if not parts:
            return

        cmd = parts[0].lower()
        log_info(f"[CMD][{my_id}] Распознана команда '{cmd}' в чате {event.chat_id}")

        if cmd in (".инструкция", ".help"):
            await _notify(event, HELP_TEXT, auto_delete=0)
            return

        if cmd in (".рассыл", ".broadcast"):
            await handle_broadcast_create(event, parts, raw)
            return

        if cmd in (".рассыл-пересланное", ".broadcast-forwarded"):
            await handle_collector_start(event, raw, session_type="forwarded")
            return

        if cmd in (".рассыл-несколько", ".рассыл-мульти", ".broadcast-multi", ".broadcast-multiple"):
            await handle_collector_start(event, raw, session_type="multi")
            return

        if cmd in (".чередовать", ".чередование", ".ротация", ".создать-чередование", ".rotate", ".rotation", ".create-rotation"):
            await handle_rotation_create(event, raw)
            return

        if cmd in (".удалить-чередование", ".удалить-ротацию", ".delete-rotation"):
            await handle_delete_rotation(event, parts)
            return

        if cmd in (".чередования", ".ротации", ".rotations"):
            await handle_list_rotations(event)
            return

        if cmd in (".стоп", ".stop"):
            await handle_stop(event, parts, raw)
            return

        if cmd in (".продолжить", ".resume"):
            await handle_resume(event, parts, raw)
            return

        if cmd in (".удалить", ".delete"):
            await handle_delete(event, parts, raw)
            return

        if cmd in (".просмотр", ".view"):
            await handle_view(event, parts)
            return

    async def handle_collector_start(event: Any, raw: str, session_type: str):
        mode, interval_sec, threshold_count, template_name, folder_name, err = parse_broadcast_params(raw.splitlines()[0])
        if err:
            await _notify(event, err)
            return

        chat_id = event.chat_id
        items = []

        clean_name = re.sub(r'[\\/*?:"<>|]', '_', template_name.strip("<>").strip())
        target_dir = MEDIA_DIR / clean_name
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)

        if event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg:
                grouped_id = getattr(reply_msg, "grouped_id", None)
                saved_media = []
                if reply_msg.media:
                    saved_media = await save_media_from_message(
                        client,
                        reply_msg,
                        template_name,
                        item_index=0,
                        clear_existing=False
                    )
                text = reply_msg.raw_text or ""
                entities_hex = serialize_entities(reply_msg.entities)
                is_fwd = bool(getattr(reply_msg, "fwd_from", None))
                items.append({
                    "text": text,
                    "entities_hex": entities_hex,
                    "media_files": saved_media,
                    "is_forward": is_fwd or (session_type == "forwarded"),
                    "forward_chat_id": reply_msg.chat_id,
                    "forward_msg_ids": [reply_msg.id],
                    "grouped_id": grouped_id
                })

        collector_key = (my_id, chat_id)
        ACTIVE_COLLECTORS[collector_key] = {
            "account_id": my_id,
            "chat_id": chat_id,
            "template_name": template_name,
            "mode": mode,
            "interval_seconds": interval_sec,
            "counter_threshold": threshold_count,
            "folder_name": folder_name,
            "session_type": session_type,
            "items": items,
            "started_at": time.time(),
            "last_active": time.time()
        }

        kind_desc = "пересланных сообщений" if session_type == "forwarded" else "сообщений пачкой"
        action_prompt = "Пересылайте сообщения" if session_type == "forwarded" else "Отправляйте или пересылайте сообщения"
        reply_note = f"\n📎 Сообщение из ответа уже добавлено как #1 (всего: {len(items)})." if items else ""

        await _notify(
            event,
            f"📥 **Режим сбора {kind_desc} начат!**\n"
            f"Имя рассылки: **{template_name}**\n"
            f"{action_prompt} в этот чат.{reply_note}\n\n"
            f"• Напишите `.закончить` (или `.закрыть`) — когда закончите добавление и нужно запустить рассылку.\n"
            f"• Напишите `.отменить` — чтобы прервать и сбросить.\n"
            f"⏱ Тайм-аут бездействия: 5 минут.",
            auto_delete=0
        )

    async def collect_message_to_session(event: Any, session: Dict[str, Any]):
        msg = event.message
        if getattr(msg, "id", None) in _BOT_SYSTEM_MSG_IDS:
            return

        text = msg.raw_text or ""
        if is_internal_bot_message(text):
            if hasattr(msg, "id"):
                _BOT_SYSTEM_MSG_IDS.add(msg.id)
            return

        session["last_active"] = time.time()
        template_name = session["template_name"]
        grouped_id = getattr(msg, "grouped_id", None)

        if grouped_id and session["items"] and session["items"][-1].get("grouped_id") == grouped_id:
            last_item = session["items"][-1]
            last_item["forward_msg_ids"].append(msg.id)
            if msg.media:
                saved = await save_media_from_message(
                    client,
                    msg,
                    template_name,
                    item_index=len(session["items"]) - 1,
                    clear_existing=False
                )
                last_item["media_files"].extend(saved)
            if (not last_item["text"]) and (msg.raw_text or msg.entities):
                last_item["text"] = msg.raw_text or ""
                last_item["entities_hex"] = serialize_entities(msg.entities)
            return

        item_index = len(session["items"])
        saved_media = []
        if msg.media:
            saved_media = await save_media_from_message(
                client,
                msg,
                template_name,
                item_index=item_index,
                clear_existing=False
            )

        entities_hex = serialize_entities(msg.entities)
        is_fwd = bool(getattr(msg, "fwd_from", None))

        item = {
            "text": text,
            "entities_hex": entities_hex,
            "media_files": saved_media,
            "is_forward": is_fwd or (session["session_type"] == "forwarded"),
            "forward_chat_id": event.chat_id,
            "forward_msg_ids": [msg.id],
            "grouped_id": grouped_id
        }
        session["items"].append(item)
        count = len(session["items"])
        await _notify(
            event,
            f"📥 Сообщение #{count} добавлено в рассылку **{template_name}**.\n"
            f"Отправьте следующее сообщение или напишите `.закрыть` (для отмены `.отменить`).",
            auto_delete=4,
            force_respond=True
        )

    async def finish_collector_session(event: Any, session: Dict[str, Any], session_key: Any = None):
        if session_key is None:
            session_key = (my_id, event.chat_id) if (my_id, event.chat_id) in ACTIVE_COLLECTORS else event.chat_id
        chat_id = event.chat_id
        template_name = session["template_name"]
        items = [it for it in session["items"] if not is_internal_bot_message(it.get("text", ""))]
        session["items"] = items

        if not items:
            clean_name = re.sub(r'[\\/*?:"<>|]', '_', template_name.strip("<>").strip())
            target_dir = MEDIA_DIR / clean_name
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            ACTIVE_COLLECTORS.pop(session_key, None)
            await _notify(event, "⚠️ Не было добавлено ни одного сообщения. Сессия закрыта.")
            return

        first = items[0]
        root_text = first.get("text", "")
        root_entities = first.get("entities_hex", [])
        root_media = first.get("media_files", [])
        bundle_json = json.dumps(items)

        db.save_template(template_name, root_text, root_entities, root_media, bundle_json=bundle_json)

        folder_name = session["folder_name"]
        mode = session["mode"]
        interval_sec = session["interval_seconds"]
        threshold_count = session["counter_threshold"]

        target_chats = []
        if folder_name:
            target_chats = await get_chats_in_folder(client, folder_name)
            if not target_chats:
                ACTIVE_COLLECTORS.pop(session_key, None)
                await _notify(event, f"⚠️ Папка \"{folder_name}\" не найдена или пуста.")
                return
        else:
            target_chats = [chat_id]

        for cid in target_chats:
            db.add_or_update_broadcast(
                account_id=my_id,
                chat_id=cid,
                template_name=template_name,
                mode=mode,
                interval_seconds=interval_sec,
                counter_threshold=threshold_count,
                folder_name=folder_name
            )

        ACTIVE_COLLECTORS.pop(session_key, None)

        if mode == "interval":
            details = f"каждые {_format_seconds(interval_sec)}"
        elif mode == "hybrid":
            details = f"через {threshold_count} соо (кд {_format_seconds(interval_sec)})"
        else:
            details = f"через каждые {threshold_count} соо"
        target_info = f"в папке \"{folder_name}\" ({len(target_chats)} чатов)" if folder_name else "в этом чате"
        kind_str = "пересланных сообщений" if session["session_type"] == "forwarded" else "сообщений пачкой"
        await _notify(
            event,
            f"✅ Рассылка {kind_str} **{template_name}** успешно запущена!\n"
            f"📦 Сообщений в пачке: {len(items)}\n"
            f"⏱ Режим: {details} {target_info}."
        )

    async def handle_broadcast_create(event: Any, parts: List[str], raw: str):
        flag, cleaned_first_line = _extract_collector_flag(raw.splitlines()[0])
        if flag:
            other_lines = raw.splitlines()[1:]
            cleaned_raw = "\n".join([cleaned_first_line] + other_lines)
            await handle_collector_start(event, cleaned_raw, session_type=flag)
            return

        mode, interval_sec, threshold_count, template_name, folder_name, err = parse_broadcast_params(raw.splitlines()[0])
        if err:
            await _notify(event, err)
            return

        text = ""
        entities_hex = []
        media_files = []

        if event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg:
                if getattr(reply_msg, "grouped_id", None):
                    album_messages = []
                    async for m in client.iter_messages(reply_msg.chat_id, limit=20):
                        if getattr(m, "grouped_id", None) == reply_msg.grouped_id:
                            album_messages.append(m)
                    album_messages.sort(key=lambda x: x.id)

                    caption_msg = reply_msg
                    for m in album_messages:
                        if m.raw_text or m.entities:
                            caption_msg = m
                            break
                    text = caption_msg.raw_text or ""
                    entities_hex = serialize_entities(caption_msg.entities)
                else:
                    text = reply_msg.raw_text or ""
                    entities_hex = serialize_entities(reply_msg.entities)

                media_files = await save_media_from_message(client, reply_msg, template_name, clear_existing=True)

            cmd_text, cmd_entities = extract_payload_and_entities(event.message, raw, folder_name)
            if cmd_text:
                text = cmd_text
                entities_hex = cmd_entities
        else:
            text, entities_hex = extract_payload_and_entities(event.message, raw, folder_name)
            if getattr(event.message, "media", None):
                media_files = await save_media_from_message(client, event.message, template_name, clear_existing=True)

        existing = db.get_template(template_name)
        named_rot = db.get_named_rotation(template_name, account_id=my_id)
        if not text and not media_files:
            if existing:
                text = existing["text"]
                entities_hex = existing["entities_hex"]
                media_files = existing["media_files"]
            elif named_rot:
                pass
            else:
                await _notify(event, "⚠️ Нет текста или медиа. Ответьте командой на сообщение с рекламой или укажите текст со следующей строки.")
                return

        if text or media_files:
            db.save_template(template_name, text, entities_hex, media_files)

        target_chats = []
        if folder_name:
            target_chats = await get_chats_in_folder(client, folder_name)
            if not target_chats:
                await _notify(event, f"⚠️ Папка \"{folder_name}\" не найдена или пуста.")
                return
        else:
            target_chats = [event.chat_id]

        for cid in target_chats:
            db.add_or_update_broadcast(
                account_id=my_id,
                chat_id=cid,
                template_name=template_name,
                mode=mode,
                interval_seconds=interval_sec,
                counter_threshold=threshold_count,
                folder_name=folder_name
            )

        if mode == "interval":
            details = f"каждые {_format_seconds(interval_sec)}"
        elif mode == "hybrid":
            details = f"через {threshold_count} соо (кд {_format_seconds(interval_sec)})"
        else:
            details = f"через каждые {threshold_count} соо"

        rot_note = f" (чередование **{template_name}**)" if named_rot else ""
        target_info = f"в папке \"{folder_name}\" ({len(target_chats)} чатов)" if folder_name else "в этом чате"
        await _notify(event, f"✅ Рассылка{rot_note} **{template_name}** запущена ({details}) {target_info}.")

    async def handle_rotation_create(event: Any, raw: str):
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        first_line = lines[0]
        cmd_tokens = first_line.split()
        cmd = cmd_tokens[0].lower()

        if len(lines) > 1:
            if len(cmd_tokens) > 1:
                name = cmd_tokens[1].strip("<>:").strip()
                templates = [l.strip("<>").strip() for l in lines[1:] if l.strip("<>").strip()]
                if not templates:
                    await _notify(event, "⚠️ Укажите названия рассылок для чередования со следующих строк.")
                    return
                db.set_named_rotation(name, templates, chat_id=event.chat_id, account_id=my_id)
                joined = " ➔ ".join(templates)
                await _notify(event, f"✅ Создано чередование **{name}**:\n{joined}\n\nЗапуск: `.рассыл <время/соо> {name}`")
                return
            else:
                templates = [l.strip("<>").strip() for l in lines[1:] if l.strip("<>").strip()]
                if not templates:
                    await _notify(event, "⚠️ Укажите названия рассылок для чередования со следующих строк.")
                    return
                db.set_chat_rotation(event.chat_id, templates, account_id=my_id)
                joined = " ➔ ".join(templates)
                await _notify(event, f"✅ Чередование настроено для этого чата:\n{joined}")
                return
        else:
            args = cmd_tokens[1:]
            if not args:
                await _notify(event, "⚠️ Использование:\n• `.чередовать <название> <пост1> <пост2>...`\n• `.чередование <название> <пост1> <пост2>...`\n• `.чередовать` (со следующей строки посты для этого чата)")
                return

            is_explicit_named_cmd = cmd in (".чередование", ".ротация", ".создать-чередование", ".create-rotation", ".rotation")
            if is_explicit_named_cmd or len(args) >= 3 or (len(args) == 2 and not (db.get_template(args[0]) and db.get_template(args[1]))):
                name = args[0].strip("<>:").strip()
                templates = [n.strip("<>").strip() for n in args[1:] if n.strip("<>").strip()]
                if not templates:
                    await _notify(event, f"⚠️ Укажите шаблоны для чередования **{name}**.")
                    return
                db.set_named_rotation(name, templates, chat_id=event.chat_id, account_id=my_id)
                joined = " ➔ ".join(templates)
                await _notify(event, f"✅ Создано чередование **{name}**:\n{joined}\n\nЗапуск: `.рассыл <время/соо> {name}`")
            else:
                templates = [n.strip("<>").strip() for n in args if n.strip("<>").strip()]
                db.set_chat_rotation(event.chat_id, templates, account_id=my_id)
                joined = " ➔ ".join(templates)
                await _notify(event, f"✅ Чередование настроено для этого чата:\n{joined}")

    async def handle_delete_rotation(event: Any, parts: List[str]):
        if len(parts) < 2:
            db.delete_chat_rotation(chat_id=event.chat_id, account_id=my_id)
            await _notify(event, "🗑 Чередование для этого чата удалено.")
            return

        target_name = parts[1].strip("<>").strip()
        if target_name.lower() in ("все", "all"):
            db.delete_all_named_rotations(account_id=my_id)
            db.delete_chat_rotation(chat_id=event.chat_id, account_id=my_id)
            await _notify(event, "🗑 Все чередования удалены.")
            return

        deleted = db.delete_named_rotation(target_name, account_id=my_id)
        if deleted:
            await _notify(event, f"🗑 Чередование **{target_name}** удалено.")
        else:
            rot = db.get_chat_rotation(event.chat_id, account_id=my_id)
            if rot:
                db.delete_chat_rotation(chat_id=event.chat_id, account_id=my_id)
                await _notify(event, f"🗑 Чередование для этого чата удалено.")
            else:
                await _notify(event, f"⚠️ Чередование **{target_name}** не найдено.")

    async def handle_list_rotations(event: Any):
        named = db.list_named_rotations(account_id=my_id)
        chat_rot = db.get_chat_rotation(event.chat_id, account_id=my_id)

        if not named and not chat_rot:
            await _notify(event, "🔄 Нет активных чередований.\nСоздать: `.чередовать <название> <пост1> <пост2>`")
            return

        lines = ["🔄 **Чередования постов:**\n"]
        if named:
            lines.append("**Именованные (для любых чатов/папок):**")
            for r in named:
                seq = " ➔ ".join(r["template_names"])
                curr = r["template_names"][r["current_index"] % len(r["template_names"])] if r["template_names"] else "—"
                lines.append(f"• **{r['name']}**: {seq} (след: `{curr}`)")

        if chat_rot and chat_rot.get("template_names"):
            lines.append("\n**Текущий чат:**")
            seq = " ➔ ".join(chat_rot["template_names"])
            curr = chat_rot["template_names"][chat_rot["current_index"] % len(chat_rot["template_names"])] if chat_rot["template_names"] else "—"
            lines.append(f"• {seq} (след: `{curr}`)")

        await _notify(event, "\n".join(lines), auto_delete=20)

    async def handle_stop(event: Any, parts: List[str], raw: str):
        folder_name, remaining = _extract_quoted_folder(raw)
        tokens = remaining.split()
        target_name = tokens[1].strip("<>").strip() if len(tokens) > 1 else "все"

        target_chats = await get_chats_in_folder(client, folder_name) if folder_name else []
        count = db.set_broadcast_status(
            account_id=my_id,
            name=target_name,
            chat_id=None if folder_name else event.chat_id,
            chat_ids=target_chats if folder_name else None,
            folder_name=folder_name,
            is_active=0
        )
        if target_name.lower() in ("все", "all"):
            db.set_rotation_status(
                account_id=my_id,
                chat_id=None if folder_name else event.chat_id,
                chat_ids=target_chats if folder_name else None,
                is_active=0
            )
        else:
            named_rot = db.get_named_rotation(target_name, account_id=my_id)
            if named_rot:
                with db._conn() as conn:
                    conn.execute("UPDATE chat_rotations SET is_active = 0 WHERE (name = ? OR name = ?) AND (account_id = ? OR account_id = 0)", (target_name, f"<{target_name}>", my_id))
        folder_str = f" в папке \"{folder_name}\"" if folder_name else ""
        await _notify(event, f"⏹ Остановлено рассылок: {count}{folder_str}")

    async def handle_resume(event: Any, parts: List[str], raw: str):
        folder_name, remaining = _extract_quoted_folder(raw)
        tokens = remaining.split()
        target_name = tokens[1].strip("<>").strip() if len(tokens) > 1 else "все"

        target_chats = await get_chats_in_folder(client, folder_name) if folder_name else []
        count = db.set_broadcast_status(
            account_id=my_id,
            name=target_name,
            chat_id=None if folder_name else event.chat_id,
            chat_ids=target_chats if folder_name else None,
            folder_name=folder_name,
            is_active=1
        )
        if target_name.lower() in ("все", "all"):
            db.set_rotation_status(
                account_id=my_id,
                chat_id=None if folder_name else event.chat_id,
                chat_ids=target_chats if folder_name else None,
                is_active=1
            )
        else:
            named_rot = db.get_named_rotation(target_name, account_id=my_id)
            if named_rot:
                with db._conn() as conn:
                    conn.execute("UPDATE chat_rotations SET is_active = 1 WHERE (name = ? OR name = ?) AND (account_id = ? OR account_id = 0)", (target_name, f"<{target_name}>", my_id))
        folder_str = f" в папке \"{folder_name}\"" if folder_name else ""
        await _notify(event, f"▶️ Возобновлено рассылок: {count}{folder_str}")

    async def handle_delete(event: Any, parts: List[str], raw: str):
        folder_name, remaining = _extract_quoted_folder(raw)
        tokens = remaining.split()
        target_name = tokens[1].strip("<>").strip() if len(tokens) > 1 else "все"

        target_chats = await get_chats_in_folder(client, folder_name) if folder_name else []
        count = db.delete_broadcasts(
            account_id=my_id,
            name=target_name,
            chat_id=None if folder_name else event.chat_id,
            chat_ids=target_chats if folder_name else None,
            folder_name=folder_name
        )
        if target_name.lower() in ("все", "all"):
            if not folder_name:
                for tpl in db.list_templates():
                    db.delete_template(tpl["name"])
                db.delete_chat_rotation(chat_id=event.chat_id, account_id=my_id)
                db.delete_all_named_rotations(account_id=my_id)
            else:
                db.delete_chat_rotation(chat_ids=target_chats, account_id=my_id)
        else:
            if not folder_name:
                remaining_tasks = db.count_tasks_for_template(target_name, account_id=my_id)
                if remaining_tasks == 0:
                    db.delete_template(target_name)
                    db.delete_named_rotation(target_name, account_id=my_id)

        folder_str = f" в папке \"{folder_name}\"" if folder_name else ""
        await _notify(event, f"🗑 Удалено рассылок: {count}{folder_str}")

    async def handle_view(event: Any, parts: List[str]):
        if len(parts) < 2:
            await _notify(event, "⚠️ Укажите название рассылки или `все`.")
            return

        target = parts[1].strip("<>").strip()
        if target.lower() in ("все", "all"):
            tasks = db.get_chat_broadcasts(event.chat_id, account_id=my_id)
            if not tasks:
                await _notify(event, "В этом чате нет активных рассылок.")
                return

            lines = ["**📋 Рассылки в этом чате:**\n"]
            for t in tasks:
                status = "🟢 Вкл" if t["is_active"] else "🔴 Выкл"
                if t["mode"] == "interval":
                    mode_str = f"каждые {_format_seconds(t['interval_seconds'])}"
                elif t["mode"] == "hybrid":
                    mode_str = f"через {t['counter_threshold']} соо (кд {_format_seconds(t['interval_seconds'])})"
                else:
                    mode_str = f"через {t['counter_threshold']} соо"
                folder_str = f" (папка: {t['folder_name']})" if t["folder_name"] else ""

                named_rot = db.get_named_rotation(t["template_name"], account_id=my_id)
                rot_info = f" [чередование: {' ➔ '.join(named_rot['template_names'])}]" if named_rot else ""
                lines.append(f"• **{t['template_name']}**{rot_info} — {status} | {mode_str}{folder_str}")

            rot = db.get_chat_rotation(event.chat_id, account_id=my_id)
            if rot and rot.get("template_names"):
                seq = " ➔ ".join(rot["template_names"])
                lines.append(f"\n🔄 **Очередь чередования этого чата:** {seq}")

            named_rots = db.list_named_rotations(account_id=my_id)
            if named_rots:
                lines.append("\n🔄 **Все сохраненные чередования:**")
                for nr in named_rots:
                    seq = " ➔ ".join(nr["template_names"])
                    lines.append(f"• **{nr['name']}**: {seq}")

            await _notify(event, "\n".join(lines), auto_delete=20)
        else:
            named_rot = db.get_named_rotation(target, account_id=my_id)
            if named_rot:
                tpls = named_rot["template_names"]
                seq = " ➔ ".join(tpls)
                next_tpl = db.get_next_rotation_template(account_id=my_id, rotation_name=target)
                await _notify(event, f"🔄 Чередование **{target}**: {seq}\n👁 Тестовый предпросмотр: **{next_tpl}** (отправляю ниже)", auto_delete=5)
                if next_tpl:
                    tpl_data = db.get_template(next_tpl)
                    if tpl_data:
                        await send_broadcast_post(
                            client,
                            event.chat_id,
                            text=tpl_data["text"],
                            entities_hex=tpl_data["entities_hex"],
                            media_files=tpl_data["media_files"],
                            bundle=tpl_data.get("bundle")
                        )
                return

            tpl = db.get_template(target)
            if not tpl:
                await _notify(event, f"⚠️ Рассылка с именем **{target}** не найдена.")
                return

            await _notify(event, f"👁 Тестовый предпросмотр: **{target}** (отправляю ниже)", auto_delete=3)
            await send_broadcast_post(
                client,
                event.chat_id,
                text=tpl["text"],
                entities_hex=tpl["entities_hex"],
                media_files=tpl["media_files"],
                bundle=tpl.get("bundle")
            )

async def authenticate_client(client: TelegramClient, phone_cfg: str, acc_name: str = "default"):
    await client.connect()
    if await client.is_user_authorized():
        return

    phone = phone_cfg.strip()
    if not phone:
        phone = input(f"[{acc_name}] Введите номер телефона (с кодом страны, напр. +79991234567): ").strip()

    print(f"[{acc_name}] Отправка кода подтверждения на номер {phone}...")
    await client.send_code_request(phone)
    code = input(f"[{acc_name}] Введите код из Telegram: ").strip()

    try:
        await client.sign_in(phone=phone, code=code)
    except SessionPasswordNeededError:
        print(f"[{acc_name}] На аккаунте включена двухфакторная аутентификация (2FA).")
        password = getpass.getpass(f"[{acc_name}] Введите облачный пароль (2FA): ")
        await client.sign_in(password=password)

async def run_single_account(acc_cfg: Dict[str, Any]):
    acc_name = acc_cfg.get("name", "default")
    api_id = acc_cfg.get("api_id")
    api_hash = acc_cfg.get("api_hash")

    if not api_id or not api_hash:
        log_error(f"[ERROR][{acc_name}] Не заполнены api_id и api_hash в config.txt")
        return

    session_name = acc_cfg.get("session_name") or f"session_{acc_name}"
    session_path = BASE_DIR / session_name
    client = TelegramClient(
        str(session_path),
        api_id,
        api_hash,
        catch_up=False,
        connection_retries=None,
        retry_delay=2,
        auto_reconnect=True,
        timeout=15,
    )

    while True:
        try:
            await authenticate_client(client, acc_cfg.get("phone", ""), acc_name=acc_name)
            break
        except (ConnectionError, OSError, asyncio.TimeoutError) as conn_err:
            log_error(f"[START][{acc_name}] Ошибка сети (нет интернета / VPN): {conn_err}. Повторное подключение через 5с...")
            await asyncio.sleep(5)

    me = await client.get_me()
    username_str = f"@{me.username}" if me.username else me.first_name

    broadcaster = BroadcasterService(client, account_id=me.id)
    broadcaster.start()

    register_events(client, broadcaster, me.id, acc_cfg=acc_cfg)

    print(f"✅ Аккаунт [{acc_name}] {username_str} (ID: {me.id}) подключен и запущен 24/7!")

    try:
        while True:
            try:
                if not client.is_connected():
                    await client.connect()
                await client.run_until_disconnected()
            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                log_error(f"[RECONNECT][{acc_name}] Потеря связи (VPN/сеть): {e}. Автоматический реконнект через 3с...")
                try:
                    await client.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                break
    finally:
        broadcaster.stop()

async def main():
    accounts = CONFIG.get("accounts", [])
    if not accounts:
        accounts = [{
            "name": "default",
            "api_id": CONFIG.get("api_id"),
            "api_hash": CONFIG.get("api_hash"),
            "phone": CONFIG.get("phone", ""),
            "session_name": CONFIG.get("session_name", "session_userbot"),
            "auto_sub_folder": CONFIG.get("auto_sub_folder", "автосабнутое"),
            "auto_read_spam_pings": CONFIG.get("auto_read_spam_pings", True),
            "auto_sub_enabled": CONFIG.get("auto_sub_enabled", True),
        }]

    valid_accounts = [a for a in accounts if a.get("api_id") and a.get("api_hash")]

    if not valid_accounts:
        print("=" * 60)
        print("[!] Не заполнены api_id и api_hash в config.txt")
        print("1. Откройте https://my.telegram.org")
        print("2. Войдите по своему номеру телефона")
        print("3. Перейдите в 'API development tools'")
        print("4. Создайте приложение (любое имя) и скопируйте api_id и api_hash в config.txt")
        print("=" * 60)

        raw_id = input("Или введите api_id прямо сейчас: ").strip()
        raw_hash = input("Введите api_hash прямо сейчас: ").strip()
        if raw_id.isdigit() and raw_hash:
            acc = {
                "name": "default",
                "api_id": int(raw_id),
                "api_hash": raw_hash,
                "phone": "",
                "session_name": "session_userbot",
                "auto_sub_folder": "автосабнутое",
                "auto_read_spam_pings": True,
                "auto_sub_enabled": True,
            }
            valid_accounts = [acc]
            CONFIG_PATH.write_text(
                f"api_id = {raw_id}\napi_hash = {raw_hash}\nphone = \nsession_name = session_userbot\nauto_sub_folder = автосабнутое\nauto_read_spam_pings = true\nauto_sub_enabled = true\nlog_errors_only = true\n",
                encoding="utf-8"
            )
        else:
            sys.exit(1)

    print("=" * 55)
    print("🚀 Запуск Spambuster Telegram Userbot (24/7 Background)")
    print(f"📡 Активных аккаунтов в конфигурации: {len(valid_accounts)}")
    print(f"🔇 Режим скрытия спама в логах: {'ВКЛ (только ошибки)' if CONFIG.get('log_errors_only', True) else 'ВЫКЛ (полные логи)'}")
    print("💬 Команды: напишите .инструкция или .help в любом чате")
    print("=" * 55)

    tasks = [run_single_account(acc) for acc in valid_accounts]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
