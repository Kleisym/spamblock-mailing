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

            # One row per running folder broadcast. Tasks are still materialised
            # per chat, but this registry is what lets the scheduler notice chats
            # added to a folder after the broadcast was created.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS folder_broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER DEFAULT 0,
                    folder_name TEXT,
                    template_name TEXT,
                    mode TEXT,
                    interval_seconds INTEGER DEFAULT 0,
                    counter_threshold INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    created_at REAL
                )
            """)
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_folder_broadcasts_unique ON folder_broadcasts (account_id, folder_name, template_name);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_folder_broadcasts_active ON folder_broadcasts (is_active, account_id);")

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

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS muted_peers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER DEFAULT 0,
                    peer_id INTEGER,
        peer_key INTEGER,
                    username TEXT,
                    display_name TEXT,
                    scope TEXT DEFAULT 'user',
                    reason TEXT DEFAULT '',
                    created_at REAL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_muted_peers_lookup ON muted_peers (account_id, peer_key);")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_muted_peers_unique ON muted_peers (account_id, peer_key);")

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
            cursor.execute(
                "DELETE FROM folder_broadcasts WHERE template_name = ? OR template_name = ?",
                (clean_name, f"<{clean_name}>")
            )
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
                WHERE chat_id = ? AND template_name = ? AND account_id = ?
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
                        current_count = 0,
                        last_sent_at = 0.0
                    WHERE id = ?
                """, (account_id, mode, interval_seconds, counter_threshold, folder_name, row["id"]))
            else:
                cursor.execute("""
                    INSERT INTO broadcast_tasks (account_id, chat_id, template_name, mode, interval_seconds, counter_threshold, is_active, folder_name, last_sent_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0.0)
                """, (account_id, chat_id, clean_name, mode, interval_seconds, counter_threshold, folder_name))

    def register_folder_broadcast(
        self,
        folder_name: str,
        template_name: str,
        mode: str,
        interval_seconds: int = 0,
        counter_threshold: int = 0,
        account_id: int = 0
    ):
        """Persist a folder-level broadcast so the scheduler can keep it in sync."""
        clean_folder = (folder_name or "").strip()
        clean_name = template_name.strip("<>").strip()
        if not clean_folder:
            return
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO folder_broadcasts
                    (account_id, folder_name, template_name, mode, interval_seconds, counter_threshold, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(account_id, folder_name, template_name) DO UPDATE SET
                    mode = excluded.mode,
                    interval_seconds = excluded.interval_seconds,
                    counter_threshold = excluded.counter_threshold,
                    is_active = 1
            """, (account_id, clean_folder, clean_name, mode, interval_seconds, counter_threshold, time.time()))

    def get_active_folder_broadcasts(self, account_id: int = 0) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            if account_id:
                rows = conn.execute(
                    "SELECT * FROM folder_broadcasts WHERE is_active = 1 AND (account_id = ? OR account_id = 0)",
                    (account_id,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM folder_broadcasts WHERE is_active = 1").fetchall()
            return [dict(r) for r in rows]

    def set_folder_broadcast_status(
        self,
        folder_name: Optional[str] = None,
        name: Optional[str] = None,
        is_active: int = 1,
        account_id: int = 0
    ) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            conditions = []
            params: List[Any] = [is_active]
            if account_id:
                conditions.append("(account_id = ? OR account_id = 0)")
                params.append(account_id)
            if folder_name:
                conditions.append("folder_name = ?")
                params.append(folder_name)
            if name and name.lower() not in ("все", "all"):
                clean_name = name.strip("<>").strip()
                conditions.append("(template_name = ? OR template_name = ?)")
                params.extend([clean_name, f"<{clean_name}>"])
            where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            cursor.execute(f"UPDATE folder_broadcasts SET is_active = ?{where_clause}", params)
            return cursor.rowcount

    def delete_folder_broadcasts(
        self,
        folder_name: Optional[str] = None,
        name: Optional[str] = None,
        account_id: int = 0
    ) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            conditions = []
            params: List[Any] = []
            if account_id:
                conditions.append("(account_id = ? OR account_id = 0)")
                params.append(account_id)
            if folder_name:
                conditions.append("folder_name = ?")
                params.append(folder_name)
            if name and name.lower() not in ("все", "all"):
                clean_name = name.strip("<>").strip()
                conditions.append("(template_name = ? OR template_name = ?)")
                params.extend([clean_name, f"<{clean_name}>"])
            where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            cursor.execute(f"DELETE FROM folder_broadcasts{where_clause}", params)
            return cursor.rowcount

    def get_chats_in_broadcast(self, chat_id: int, template_name: str, account_id: int = 0) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            clean_name = template_name.strip("<>").strip()
            row = conn.execute(
                "SELECT * FROM broadcast_tasks WHERE chat_id = ? AND template_name = ? AND (account_id = ? OR account_id = 0)",
                (chat_id, clean_name, account_id)
            ).fetchone()
            return dict(row) if row else None

    def ensure_broadcast_task(
        self,
        chat_id: int,
        template_name: str,
        mode: str,
        interval_seconds: int = 0,
        counter_threshold: int = 0,
        folder_name: str = "",
        account_id: int = 0
    ) -> bool:
        """
        Idempotent enrolment used by the folder reconciler.

        Returns True when a new task row was created. Crucially it never touches
        last_sent_at on an existing row: add_or_update_broadcast() zeroes that
        column, so re-registering every chat on every sync pass would reset every
        interval timer and fire the whole broadcast on each tick.
        """
        clean_name = template_name.strip("<>").strip()
        with self._conn() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT id, is_active, mode, interval_seconds, counter_threshold, account_id "
                "FROM broadcast_tasks WHERE chat_id = ? AND template_name = ? AND account_id = ?",
                (chat_id, clean_name, account_id)
            ).fetchone()
            if row is None:
                cursor.execute(
                    """
                    INSERT INTO broadcast_tasks
                        (account_id, chat_id, template_name, mode, interval_seconds,
                         counter_threshold, is_active, folder_name, last_sent_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0.0)
                    """,
                    (account_id, chat_id, clean_name, mode, interval_seconds,
                     counter_threshold, folder_name)
                )
                return True

            # Re-activate when it was stopped or parked, and adopt config drift,
            # but leave the send timer alone.
            needs_update = (
                not row["is_active"]
                or row["mode"] != mode
                or (row["interval_seconds"] or 0) != (interval_seconds or 0)
                or (row["counter_threshold"] or 0) != (counter_threshold or 0)
            )
            if needs_update:
                cursor.execute(
                    """
                    UPDATE broadcast_tasks
                    SET account_id = ?, mode = ?, interval_seconds = ?,
                        counter_threshold = ?, is_active = 1, folder_name = ?
                    WHERE id = ?
                    """,
                    (account_id, mode, interval_seconds, counter_threshold,
                     folder_name, row["id"])
                )
            return False

    def get_enrolled_chat_ids(self, template_name: str, folder_name: str, account_id: int = 0) -> Set[int]:
        """Chats currently carrying an active task for this folder broadcast."""
        with self._conn() as conn:
            clean_name = template_name.strip("<>").strip()
            if account_id:
                rows = conn.execute(
                    "SELECT chat_id FROM broadcast_tasks "
                    "WHERE template_name = ? AND folder_name = ? AND is_active = 1 "
                    "AND (account_id = ? OR account_id = 0)",
                    (clean_name, folder_name, account_id)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT chat_id FROM broadcast_tasks "
                    "WHERE template_name = ? AND folder_name = ? AND is_active = 1",
                    (clean_name, folder_name)
                ).fetchall()
            return {r["chat_id"] for r in rows}

    def deactivate_broadcast_task(self, chat_id: int, template_name: str, account_id: int = 0) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            clean_name = template_name.strip("<>").strip()
            cursor.execute(
                "UPDATE broadcast_tasks SET is_active = 0 "
                "WHERE chat_id = ? AND template_name = ? AND (account_id = ? OR account_id = 0)",
                (chat_id, clean_name, account_id)
            )
            return cursor.rowcount
    def remove_template_from_folder_broadcasts(self, template_name: str):
        clean_name = template_name.strip("<>").strip()
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM folder_broadcasts WHERE template_name = ? OR template_name = ?",
                (clean_name, f"<{clean_name}>")
            )

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

    # ---- atomic claims: only one caller can win a due task -----------------
    def claim_interval_task(self, task_id: int, now: float) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE broadcast_tasks SET last_sent_at = ? "
                "WHERE id = ? AND is_active = 1 AND (? - COALESCE(last_sent_at, 0)) >= interval_seconds",
                (now, task_id, now),
            )
            return cur.rowcount == 1

    def claim_hybrid_task(self, task_id: int, now: float) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE broadcast_tasks SET last_sent_at = ?, current_count = 0 "
                "WHERE id = ? AND is_active = 1 AND current_count >= counter_threshold "
                "AND (? - COALESCE(last_sent_at, 0)) >= interval_seconds",
                (now, task_id, now),
            )
            return cur.rowcount == 1

    def schedule_retry(self, task: Dict[str, Any], delay: float):
        """Make a claimed-but-unsent task due again after `delay` seconds."""
        mode = task.get("mode") or "interval"
        interval = task.get("interval_seconds") or 0
        threshold = task.get("counter_threshold") or 0
        with self._conn() as conn:
            if mode in ("interval", "hybrid"):
                conn.execute(
                    "UPDATE broadcast_tasks SET last_sent_at = ? WHERE id = ?",
                    (time.time() - interval + max(delay, 1.0), task["id"]),
                )
            if mode in ("counter", "hybrid"):
                conn.execute(
                    "UPDATE broadcast_tasks SET current_count = MAX(current_count, ?) WHERE id = ?",
                    (threshold, task["id"]),
                )

    def deactivate_task(self, task_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE broadcast_tasks SET is_active = 0 WHERE id = ?", (task_id,))

    def adopt_legacy_rows(self, account_id: int):
        """
        Rows saved with account_id = 0 (old versions) were picked up by EVERY
        running account, so each of them sent the same post. The first account
        that starts now takes ownership of them.
        """
        if not account_id:
            return
        with self._conn() as conn:
            for table in ("broadcast_tasks", "folder_broadcasts", "chat_rotations", "muted_peers"):
                try:
                    conn.execute(f"UPDATE OR IGNORE {table} SET account_id = ? WHERE account_id = 0 OR account_id IS NULL", (account_id,))
                    conn.execute(f"DELETE FROM {table} WHERE account_id = 0 OR account_id IS NULL")
                except Exception:
                    pass

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

    def add_muted_peer(
        self,
        peer_id: int,
        account_id: int = 0,
        peer_key: Optional[int] = None,
        username: str = "",
        display_name: str = "",
        scope: str = "user",
        reason: str = ""
    ) -> bool:
        """Add a peer to the mute list. Returns True when newly added."""
        if peer_key is None:
            peer_key = peer_id
        clean_username = (username or "").lstrip("@").strip().lower()
        clean_name = (display_name or "").strip()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO muted_peers
                    (account_id, peer_id, peer_key, username, display_name, scope, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (account_id, peer_id, peer_key, clean_username, clean_name, scope, reason, time.time())
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "UPDATE muted_peers SET username = COALESCE(NULLIF(?, ''), username), "
                    "display_name = COALESCE(NULLIF(?, ''), display_name), scope = ? WHERE account_id = ? AND peer_key = ?",
                    (clean_username, clean_name, scope, account_id, peer_key)
                )
                return False
            return True

    def remove_muted_peer(self, peer_id: int, account_id: int = 0, scope: str = "user") -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            if scope == "chat":
                cursor.execute(
                    "DELETE FROM muted_peers WHERE peer_key = ? AND scope = 'chat' AND (account_id = ? OR account_id = 0)",
                    (peer_id, account_id)
                )
            else:
                cursor.execute(
                    "DELETE FROM muted_peers WHERE peer_key = ? AND scope != 'chat' AND (account_id = ? OR account_id = 0)",
                    (peer_id, account_id)
                )
            return cursor.rowcount

    def list_muted_peers(self, account_id: int = 0) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            if account_id:
                rows = conn.execute(
                    "SELECT * FROM muted_peers WHERE account_id = ? OR account_id = 0 ORDER BY created_at DESC",
                    (account_id,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM muted_peers ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def find_muted_peer_by_username(self, username: str, account_id: int = 0) -> Optional[Dict[str, Any]]:
        clean = (username or "").lstrip("@").strip().lower()
        if not clean:
            return None
        with self._conn() as conn:
            if account_id:
                row = conn.execute(
                    "SELECT * FROM muted_peers WHERE username = ? AND (account_id = ? OR account_id = 0) LIMIT 1",
                    (clean, account_id)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM muted_peers WHERE username = ? LIMIT 1", (clean,)
                ).fetchone()
        return dict(row) if row else None

try:
    db = Database()
except Exception:
    db = None
ACTIVE_COLLECTORS: Dict[Any, Dict[str, Any]] = {}

# Per-process index of muted peers so the hot incoming-message path never
# touches sqlite. Rebuilt from the database on demand and after every change.
_MUTE_CACHE: Dict[int, Set[int]] = {}
_MUTE_CHAT_CACHE: Dict[int, Set[int]] = {}

def _strip_peer_id(value: int) -> int:
    """Telegram user ids arrive as 1000000000000+id; store and match the plain id."""
    try:
        v = int(value)
    except Exception:
        return value
    if v > 1000000000000:
        return v - 1000000000000
    return v

def mute_cache_rebuild(account_id: int = 0):
    """Repopulate the in-memory mute index for an account."""
    if db is None:
        return
    try:
        with db._conn() as conn:
            if account_id:
                rows = conn.execute(
                    "SELECT peer_key, scope FROM muted_peers WHERE account_id = ? OR account_id = 0",
                    (account_id,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT peer_key, scope FROM muted_peers").fetchall()
    except Exception:
        return

    users = _MUTE_CACHE.setdefault(account_id, set())
    chats = _MUTE_CHAT_CACHE.setdefault(account_id, set())
    users.clear()
    chats.clear()
    for r in rows:
        try:
            key = int(r["peer_key"])
        except Exception:
            continue
        if (r["scope"] or "user") == "chat":
            chats.add(key)
        else:
            users.add(key)

def is_peer_muted(account_id: int, peer_id: Any, chat_id: Optional[int] = None) -> bool:
    """True when the sender (or the chat itself) is muted for this account."""
    if peer_id is None:
        return False
    try:
        if _strip_peer_id(peer_id) in _MUTE_CACHE.get(account_id, set()):
            return True
        if chat_id is not None and int(chat_id) in _MUTE_CHAT_CACHE.get(account_id, set()):
            return True
    except Exception:
        return False
    return False

# =============================================================================
# SPAM / ADVERTISING DETECTION
#
# Three layers, evaluated in this order:
#   1. Hard structural evidence (score 100): inline-bot posts, invisible
#      characters, hidden or mass mentions, autoposter watermarks.
#   2. Weighted lexicon + layout scoring. Spam topics (stars, TON, NFT,
#      accounts, "earnings", casino...), sale verbs, contact routing, links and
#      catalogue layout each add points.
#   3. Conversational dampeners. They are capped and never cancel strong
#      commercial evidence, and the "buyer question" veto only applies to
#      messages that carry no sale lexicon, routing or links.
#
# Classification threshold: score >= SPAM_THRESHOLD.
# =============================================================================

SPAM_THRESHOLD = 50

# Invisible characters used for ghost pings. ZWJ (\u200d) and variation
# selectors are deliberately NOT here: they are part of normal emoji.
_INVISIBLE_RE = re.compile(r"[\u200b\u200e\u200f\u2060-\u2064\u2066-\u206f\ufeff\u180e\u3164\u115f\u1160]")

SPAM_SIGNATURES = [
    # Autoposter / userbot watermarks
    r"(?:отправлено|рассылаю|рассылка|рассылается|опубликовано|запощено|размещено|постинг|автопост\w*)\s+(?:с\s+помощью|через|в|с)\s+@?[a-z0-9_]{3,32}\b",
    r"\b(?:sent|posted|broadcasted|powered)\s+(?:via|by|with|through)\s+@?[a-z0-9_]{3,32}\b",
    # Mailing software / services
    r"(?:скрипт|софт|программ\w*|бот)\s+(?:для\s+)?(?:рассыл|спам|автопост|инвайт)\w*\s*[:\-—]?\s*@?\w+",
    r"@[a-z0-9_]*(?:autopost|sender|spambot|postbot|mailer|repost|trafficbot|mailing)[a-z0-9_]*",
    r"заказать\s+(?:рассылку|спам|инвайтинг)",
]
SPAM_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SPAM_SIGNATURES]

COUNTRY_FLAGS = ["🇺🇸", "🇮🇳", "🇮🇩", "🇨🇴", "🇲🇲", "🇧🇩", "🇧🇷", "🇨🇦", "🇲🇽", "🇹🇷", "🇨🇱", "🇿🇦", "🇷🇺",
                 "🇺🇦", "🇰🇿", "🇧🇾", "🇺🇿", "🇬🇧", "🇩🇪", "🇵🇭", "🇻🇳", "🇳🇬", "🇵🇰", "🇪🇬"]

# (pattern, points, label). Topics that are almost only ever spam in PR chats.
_SPAM_LEXICON = [
    (r"зв[её]зд\w*|\bstars?\b", 20, "stars"),
    (r"\bтон\w{0,2}\b|\bton\b|\busdt\b|\bbtc\b|крипт\w*|\bкрипта\b|\bp2p\b|обменник\w*", 20, "crypto"),
    (r"\bнфт\b|\bnft\b|подар(?:ок|ки|ков)\b|флор\w*", 20, "nft_gifts"),
    (r"\bголд\w*|\bgold\b|\bюс\b|\bробукс\w*|\bгемы\b|\bдонат\w*", 15, "game_currency"),
    (r"аккаунт\w*|\bакк[иа]?\b|\bакков\b|\bсессии\b|\btdata\b|номер(?:а|ов)\s+(?:стран|рф|сша)|смен\w*\s+номер\w*", 20, "accounts"),
    (r"заработ\w*|зарабат\w*|работа\s+с\s+телефона|\bдоход\w*|пассивн\w*|без\s+вложени\w*|\bв\s+день\b|\bв\s+сутки\b|\$\s*в\s+(?:день|неделю)|удал[её]нн?\w*\s+работ\w*|\bподработк\w*|набор\s+в\s+команду|ищем\s+людей|\bсхем[ауы]\b|\bарбитраж\w*", 35, "earnings"),
    (r"казино|\bcasino\b|гемблинг|\bazart\b|\b1win\b|\bstake\b|ставк[иа]\b|фриспин\w*|\bбонус\w*\s+(?:за|при)\s+регистрац\w*|промокод\w*", 35, "gambling"),
    (r"\bинтим\w*|\b18\s*\+|\bэро\b|\bслив\w*|\bприват\w*\s+канал", 35, "adult"),
    (r"\bклик(?:и|ов)\b|\bпросмотр(?:ы|ов)\b\s+(?:на|для)|\bнакрутк\w*|\bподписчик(?:и|ов)\b\s+(?:на|в|для)|\bреакци(?:и|й)\b\s+на", 20, "smm"),
    (r"\bвербовк\w*|\bдроп\w*\b|\bобнал\w*|\bкарты?\s+(?:физ|юр)", 30, "fraud"),
]
_SPAM_LEXICON = [(re.compile(p, re.IGNORECASE), pts, label) for p, pts, label in _SPAM_LEXICON]
_STRONG_TOPICS = {"earnings", "gambling", "adult", "fraud"}
_PROMO_RE = re.compile(
    r"\b(?:бонус\w*|промокод\w*|акци[яи]|скидк\w*|бесплатн\w*|халяв\w*|розыгрыш\w*|раздач\w*|выигр\w*|вывод\w*|оптом|опт\b)",
    re.IGNORECASE,
)

_SALE_VERB_RE = re.compile(
    r"\b(?:продам|продаю|прода[её]тся|прода[её]м|продажа|скупаю|скупка|скупаем|куплю|покупаю|обменяю|обмен|"
    r"сдам|сдаю|предлагаю|предлагаем|оказываю|оказываем|отдам|раздаю|раздача|ищу\s+менеджер\w*|"
    r"менеджер\w*\s+по\s+продажам|дешевле|дешево|дёшево|выгодно|недорого|по\s+курсу|ниже\s+рынка|"
    r"купить\s+можно|можно\s+купить\s+за|в\s+наличии|цена\s+за|прайс\s*:|оплата\s+(?:в|через|картой|криптой|зв[её]здами))\b",
    re.IGNORECASE,
)
_SELLER_ASK_RE = re.compile(
    r"\b(?:купишь|купите|покупайте|покупай|закажи|заказывай|нужн[ыао]\s+(?:зв[её]зд|акк|подписчик|просмотр|клик|реклам))",
    re.IGNORECASE,
)
_ROUTING_RE = re.compile(
    r"(?:пиш(?:и|ите)|писать|связь|обращаться|отпиш(?:и|ите)|стучи(?:те)?|жду|вопросы|заказ|менеджер\w*|"
    r"по\s+поводу\s+\w+|для\s+заказа|купить)\s*(?:в\s+лс|в\s+личку|в\s+пм|сюда|тут)?\s*[:\-—–]?\s*@\w+",
    re.IGNORECASE,
)
_EMOJI_ROUTING_RE = re.compile(r"(?:📩|👉|📲|💬|✍️|✍|✉️|☎️|📞|➡️|⬇️)\s*(?:в\s+лс|пишите|связь|обращаться)?\s*@\w+", re.IGNORECASE)
_DM_CTA_RE = re.compile(
    r"(?:\bв\s+лс\b|\bв\s+личку\b|\bв\s+пм\b|\bлс\s+открыт\w*|подробн\w*\s+(?:в|по)\s+(?:лс|профил\w*|био|ссылк\w*|канал\w*)|"
    r"ссылка\s+в\s+(?:профиле|био|описании)|жми\s+на|переходи\w*|\bтык\b)",
    re.IGNORECASE,
)
_CATALOG_BULLET_RE = re.compile(r"^(?:[·•▪️▫️◾◽\-\—\*✅✔️☑️🔹🔸🔵🟢❗️❕]|\d+[\.\)\-\—]|[1-9]️⃣|[①-⑩])\s*")
_BANNER_RE = re.compile(r"^(?:🔥|⚡️|⚡|📢|📣|💎|💰|💸|💵|🚀|🚨|❗️|‼️|⚠️|⭐️|⭐|🌟|💥|🎯|👉|📩|📲|💬|🎁|🤑|📈|🆕|🔝)")
_PRICE_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:₽|руб\w*|р\b|\$|usd\w*|€|грн|тг|⭐|🌟|зв[её]зд\w*|ton\b|тон\b)", re.IGNORECASE)
_LINK_RE = re.compile(r"t\.me/(?:\+|joinchat/|[a-z0-9_]{5,})|telegram\.me/|https?://\S+", re.IGNORECASE)
_PRIVATE_LINK_RE = re.compile(r"t\.me/(?:\+|joinchat/)", re.IGNORECASE)
_CONVO_RE = re.compile(
    r"\b(?:я|мне|меня|мы|нас|ты|тебе|тебя|твой|твоем|твоём|привет|ку|здравствуйте|спасибо|спс|лол|хах\w*|хд|"
    r"ладно|норм|почему|зачем|окей|ок|понял\w*|сорри|извини\w*|ага|угу|да|нет)\b",
    re.IGNORECASE,
)

BUYER_QUESTION_TRIGGERS = [
    r"\bпоч[её]м\b", r"\bцена\b", r"\bцену\b", r"\bпрайс\b", r"\bактуально\b",
    r"\bсвободн\w*\b", r"\bстат[уа]\b", r"\bстатистик[ау]\b", r"\bохват\w*\b",
    r"\bчекни\s+лс\b", r"\bответь(?:те)?\s+в\s+лс\b", r"\bкуплю\s+рекламу\b", r"\bхочу\s+купить\b",
    r"\bпродашь\b", r"\bпрода[её]шь\b", r"\bпрода[её]те\b", r"\bсколько\s+стоит\b",
    r"\bможно\s+(?:купить|взять|разместить|заказать)\b", r"\bместо\s+есть\b", r"\bесть\s+место\b",
    r"\bзакреп\w*\b", r"\bслот\w*\b",
]
BUYER_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BUYER_QUESTION_TRIGGERS]

GATEKEEPER_TRIGGERS = [
    r"чтобы\s+(?:писать|отправлять\s+сообщения)\s+в\s+(?:чат|группу)",
    r"необходимо\s+подписаться",
    r"нужно\s+подписаться",
    r"подпишитесь\s+на\s+(?:канал|наш)",
    r"обязательная\s+подписка",
    r"для\s+доступа\s+к\s+чату",
    r"пройдите\s+капчу",
    r"подтвердите,?\s*что\s+вы\s+не\s+(?:бот|робот)",
    r"you\s+must\s+(?:join|subscribe)",
]
GATEKEEPER_PATTERNS = [re.compile(p, re.IGNORECASE) for p in GATEKEEPER_TRIGGERS]

VERIFY_BUTTON_TEXTS = [
    "подписался", "я подписался", "проверить", "готово", "подтвердить",
    "вступил", "продолжить", "я не бот", "check", "done", "verify", "i subscribed", "i'm not a bot",
]


def _utf16_slice(text: str, offset: int, length: int) -> str:
    try:
        encoded = text.encode("utf-16-le")
        return encoded[offset * 2 : (offset + length) * 2].decode("utf-16-le", errors="ignore")
    except Exception:
        return text[offset : offset + length]


def _entity_evidence(text: str, message: Any) -> Optional[Tuple[str, str]]:
    """Hidden or mass mentions carried by message entities."""
    entities = getattr(message, "entities", None) if message is not None else None
    if not entities:
        return None
    from telethon.tl.types import (
        MessageEntityMentionName,
        InputMessageEntityMentionName,
        MessageEntityMention,
        MessageEntityTextUrl,
    )
    user_mentions = 0
    total_mentions = 0
    hidden = False
    for ent in entities:
        is_user_ref = isinstance(ent, (MessageEntityMentionName, InputMessageEntityMentionName)) or (
            isinstance(ent, MessageEntityTextUrl) and "tg://user?id=" in (ent.url or "").lower()
        )
        if is_user_ref:
            user_mentions += 1
            total_mentions += 1
            ent_text = _utf16_slice(text, ent.offset, ent.length)
            if not re.search(r"\w", ent_text):
                hidden = True
        elif isinstance(ent, MessageEntityMention):
            total_mentions += 1
    if hidden:
        return "hidden_ghost_mention", "mention_under_emoji_or_space"
    if user_mentions >= 2 or total_mentions >= 4:
        return "mass_entity_tagging", f"mentions_{total_mentions}"
    return None


def _hard_evidence(text: str, message: Any = None) -> Optional[Tuple[str, str]]:
    if message is not None and getattr(message, "via_bot_id", None):
        return "sent_via_inline_bot", "inline_bot"
    if _INVISIBLE_RE.search(text):
        return "invisible_ghost_ping", "zero_width_ghost_chars"
    ev = _entity_evidence(text, message)
    if ev:
        return ev
    clean = text.lower()
    for pat in SPAM_PATTERNS:
        if pat.search(clean):
            return pat.pattern, "signature_match"
    return None


def calculate_spam_score(text: str, message: Any = None) -> Tuple[int, str, List[str]]:
    """Returns (score, primary_trigger, matched_factors). Spam when score >= 50."""
    if not text:
        if message is not None and getattr(message, "via_bot_id", None):
            return 100, "sent_via_inline_bot", ["inline_bot"]
        return 0, "", []

    hard = _hard_evidence(text, message)
    if hard:
        return 100, hard[0], [hard[1]]

    clean = text.lower()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    factors: List[str] = []
    score = 0

    def add(points: int, label: str):
        nonlocal score
        score += points
        factors.append(f"{label}(+{points})")

    # --- commercial lexicon -------------------------------------------------
    topic_points = 0
    strong_topic = False
    multi_hit_topic = False
    for pat, pts, label in _SPAM_LEXICON:
        hits = {m.group(0) for m in pat.finditer(clean)}
        if not hits:
            continue
        if label in _STRONG_TOPICS:
            strong_topic = True
        extra = min(10 * (len(hits) - 1), 20)
        if extra:
            multi_hit_topic = True
        topic_points += pts + extra
        factors.append(f"topic_{label}(+{pts + extra})")
    topic_points = min(topic_points, 50)
    score += topic_points

    has_sale_verb = bool(_SALE_VERB_RE.search(clean))
    has_seller_ask = bool(_SELLER_ASK_RE.search(clean))
    if has_sale_verb:
        add(25, "sale_verb")
    if has_seller_ask:
        add(55, "seller_ask")

    # --- contact routing ----------------------------------------------------
    has_emoji_routing = bool(_EMOJI_ROUTING_RE.search(clean))
    has_text_routing = bool(_ROUTING_RE.search(clean))
    has_dm_cta = bool(_DM_CTA_RE.search(clean))
    if has_emoji_routing:
        add(30, "emoji_contact_routing")
    elif has_text_routing:
        add(25, "text_seller_routing")
    if has_dm_cta and (topic_points or has_sale_verb or has_seller_ask):
        add(15, "dm_call_to_action")

    links = _LINK_RE.findall(clean)
    if links:
        add(25 if _PRIVATE_LINK_RE.search(clean) else 10, "promo_links")
    handles = re.findall(r"@([a-zA-Z0-9_]{4,32})", text)
    if len(handles) >= 2:
        add(15, f"multiple_usernames_{len(handles)}")

    prices = _PRICE_RE.findall(clean)
    if len(prices) >= 2:
        add(20, f"price_list_{len(prices)}")
    elif prices and (topic_points or has_sale_verb):
        add(10, "price")

    flag_count = sum(1 for f in COUNTRY_FLAGS if f in text)
    if flag_count >= 3:
        add(35, f"country_flags_{flag_count}")

    # --- layout -------------------------------------------------------------
    banner = sum(1 for l in lines if _BANNER_RE.match(l))
    if banner:
        add(15 if banner == 1 else 25, "banner_emojis")
    bullets = sum(1 for l in lines if _CATALOG_BULLET_RE.match(l))
    if bullets:
        add(10 if bullets == 1 else 25, "bullet_points")
    if len(lines) >= 4:
        add(15, "multiblock_layout")
    if re.search(r"\b(?:канал[ыа]?|чат[ыа]?|групп[уыа]|сетк[уи]|баз[уы])\b", clean) and has_sale_verb:
        add(15, "asset_specifiers")

    commercial = bool(topic_points or has_sale_verb or has_seller_ask or has_emoji_routing or has_text_routing)
    has_promo = bool(_PROMO_RE.search(clean))
    combo = bool(topic_points) and (
        has_sale_verb or has_seller_ask or has_emoji_routing or has_text_routing or has_dm_cta
        or bool(links) or bool(prices) or has_promo or (strong_topic and multi_hit_topic)
    )
    if combo:
        add(20, "commercial_combo")

    # --- conversational dampeners (capped, never cancel commerce) ----------
    damp = 0
    if "?" in clean and not (has_emoji_routing or has_text_routing):
        damp += 25
        factors.append("question_mark(-25)")
    if len(clean) < 70 and len(lines) == 1:
        damp += 25
        factors.append("short_single_line(-25)")
    if _CONVO_RE.search(clean) and not (has_emoji_routing or bullets):
        damp += 15
        factors.append("conversational_markers(-15)")
    if has_seller_ask or combo:
        cap = 0
    elif strong_topic:
        cap = 10
    elif commercial and score >= 70:
        cap = 20
    else:
        cap = 40
    score = max(0, score - min(damp, cap))

    if is_genuine_buyer_question(text):
        score = 0
        factors.append("genuine_buyer_question(veto)")

    return score, (factors[0] if factors else "low_score"), factors


def is_spam_message(text: str, message: Any = None) -> Tuple[bool, str]:
    score, trigger, _ = calculate_spam_score(text or "", message)
    if score >= SPAM_THRESHOLD:
        return True, f"{trigger} (score={score})"
    return False, ""


def is_genuine_buyer_question(text: str) -> bool:
    """A short question from someone who wants to BUY from us. Never spam."""
    if not text:
        return False
    clean = text.lower().strip()
    if len(clean) >= 220 or _INVISIBLE_RE.search(text):
        return False
    if _ROUTING_RE.search(clean) or _EMOJI_ROUTING_RE.search(clean):
        return False
    if _LINK_RE.search(clean) or re.search(r"[1-9]️⃣|[①-⑩]", clean):
        return False
    if sum(1 for f in COUNTRY_FLAGS if f in text) >= 2:
        return False
    if _SELLER_ASK_RE.search(clean):
        return False
    if re.search(r"\b(?:продам|продаю|скупаю|скупка|продажа|куплю\s+(?:зв|акк|тон|нфт|голд))", clean):
        return False
    if sum(1 for pat, _p, _l in _SPAM_LEXICON if pat.search(clean)) >= 2:
        return False
    has_q = "?" in clean
    has_trigger = any(p.search(clean) for p in BUYER_PATTERNS)
    if has_trigger and (has_q or len(clean) < 120):
        return True
    # A bare short question about ads/our channel ("а сколько?", "есть место?")
    if has_q and len(clean) < 120 and not any(pat.search(clean) for pat, _p, _l in _SPAM_LEXICON):
        return bool(re.search(r"\b(?:реклам\w*|пост\w*|канал\w*|чат\w*|размещ\w*|закреп\w*|стоит|цен\w*)\b", clean))
    return False


def is_hard_spam(text: str, message: Any = None) -> Tuple[bool, str]:
    """Structural evidence a human never produces by accident."""
    if not text and not (message is not None and getattr(message, "via_bot_id", None)):
        return False, ""
    hard = _hard_evidence(text or "", message)
    return (True, hard[0]) if hard else (False, "")


def should_auto_read(text: str, message: Any = None, is_ping: Optional[bool] = None) -> bool:
    """
    Auto-clear gate.

    Hard structural spam is always cleared. Heuristic advertising is cleared
    only when it pinged us (is_ping=True) or no ping context was given. A real
    buyer question is never cleared.
    """
    hard, _ = is_hard_spam(text, message)
    if hard:
        return True
    if is_genuine_buyer_question(text):
        return False
    if is_ping is False:
        return False
    return is_spam_message(text, message)[0]

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

async def fetch_album(client: TelegramClient, message: Any) -> List[Any]:
    """All parts of the album `message` belongs to, sorted, without duplicates."""
    gid = getattr(message, "grouped_id", None)
    if not gid:
        return [message]
    around = list(range(max(1, message.id - 10), message.id + 11))
    try:
        found = await client.get_messages(message.chat_id, ids=around)
    except Exception:
        found = []
    album = {m.id: m for m in (found or []) if m is not None and getattr(m, "grouped_id", None) == gid}
    album.setdefault(message.id, message)
    return [album[k] for k in sorted(album)][:10]


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
        album_messages = await fetch_album(client, message)
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
        sent_items = 0
        for idx, item in enumerate(clean_bundle):
            if idx > 0:
                await asyncio.sleep(1.0)
            try:
                await _send_bundle_item(client, chat_peer, item)
                sent_items += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if sent_items:
                    raise PartialSendError(sent_items, e) from e
                raise
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


async def _send_bundle_item(client: TelegramClient, chat_peer: Any, item: Dict[str, Any]):
    if item.get("is_forward") and item.get("forward_chat_id") and item.get("forward_msg_ids"):
        try:
            await client.forward_messages(
                chat_peer,
                messages=item["forward_msg_ids"],
                from_peer=item["forward_chat_id"]
            )
            return
        except (errors.SlowModeWaitError, errors.FloodWaitError, errors.UserBannedInChannelError,
                errors.ChannelPrivateError, errors.ChatWriteForbiddenError, asyncio.CancelledError,
                ConnectionError, OSError, asyncio.TimeoutError):
            raise
        except Exception as e:
            log_error(f"[WARN] Не удалось переслать сообщение: {e}. Отправляю сохранённую копию.")

    await send_single_item(
        client,
        chat_peer,
        text=item.get("text", ""),
        entities_hex=item.get("entities_hex", []),
        media_files=item.get("media_files", [])
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
        # Duck-typed on purpose: Telegram has shipped several folder filter
        # shapes (DialogFilter, DialogFilterChatlist and their successors), and
        # an isinstance whitelist silently turned every unrecognised one into
        # "folder not found". Matching on the title alone is what actually
        # decides whether this filter is the folder we asked for.
        if hasattr(f, "title") or hasattr(f, "id"):
            title = _extract_folder_title(f).lower()
            if title == target_clean:
                target_filter = f
                break

    if not target_filter:
        return []

    chat_ids = set()
    for peer in list(getattr(target_filter, "include_peers", []) or []) + list(getattr(target_filter, "pinned_peers", []) or []):
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


# Highest message id of a *genuine* ping (real reply / mention from a person)
# per (account, chat). Used so that clearing a spam ping never marks a real,
# still-unread reply as read.
_GENUINE_PINGS: Dict[Tuple[int, int], int] = {}


def note_genuine_ping(account_id: int, chat_id: int, msg_id: int):
    key = (account_id, chat_id)
    if msg_id and msg_id > _GENUINE_PINGS.get(key, 0):
        _GENUINE_PINGS[key] = msg_id
    if len(_GENUINE_PINGS) > 5000:
        for k in list(_GENUINE_PINGS)[:1000]:
            _GENUINE_PINGS.pop(k, None)


async def _resolve_input_peer(client: TelegramClient, chat_peer: Any, message: Any):
    from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser, InputPeerSelf
    if isinstance(chat_peer, (InputPeerChannel, InputPeerChat, InputPeerUser, InputPeerSelf)):
        return chat_peer
    if message is not None and hasattr(message, "get_input_chat"):
        try:
            peer = await message.get_input_chat()
            if peer:
                return peer
        except Exception:
            pass
    if getattr(message, "input_chat", None):
        return message.input_chat
    try:
        return await client.get_input_entity(chat_peer)
    except Exception:
        return None


async def clear_spam_mention(
    client: TelegramClient,
    chat_peer: Any,
    message: Any,
    account_id: int = 0,
    chat_id: Optional[int] = None,
):
    """
    Silence one spam ping that reached us.

    1. Mark *only this message's* mention as read (readMessageContents). The
       old code called ReadMentions for the whole chat, which also wiped real
       mentions from people, and ReadReactions, which wiped real reactions.
    2. Read the chat history up to the spam message so the push notification
       disappears, but only when no genuine ping in that chat is still unread.
    """
    from telethon.tl.types import InputPeerChannel
    from telethon.tl.functions.messages import (
        ReadHistoryRequest as MessagesReadHistoryRequest,
        ReadMessageContentsRequest as MessagesReadContentsRequest,
        GetPeerDialogsRequest,
    )
    from telethon.tl.functions.channels import (
        ReadHistoryRequest as ChannelReadHistoryRequest,
        ReadMessageContentsRequest as ChannelReadContentsRequest,
    )
    from telethon.tl.types import InputDialogPeer

    msg_id = getattr(message, "id", 0) or 0
    if not msg_id:
        return
    input_peer = await _resolve_input_peer(client, chat_peer, message)
    if input_peer is None:
        return
    is_channel = isinstance(input_peer, InputPeerChannel)

    try:
        if is_channel:
            await client(ChannelReadContentsRequest(channel=utils.get_input_channel(input_peer), id=[msg_id]))
        else:
            await client(MessagesReadContentsRequest(id=[msg_id]))
    except Exception:
        pass

    # Is a real ping in this chat still unread? Then leave the history alone.
    key_chat = chat_id if chat_id is not None else getattr(message, "chat_id", None)
    pending_real = _GENUINE_PINGS.get((account_id, key_chat), 0) if key_chat is not None else 0
    if pending_real and pending_real < msg_id:
        try:
            res = await client(GetPeerDialogsRequest(peers=[InputDialogPeer(peer=input_peer)]))
            read_max = res.dialogs[0].read_inbox_max_id if getattr(res, "dialogs", None) else 0
        except Exception:
            read_max = 0
        if pending_real > read_max:
            return
        _GENUINE_PINGS.pop((account_id, key_chat), None)

    try:
        if is_channel:
            await client(ChannelReadHistoryRequest(channel=utils.get_input_channel(input_peer), max_id=msg_id))
        else:
            await client(MessagesReadHistoryRequest(peer=input_peer, max_id=msg_id))
    except Exception:
        pass


class PartialSendError(Exception):
    """Some items of a multi-message post were delivered before an error."""

    def __init__(self, sent_items: int, original: BaseException):
        super().__init__(f"{sent_items} item(s) sent before: {original!r}")
        self.sent_items = sent_items
        self.original = original


# Errors after which the server definitely did NOT publish our message.
_DEFINITE_NOT_SENT = (
    errors.SlowModeWaitError,
    errors.FloodWaitError,
    errors.ChatWriteForbiddenError,
    errors.UserBannedInChannelError,
    errors.ChannelPrivateError,
)

# Telegram user ids of every account running in this process. Messages from
# our own accounts must never advance a counter, otherwise two accounts in the
# same chat keep triggering each other forever.
OWN_ACCOUNT_IDS: Set[int] = set()


class BroadcasterService:
    _ACTIVE_INSTANCES: Dict[int, 'BroadcasterService'] = {}
    FOLDER_SYNC_INTERVAL = 60.0
    RETRY_DELAY = 30.0          # seconds before retrying a definitely-failed send
    PAUSE_BETWEEN_CHATS = 1.5   # anti-flood pause between different chats

    def __init__(self, client: TelegramClient, account_id: int = 0):
        self.client = client
        self.account_id = account_id
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self._chat_locks: Dict[int, asyncio.Lock] = {}
        self._sending_chats: Set[int] = set()
        self._last_sent_chat: Dict[int, float] = {}
        self._flood_until = 0.0
        self._folder_cache: Dict[Tuple[str, str], Tuple[float, List[int]]] = {}
        self._last_folder_log: Dict[Tuple[str, str], float] = {}
        self._bg_tasks: Set[asyncio.Task] = set()

    # ------------------------------------------------------------------ life
    def start(self):
        old = BroadcasterService._ACTIVE_INSTANCES.get(self.account_id)
        if old and old is not self:
            old.stop()
        BroadcasterService._ACTIVE_INSTANCES[self.account_id] = self
        if self.account_id:
            OWN_ACCOUNT_IDS.add(int(self.account_id))
            try:
                db.adopt_legacy_rows(self.account_id)
            except Exception as e:
                log_error(f"[DB] Не удалось привязать старые рассылки к аккаунту: {e}")

        if self.is_running and self._task and not self._task.done():
            return
        self.is_running = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self._task = loop.create_task(self._loop())

    def stop(self):
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
        for t in list(self._bg_tasks):
            if not t.done():
                t.cancel()
        self._bg_tasks.clear()
        self._sending_chats.clear()
        self._folder_cache.clear()
        if BroadcasterService._ACTIVE_INSTANCES.get(self.account_id) is self:
            BroadcasterService._ACTIVE_INSTANCES.pop(self.account_id, None)

    def _chat_lock(self, chat_id: int) -> asyncio.Lock:
        lock = self._chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_id] = lock
        return lock

    # --------------------------------------------------------------- folders
    async def sync_folder_broadcasts(self) -> int:
        """Enrol chats that appeared in a folder, park chats that left it."""
        folder_broadcasts = db.get_active_folder_broadcasts(account_id=self.account_id)
        if not folder_broadcasts:
            return 0

        enrolled_total = 0
        for fb in folder_broadcasts:
            folder_name = (fb.get("folder_name") or "").strip()
            template_name = fb.get("template_name") or ""
            if not folder_name or not template_name:
                continue

            cache_key = (folder_name, template_name)
            try:
                live_chats = await get_chats_in_folder(self.client, folder_name)
            except Exception as e:
                log_error(f"[FOLDER] Не удалось прочитать папку \"{folder_name}\": {e}")
                continue
            if not live_chats:
                continue

            live_set = set(live_chats)
            enrolled_set = db.get_enrolled_chat_ids(template_name, folder_name, account_id=self.account_id)
            added = live_set - enrolled_set
            removed = enrolled_set - live_set

            for cid in sorted(added):
                try:
                    if db.ensure_broadcast_task(
                        account_id=self.account_id,
                        chat_id=cid,
                        template_name=template_name,
                        mode=fb.get("mode") or "interval",
                        interval_seconds=fb.get("interval_seconds") or 0,
                        counter_threshold=fb.get("counter_threshold") or 0,
                        folder_name=folder_name,
                    ):
                        enrolled_total += 1
                except Exception as e:
                    log_error(f"[FOLDER] Не удалось добавить чат {cid} в рассылку \"{template_name}\": {e}")

            for cid in sorted(removed):
                try:
                    db.deactivate_broadcast_task(cid, template_name, account_id=self.account_id)
                except Exception as e:
                    log_error(f"[FOLDER] Не удалось снять чат {cid} с рассылки \"{template_name}\": {e}")

            self._folder_cache[cache_key] = (time.time(), list(live_chats))
            if added or removed:
                last_log = self._last_folder_log.get(cache_key, 0.0)
                if time.time() - last_log > 60.0:
                    self._last_folder_log[cache_key] = time.time()
                    log_info(
                        f"[FOLDER] \"{template_name}\" (\"{folder_name}\"): +{len(added)} / -{len(removed)}, всего {len(live_set)}"
                    )
        return enrolled_total

    # ------------------------------------------------------------ scheduler
    async def _loop(self):
        import gc
        last_gc = time.time()
        last_collector_clean = time.time()
        last_folder_sync = 0.0

        while self.is_running:
            try:
                now = time.time()
                if now - last_collector_clean > 30:
                    for k in [k for k, s in ACTIVE_COLLECTORS.items() if now - s.get("last_active", 0) > 300]:
                        ACTIVE_COLLECTORS.pop(k, None)
                    last_collector_clean = now
                if now - last_gc > 600:
                    gc.collect()
                    last_gc = now
                if now - last_folder_sync >= self.FOLDER_SYNC_INTERVAL:
                    last_folder_sync = now
                    try:
                        await self.sync_folder_broadcasts()
                    except Exception as e:
                        log_error(f"[FOLDER] Ошибка синхронизации папок: {e}")

                if now < self._flood_until:
                    await asyncio.sleep(min(5.0, self._flood_until - now))
                    continue

                for task in db.get_active_interval_tasks(account_id=self.account_id):
                    if not self.is_running:
                        break
                    if time.time() < self._flood_until:
                        break
                    sent = await self._run_scheduled(task)
                    if sent:
                        await asyncio.sleep(self.PAUSE_BETWEEN_CHATS)

                await asyncio.sleep(self._next_wake())
            except asyncio.CancelledError:
                break
            except Exception as e:
                log_error(f"[LOOP] Ошибка планировщика: {type(e).__name__}: {e}")
                await asyncio.sleep(3.0)

    def _next_wake(self) -> float:
        try:
            tasks = db.get_active_interval_tasks(account_id=self.account_id)
        except Exception:
            return 5.0
        wait = 5.0
        now = time.time()
        for t in tasks:
            left = (t.get("interval_seconds") or 0) - (now - (t.get("last_sent_at") or 0))
            wait = min(wait, max(left, 0.0))
        return max(1.0, wait)

    async def _run_scheduled(self, task: Dict[str, Any]) -> bool:
        """Claim a due interval/hybrid task atomically, then send it."""
        mode = task.get("mode", "interval")
        if mode == "hybrid":
            claimed = db.claim_hybrid_task(task["id"], time.time())
        else:
            claimed = db.claim_interval_task(task["id"], time.time())
        if not claimed:
            return False
        return await self._deliver(task)

    # -------------------------------------------------------------- counter
    def on_incoming_message(self, chat_id: int):
        """Called from the event handler. Never blocks update processing."""
        t = asyncio.ensure_future(self.handle_counter_event(chat_id))
        self._bg_tasks.add(t)
        t.add_done_callback(self._bg_tasks.discard)

    async def handle_counter_event(self, chat_id: int):
        # increment_and_check_counter() resets the counter in the same
        # statement that reports it ready, so a task can only be claimed once.
        for task in db.increment_and_check_counter(chat_id, account_id=self.account_id):
            await self._deliver(task)

    # ---------------------------------------------------------------- send
    async def _deliver(self, task: Dict[str, Any]) -> bool:
        chat_id = task["chat_id"]
        template_name = task["template_name"]
        rotation_tpl = db.get_next_rotation_template(chat_id, account_id=self.account_id, rotation_name=template_name)
        effective_name = rotation_tpl or template_name

        outcome = await self._send_task(task["id"], chat_id, effective_name, task=task)
        if outcome == "retry":
            db.schedule_retry(task, delay=self.RETRY_DELAY)
        return outcome == "sent"

    async def _send_task(self, task_id: int, chat_id: int, template_name: str, task: Optional[Dict[str, Any]] = None) -> str:
        """
        Returns "sent", "retry" (server rejected it, try again later) or "skip".

        A network error in the middle of a send is ambiguous: Telegram may well
        have published the message already. Those are treated as sent, because
        resending them is exactly how one scheduled post turned into two.
        """
        template = db.get_template(template_name)
        if not template:
            log_error(f"[ERROR] Шаблон '{template_name}' не найден")
            return "skip"

        lock = self._chat_lock(chat_id)
        if lock.locked():
            log_info(f"[DEDUP] Чат {chat_id}: отправка уже идёт, дубликат пропущен.")
            return "skip"

        async with lock:
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
                    bundle=template.get("bundle"),
                )
                self._last_sent_chat[chat_id] = time.time()
                return "sent"
            except PartialSendError as e:
                log_error(f"[PARTIAL] Чат {chat_id}: отправлено {e.sent_items} из пачки, остаток пропущен ({e.original}).")
                self._last_sent_chat[chat_id] = time.time()
                return "sent"
            except errors.SlowModeWaitError as e:
                log_error(f"[SLOWMODE] Чат {chat_id}: медленный режим, ждать {e.seconds}с")
                if task is not None:
                    db.schedule_retry(task, delay=e.seconds + 2)
                return "skip"
            except errors.FloodWaitError as e:
                log_error(f"[FLOOD] FloodWait {e.seconds}с (чат {chat_id}) — пауза всех отправок аккаунта")
                self._flood_until = time.time() + e.seconds + 1
                if task is not None:
                    db.schedule_retry(task, delay=e.seconds + 2)
                return "skip"
            except (errors.UserBannedInChannelError, errors.ChannelPrivateError) as e:
                log_error(f"[PERM] Нет доступа к чату {chat_id} ({type(e).__name__}). Рассылка в нём отключена.")
                db.deactivate_task(task_id)
                return "skip"
            except errors.ChatWriteForbiddenError:
                log_error(f"[RESTRICT] Чат {chat_id}: писать запрещено, повтор позже.")
                if task is not None:
                    db.schedule_retry(task, delay=600)
                return "skip"
            except errors.RPCError as e:
                log_error(f"[ERROR] Telegram отклонил отправку в чат {chat_id}: {e}")
                return "retry"
            except asyncio.CancelledError:
                raise
            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                log_error(f"[NET] Чат {chat_id}: связь оборвалась во время отправки ({e}). Не повторяю, чтобы не было дубля.")
                return "sent"
            except Exception as e:
                log_error(f"[ERROR] Ошибка отправки в чат {chat_id}: {type(e).__name__}: {e}")
                return "retry"
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
• Гасит только те спам-сообщения, которые **пинганули тебя** (упоминание, ответ на твой пост, скрытый тег). Обычная реклама в чате не трогается.
• Снимается только пинг этого спам-сообщения. Если в чате есть непрочитанный ответ от живого человека, он остаётся непрочитанным.
• Ловит скупку/продажу звёзд, TON, NFT-подарков, аккаунтов, «заработок», казино, накрутку, скрытые теги и вотермарки автопостеров.
• Автоматически вступает в каналы/боты по требованию капчи админов, мьютит их и убирает в папку `автосабнутое`.

**9. Заглушение конкретных людей:**
* `.мутить` — ответьте командой на сообщение (самый точный способ)
* `.мутить `@username`
* `.мутить 123456789`
* `.мутить 123456789 причина` — сохранит причину
* `.размутить `@username` или `.размутить 123456789`
* `.заглушенные` — список всех как заглушены
*(Заглушенные не запускают авто-подписку и не увеличивают счётчик рассылки)*
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

def _strip_command_word(text: str) -> str:
    """
    Drop the leading command word (and its dot) from an argument string.

    Commands reach the handlers as the full raw line, so without this the first
    token is always ".мутить"/".mute" rather than the actual argument. Both mute
    handlers compare parts[0] against the numeric and username patterns, so an
    unstripped command word made every ".мутить @user" and ".мутить 123456789"
    form fail silently.
    """
    text = (text or "").strip()
    tokens = text.split(None, 1)
    if not tokens:
        return ""
    first = tokens[0]
    if first.startswith("."):
        return tokens[1] if len(tokens) > 1 else ""

    # Multiline form: the command sits alone on the first line and the target
    # follows on the next one.
    lines = text.splitlines()
    if len(lines) > 1 and not lines[0].strip().startswith("@"):
        return "\n".join(lines[1:]).strip()

    # Single-line form without a dot (some clients strip the leading dot): strip
    # the first word so the argument, not the verb, reaches the resolver.
    if len(tokens) > 1:
        return tokens[1]
    return ""

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
        self._deque: Deque[Tuple[Any, int]] = collections.deque()
        self._set: Set[Tuple[Any, int]] = set()

    def check_and_add(self, chat_id: Any, msg_id: int) -> bool:
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
    my_username = getattr(getattr(client, "_self_user", None), "username", None) or acc_cfg.get("username") or ""

    # Warm the mute index so the very first incoming message is filtered too.
    mute_cache_rebuild(my_id)

    @client.on(events.NewMessage(incoming=True))
    async def incoming_handler(event: Any):
        chat_id = event.chat_id
        msg_id = getattr(event, "id", None)
        if msg_id and chat_id:
            # Keyed per account: with two accounts in one chat the second one
            # used to see the message as a "duplicate" and ignore it.
            if _GLOBAL_DEDUP_RING.check_and_add((my_id, chat_id), msg_id):
                return

        sender_id = event.sender_id
        # Our own other accounts never count and are never "spam".
        if sender_id is not None and _strip_peer_id(sender_id) in OWN_ACCOUNT_IDS:
            return

        active_broadcaster = getattr(client, "_spambuster_broadcaster", broadcaster)
        if is_peer_muted(my_id, sender_id, chat_id):
            log_info(f"[MUTE] Чат {chat_id}: сообщение от {sender_id} скрыто по списку мута.")
            return

        # Counter broadcasts run in the background: a slow media upload must
        # not delay spam clearing or command handling.
        active_broadcaster.on_incoming_message(chat_id)
        if event.is_private:
            return

        text = event.raw_text or ""
        message = event.message

        # Did this message actually reach us? (mention / reply to our post /
        # our @handle typed in the text)
        is_ping = bool(getattr(message, "mentioned", False))
        reply_to_me = False
        if not is_ping and event.is_reply:
            try:
                reply_msg = await event.get_reply_message()
                reply_to_me = bool(reply_msg and reply_msg.sender_id == my_id)
            except Exception:
                reply_to_me = False
            is_ping = reply_to_me
        if not is_ping and my_username and text:
            if re.search(rf"(?<![\w@])@{re.escape(my_username)}(?!\w)", text, re.IGNORECASE):
                is_ping = True

        if auto_read_spam and is_ping:
            target_peer = getattr(event, "input_chat", None) or chat_id
            if should_auto_read(text, message, is_ping=True):
                await clear_spam_mention(client, target_peer, message, account_id=my_id, chat_id=chat_id)
            else:
                note_genuine_ping(my_id, chat_id, msg_id or 0)

        if auto_sub and is_gatekeeper_text(text):
            if is_ping or str(my_id) in text:
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
                    ".закончить", ".завершить", ".готово",
                    ".мутить", ".мут", ".mute", ".ignore",
                    ".размутить", ".размут", ".unmute", ".unignore",
                    ".заглушенные", ".мутлист", ".mutelist"
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

        if cmd in (".мутить", ".мут", ".mute", ".ignore"):
            await handle_mute(event, raw)
            return

        if cmd in (".размутить", ".размут", ".unmute", ".unignore"):
            await handle_unmute(event, raw)
            return

        if cmd in (".заглушенные", ".мутлист", ".mutelist"):
            await handle_mute_list(event)
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

        # Albums arrive as N separate messages, handled concurrently. The old
        # code downloaded the whole album for EVERY part (an album of 3 became
        # 9 files) and, because the item was appended only after an await,
        # parts could also become separate items. Both meant one post was sent
        # as two or more. Register the album synchronously, download it once.
        if grouped_id:
            albums = session.setdefault("_albums", {})
            existing = albums.get(grouped_id)
            if existing is not None:
                if msg.id not in existing["forward_msg_ids"]:
                    existing["forward_msg_ids"].append(msg.id)
                    existing["forward_msg_ids"].sort()
                if (not existing["text"]) and (msg.raw_text or msg.entities):
                    existing["text"] = msg.raw_text or ""
                    existing["entities_hex"] = serialize_entities(msg.entities)
                return
            item = {
                "text": text,
                "entities_hex": serialize_entities(msg.entities),
                "media_files": [],
                "is_forward": bool(getattr(msg, "fwd_from", None)) or (session["session_type"] == "forwarded"),
                "forward_chat_id": event.chat_id,
                "forward_msg_ids": [msg.id],
                "grouped_id": grouped_id,
            }
            albums[grouped_id] = item
            session["items"].append(item)
            item_index = len(session["items"]) - 1
            await asyncio.sleep(1.5)  # let the remaining album parts arrive
            try:
                item["media_files"] = await save_media_from_message(
                    client, msg, template_name, item_index=item_index, clear_existing=False
                )
            except Exception as e:
                log_error(f"[COLLECT] Не удалось скачать альбом: {e}")
            await _notify(
                event,
                f"📥 Сообщение #{item_index + 1} (альбом, {len(item['media_files'])} медиа) добавлено в рассылку **{template_name}**.\n"
                f"Отправьте следующее сообщение или напишите `.закрыть` (для отмены `.отменить`).",
                auto_delete=4,
                force_respond=True
            )
            return

        item_index = len(session["items"])
        placeholder: Dict[str, Any] = {}
        session["items"].append(placeholder)  # reserve the slot before awaiting
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
        placeholder.update(item)
        count = item_index + 1
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
        items = [
            {k: v for k, v in it.items() if not k.startswith("_")}
            for it in session["items"]
            if it and (it.get("text") or it.get("media_files") or it.get("forward_msg_ids"))
            and not is_internal_bot_message(it.get("text", ""))
        ]
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

        if folder_name:
            db.register_folder_broadcast(
                folder_name=folder_name,
                template_name=template_name,
                mode=mode,
                interval_seconds=interval_sec,
                counter_threshold=threshold_count,
                account_id=my_id
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
                    album_messages = await fetch_album(client, reply_msg)

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

        if folder_name:
            db.register_folder_broadcast(
                folder_name=folder_name,
                template_name=template_name,
                mode=mode,
                interval_seconds=interval_sec,
                counter_threshold=threshold_count,
                account_id=my_id
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

    async def _resolve_mute_target(event: Any, raw_args: str) -> Optional[Dict[str, Any]]:
        """
        Resolve a mute target from a command argument.

        Accepted forms, in priority order:
          .мутить                    -> reply to the message (recommended)
          .мутить @username          -> resolve the handle
          .мутить 123456789          -> bare user id
          .мутить 123456789 причина  -> bare user id plus a stored reason
        """
        args = raw_args.strip()
        reason = ""
        parts = [p for p in args.split() if p]
        if len(parts) > 1 and re.fullmatch(r"-?\d{5,}", parts[0]):
            reason = " ".join(parts[1:]).strip()

        # 1. Reply form wins: it is the only unambiguous source of identity.
        if event.is_reply:
            try:
                reply_msg = await event.get_reply_message()
            except Exception:
                reply_msg = None
            if reply_msg is not None:
                sender_id = getattr(reply_msg, "sender_id", None)
                if sender_id is None:
                    return None
                peer_key = _strip_peer_id(sender_id)
                display_name = ""
                username = ""
                try:
                    sender = await reply_msg.get_sender()
                    display_name = getattr(sender, "title", None) or " ".join(
                        filter(None, [getattr(sender, "first_name", None), getattr(sender, "last_name", None)])
                    )
                    username = getattr(sender, "username", None) or ""
                except Exception:
                    pass
                if not reason:
                    reason = (reply_msg.raw_text or "")[:80].strip()
                return {
                    "peer_id": sender_id,
                    "peer_key": peer_key,
                    "username": username,
                    "display_name": display_name or f"id {peer_key}",
                    "scope": "user",
                    "reason": reason,
                }

        if not parts:
            return None

        token = parts[0]

        # 2. Numeric id
        if re.fullmatch(r"-?\d{5,}", token):
            raw_id = int(token)
            peer_key = _strip_peer_id(raw_id)
            display_name = f"id {peer_key}"
            username = ""
            try:
                entity = await client.get_entity(peer_key)
                display_name = (
                    getattr(entity, "title", None)
                    or " ".join(filter(None, [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]))
                    or display_name
                )
                username = getattr(entity, "username", None) or ""
            except Exception:
                pass
            return {
                "peer_id": raw_id,
                "peer_key": peer_key,
                "username": username,
                "display_name": display_name,
                "scope": "user",
                "reason": reason,
            }

        # 3. Username
        if token.startswith("@") or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", token):
            handle = token.lstrip("@")
            try:
                entity = await client.get_entity(handle)
            except Exception:
                return None
            raw_id = utils.get_peer_id(entity)
            peer_key = _strip_peer_id(raw_id)
            display_name = (
                getattr(entity, "title", None)
                or " ".join(filter(None, [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]))
                or f"@{handle}"
            )
            return {
                "peer_id": raw_id,
                "peer_key": peer_key,
                "username": getattr(entity, "username", None) or handle,
                "display_name": display_name,
                "scope": "user",
                "reason": reason,
            }

        return None

    async def handle_mute(event: Any, raw: str):
        _, args = _extract_quoted_folder(raw)
        target = await _resolve_mute_target(event, _strip_command_word(args))
        if not target:
            await _notify(
                event,
                "⚠️ Не понял кого мутить.\n"
                "Формы:\n"
                "• `.мутить` — ответьте командой на сообщение\n"
                "• `.мутить @username`\n"
                "• `.мутить 123456789`\n"
                "• `.мутить 123456789 причина`",
            )
            return

        added = db.add_muted_peer(
            peer_id=target["peer_id"],
            account_id=my_id,
            peer_key=target["peer_key"],
            username=target["username"],
            display_name=target["display_name"],
            scope=target["scope"],
            reason=target["reason"],
        )
        mute_cache_rebuild(my_id)
        verb = "Заглушен" if added else "Уже был заглушен"
        reason_str = f"\nПричина: {target['reason']}" if target["reason"] else ""
        await _notify(event, f"🔇 {verb}: **{target['display_name']}**{reason_str}")

    async def handle_unmute(event: Any, raw: str):
        _, args = _extract_quoted_folder(raw)
        args_clean = _strip_command_word(args).strip()
        if not args_clean:
            await _notify(event, "⚠️ Укажите @username или id, либо ответьте командой на сообщение.")
            return

        target = await _resolve_mute_target(event, args_clean)
        if target:
            removed = db.remove_muted_peer(target["peer_key"], account_id=my_id, scope=target["scope"])
        else:
            token = args_clean.split()[0].lstrip("@")
            found = db.find_muted_peer_by_username(token, account_id=my_id)
            if found:
                removed = db.remove_muted_peer(found["peer_key"], account_id=my_id, scope=found.get("scope") or "user")
                target = {"display_name": found.get("display_name") or f"@{token}"}
            else:
                removed = 0
                target = {"display_name": token}

        mute_cache_rebuild(my_id)
        if removed:
            await _notify(event, f"🔊 Разглушен: **{target['display_name']}**")
        else:
            await _notify(event, f"ℹ️ **{target['display_name']}** не был в списке заглушенных.")

    async def handle_mute_list(event: Any):
        rows = db.list_muted_peers(account_id=my_id)
        if not rows:
            await _notify(event, "🔇 Список заглушенных пуст.")
            return
        lines = ["🔇 **Заглушенные:**\n"]
        for r in rows[:50]:
            handle = f"@{r['username']}" if r["username"] else f"id {r['peer_key']}"
            label = r["display_name"] or handle
            reason = f" — {r['reason']}" if r["reason"] else ""
            lines.append(f"• {label} (`{handle}`){reason}")
        if len(rows) > 50:
            lines.append(f"\n…и ещё {len(rows) - 50}")
        await _notify(event, "\n".join(lines), auto_delete=25)

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

        db.set_folder_broadcast_status(
            folder_name=folder_name or None,
            name=target_name,
            is_active=0,
            account_id=my_id
        )
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

        db.set_folder_broadcast_status(
            folder_name=folder_name or None,
            name=target_name,
            is_active=1,
            account_id=my_id
        )
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

        db.delete_folder_broadcasts(
            folder_name=folder_name or None,
            name=target_name,
            account_id=my_id
        )

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

    client._self_user = me
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
