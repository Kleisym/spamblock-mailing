import asyncio
import os
import sys
import traceback
from pathlib import Path
from telethon import TelegramClient
import bot

_client = None
_broadcaster = None
_loop = None
_is_running = False

def is_running():
    global _is_running
    return _is_running

def start_bot(api_id_val, api_hash_val, phone_val, password_2fa_val, files_dir_val, callback):
    global _client, _broadcaster, _loop, _is_running
    try:
        api_id = int(str(api_id_val).strip())
        api_hash = str(api_hash_val).strip()
        phone = str(phone_val).strip()
        pwd_2fa = str(password_2fa_val).strip() if password_2fa_val else ""
        app_files_dir = str(files_dir_val).strip()

        # Initialize storage directory inside app internal files
        bot.init_app_storage(app_files_dir)

        # Setup asyncio event loop for this thread
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)

        session_path = os.path.join(app_files_dir, "spambuster_session")
        _client = TelegramClient(session_path, api_id, api_hash, catch_up=False)

        def code_provider():
            if callback:
                callback.onStatusChange("Ожидание кода из Telegram...", True)
                code = callback.requestCode()
                return str(code).strip()
            return ""

        def password_provider():
            if pwd_2fa:
                return pwd_2fa
            if callback:
                callback.onStatusChange("Ожидание пароля 2FA...", True)
                pwd = callback.requestPassword()
                return str(pwd).strip()
            return ""

        async def run():
            global _broadcaster, _is_running
            if callback:
                callback.onStatusChange("Подключение к Telegram...", True)

            await _client.start(
                phone=phone,
                code_callback=code_provider,
                password=password_provider,
                max_attempts=3
            )

            me = await _client.get_me()
            username = f"@{me.username}" if me.username else (me.first_name or "Пользователь")
            _is_running = True

            if callback:
                callback.onLoggedIn(username, int(me.id))
                callback.onStatusChange(f"Работает ({username})", True)

            _broadcaster = bot.BroadcasterService(_client, account_id=me.id)
            _broadcaster.start()

            acc_cfg = {
                "name": "android",
                "auto_sub_folder": "автосабнутое",
                "auto_read_spam_pings": True,
                "auto_sub_enabled": True,
                "log_errors_only": True,
            }
            bot.register_events(_client, _broadcaster, me.id, acc_cfg=acc_cfg)

            try:
                await _client.run_until_disconnected()
            finally:
                if _broadcaster:
                    _broadcaster.stop()
                _is_running = False
                if callback:
                    callback.onStatusChange("Остановлен", False)

        _loop.run_until_complete(run())

    except Exception as e:
        _is_running = False
        err_msg = f"{type(e).__name__}: {str(e)}"
        traceback.print_exc()
        if callback:
            callback.onError(err_msg)
            callback.onStatusChange("Ошибка: " + err_msg, False)

def stop_bot():
    global _client, _broadcaster, _loop, _is_running
    _is_running = False
    if _broadcaster:
        try:
            _broadcaster.stop()
        except Exception:
            pass
    if _client and _loop:
        async def _disconnect():
            try:
                await _client.disconnect()
            except Exception:
                pass
        try:
            asyncio.run_coroutine_threadsafe(_disconnect(), _loop)
        except Exception:
            pass
