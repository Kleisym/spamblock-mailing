import asyncio
import os
import sys
import threading
import traceback
from pathlib import Path
from telethon import TelegramClient, errors
import bot

_client = None
_broadcaster = None
_loop = None
_is_running = False
_bridge_lock = threading.Lock()

def is_running():
    global _is_running
    return _is_running

def stop_bot():
    global _client, _broadcaster, _loop, _is_running
    with _bridge_lock:
        _is_running = False
        broadcaster_to_stop = _broadcaster
        client_to_disconnect = _client
        loop_to_stop = _loop
        _broadcaster = None
        _client = None

    if broadcaster_to_stop:
        try:
            broadcaster_to_stop.stop()
        except Exception:
            pass

    if client_to_disconnect and loop_to_stop and loop_to_stop.is_running():
        async def _disconnect():
            try:
                await client_to_disconnect.disconnect()
            except Exception:
                pass
        try:
            future = asyncio.run_coroutine_threadsafe(_disconnect(), loop_to_stop)
            try:
                future.result(timeout=2.0)
            except Exception:
                pass
        except Exception:
            pass

    if loop_to_stop and loop_to_stop.is_running():
        try:
            loop_to_stop.call_soon_threadsafe(loop_to_stop.stop)
        except Exception:
            pass

def start_bot(api_id_val, api_hash_val, phone_val, password_2fa_val, files_dir_val, callback):
    global _client, _broadcaster, _loop, _is_running

    # Safely stop any previously running bot instance before creating a new one
    stop_bot()

    with _bridge_lock:
        try:
            api_id = int(str(api_id_val).strip())
            api_hash = str(api_hash_val).strip()
            phone = str(phone_val).strip()
            pwd_2fa = str(password_2fa_val).strip() if password_2fa_val else ""
            app_files_dir = str(files_dir_val).strip()

            bot.init_app_storage(app_files_dir)

            _loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_loop)

            session_path = os.path.join(app_files_dir, "spambuster_session")
            # connection_retries=None, retry_delay=2, auto_reconnect=True for infinite retries across VPN/network changes
            _client = TelegramClient(
                session_path,
                api_id,
                api_hash,
                catch_up=False,
                connection_retries=None,
                retry_delay=2,
                auto_reconnect=True,
                timeout=15,
            )

            current_loop = _loop
            current_client = _client
            _is_running = True
        except Exception as e:
            _is_running = False
            err_msg = f"{type(e).__name__}: {str(e)}"
            traceback.print_exc()
            if callback:
                callback.onError(err_msg)
                callback.onStatusChange("Ошибка: " + err_msg, False)
            return

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
        try:
            # Connect loop: retry persistently if VPN is down when service starts
            while _is_running:
                try:
                    if callback:
                        callback.onStatusChange("Подключение к Telegram...", True)

                    await current_client.start(
                        phone=phone,
                        code_callback=code_provider,
                        password=password_provider,
                        max_attempts=3
                    )
                    break
                except (errors.ApiIdInvalidError, errors.PhoneNumberInvalidError, errors.PhoneCodeInvalidError, errors.PasswordHashInvalidError):
                    raise
                except Exception as e:
                    bot.log_error(f"[START] Ошибка сети при старте (проверьте интернет / VPN): {e}. Повтор через 4с...")
                    if callback:
                        callback.onStatusChange("Ожидание сети / VPN...", True)
                    try:
                        await current_client.disconnect()
                    except Exception:
                        pass
                    await asyncio.sleep(4)

            if not _is_running:
                return

            me = await current_client.get_me()
            username = f"@{me.username}" if me.username else (me.first_name or "Пользователь")

            with _bridge_lock:
                _broadcaster = bot.BroadcasterService(current_client, account_id=me.id)
                _broadcaster.start()

            if callback:
                callback.onLoggedIn(username, int(me.id))
                callback.onStatusChange(f"Работает ({username})", True)

            acc_cfg = {
                "name": "android",
                "auto_sub_folder": "автосабнутое",
                "auto_read_spam_pings": True,
                "auto_sub_enabled": True,
                "log_errors_only": True,
            }
            bot.register_events(current_client, _broadcaster, me.id, acc_cfg=acc_cfg)

            # 24/7 Persistent supervisor loop across network cuts & VPN toggles
            while _is_running:
                try:
                    if not current_client.is_connected():
                        if callback:
                            callback.onStatusChange("Восстановление связи с Telegram...", True)
                        try:
                            await current_client.disconnect()
                        except Exception:
                            pass
                        await current_client.connect()
                        if callback:
                            callback.onStatusChange(f"Работает ({username})", True)

                    await current_client.run_until_disconnected()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    bot.log_error(f"[SUPERVISOR] Потеря связи (VPN выключен / смена сети): {e}. Автоматический реконнект через 3с...")
                    if callback:
                        callback.onStatusChange("Связь потеряна. Ожидание сети / VPN...", True)
                    try:
                        await current_client.disconnect()
                    except Exception:
                        pass
                    await asyncio.sleep(3)

        finally:
            with _bridge_lock:
                if _broadcaster:
                    _broadcaster.stop()
                    _broadcaster = None
                _is_running = False
            if callback:
                callback.onStatusChange("Остановлен", False)

    try:
        current_loop.run_until_complete(run())
    except Exception as e:
        with _bridge_lock:
            _is_running = False
        err_msg = f"{type(e).__name__}: {str(e)}"
        traceback.print_exc()
        if callback:
            callback.onError(err_msg)
            callback.onStatusChange("Ошибка: " + err_msg, False)
