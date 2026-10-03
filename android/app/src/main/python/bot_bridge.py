"""
Bridge between the Android service (Kotlin, via Chaquopy) and bot.py.

start_bot() blocks its (dedicated) thread for the whole lifetime of the bot.
stop_bot() may be called from any thread.
"""
import asyncio
import os
import threading
import traceback

from telethon import TelegramClient, errors
from telethon.tl.functions.updates import GetStateRequest

import bot

_client = None
_broadcaster = None
_loop = None
_is_running = False
_bridge_lock = threading.Lock()

# How often the supervisor checks the connection, and how often it sends a
# real request to prove the socket is alive (a half-dead socket after doze or
# a Wi-Fi <-> mobile handover still reports is_connected() == True).
SUPERVISOR_POLL_SECONDS = 15
HEALTH_CHECK_SECONDS = 120
HEALTH_CHECK_TIMEOUT = 25

# Errors that retrying cannot fix: stop and tell the user.
_FATAL_AUTH_ERRORS = (
    errors.ApiIdInvalidError,
    errors.PhoneNumberInvalidError,
    errors.PhoneNumberBannedError,
    errors.PhoneCodeInvalidError,
    errors.PhoneCodeExpiredError,
    errors.PasswordHashInvalidError,
    errors.AuthKeyDuplicatedError,
    errors.AuthKeyUnregisteredError,
    errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
)


class _LoginCancelled(Exception):
    pass


def is_running():
    return _is_running


def stop_bot():
    global _client, _broadcaster, _is_running
    with _bridge_lock:
        _is_running = False
        broadcaster_to_stop = _broadcaster
        client_to_disconnect = _client
        loop_to_stop = _loop
        _broadcaster = None
        _client = None

    if loop_to_stop is None or not loop_to_stop.is_running():
        return

    def _stop_in_loop():
        if broadcaster_to_stop:
            try:
                broadcaster_to_stop.stop()
            except Exception:
                pass
        if client_to_disconnect:
            asyncio.ensure_future(_safe_disconnect(client_to_disconnect))

    try:
        loop_to_stop.call_soon_threadsafe(_stop_in_loop)
    except Exception:
        pass


async def _safe_disconnect(client):
    try:
        await client.disconnect()
    except Exception:
        pass


def _status(callback, text, running=True):
    if callback:
        try:
            callback.onStatusChange(text, running)
        except Exception:
            pass


def start_bot(api_id_val, api_hash_val, phone_val, password_2fa_val, files_dir_val, callback):
    global _client, _loop, _is_running

    stop_bot()

    try:
        api_id = int(str(api_id_val).strip())
        api_hash = str(api_hash_val).strip()
        phone = str(phone_val).strip().replace(" ", "")
        pwd_2fa = str(password_2fa_val).strip() if password_2fa_val else ""
        app_files_dir = str(files_dir_val).strip()
        bot.init_app_storage(app_files_dir)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        traceback.print_exc()
        if callback:
            callback.onError(err)
        _status(callback, "Ошибка: " + err, False)
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = TelegramClient(
        os.path.join(app_files_dir, "spambuster_session"),
        api_id,
        api_hash,
        catch_up=False,
        connection_retries=None,   # retry forever across VPN / network switches
        retry_delay=3,
        auto_reconnect=True,
        timeout=20,
        request_retries=3,
        flood_sleep_threshold=60,
        device_model="Android",
        system_version="Spambuster",
        app_version="2.0",
    )

    with _bridge_lock:
        _loop = loop
        _client = client
        _is_running = True

    def code_provider():
        _status(callback, "Ожидание кода из Telegram...")
        code = str(callback.requestCode() if callback else "").strip()
        if not code:
            raise _LoginCancelled("Код не введён")
        return code

    def password_provider():
        if pwd_2fa:
            return pwd_2fa
        _status(callback, "Ожидание пароля 2FA...")
        pwd = str(callback.requestPassword() if callback else "").strip()
        if not pwd:
            raise _LoginCancelled("Пароль 2FA не введён")
        return pwd

    async def login():
        """Connect with backoff. Only network errors are retried; login is
        attempted once, so Telegram is never spammed with code requests."""
        delay = 3
        while _is_running:
            try:
                _status(callback, "Подключение к Telegram...")
                await client.connect()
                break
            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                bot.log_error(f"[START] Нет сети / VPN: {e}. Повтор через {delay}с")
                _status(callback, "Ожидание сети / VPN...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
        if not _is_running:
            return False
        if not await client.is_user_authorized():
            await client.start(
                phone=phone,
                code_callback=code_provider,
                password=password_provider,
                max_attempts=1,
            )
        return True

    async def health_check():
        try:
            await asyncio.wait_for(client(GetStateRequest()), timeout=HEALTH_CHECK_TIMEOUT)
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

    async def run():
        global _broadcaster
        broadcaster = None
        try:
            if not await login():
                return
            me = await client.get_me()
            username = f"@{me.username}" if me.username else (me.first_name or "Пользователь")
            client._self_user = me

            broadcaster = bot.BroadcasterService(client, account_id=me.id)
            with _bridge_lock:
                _broadcaster = broadcaster
            broadcaster.start()
            bot.register_events(client, broadcaster, me.id, acc_cfg={
                "name": "android",
                "auto_sub_folder": "автосабнутое",
                "auto_read_spam_pings": True,
                "auto_sub_enabled": True,
                "log_errors_only": True,
                "username": me.username or "",
            })

            # Tell Telegram we want updates.
            await health_check()
            if callback:
                callback.onLoggedIn(username, int(me.id))
            _status(callback, f"Работает ({username})")

            # Supervisor. IMPORTANT: never wrap run_until_disconnected() in a
            # timeout - when it is cancelled it calls client.disconnect(), which
            # the old code did every 20 s, killing sends in flight (and causing
            # resends = duplicate posts) and dropping updates.
            last_health = asyncio.get_running_loop().time()
            healthy = True
            while _is_running:
                await asyncio.wait({client.disconnected}, timeout=SUPERVISOR_POLL_SECONDS)
                if not _is_running:
                    break

                now = asyncio.get_running_loop().time()
                dead = not client.is_connected()
                if not dead and now - last_health >= HEALTH_CHECK_SECONDS:
                    last_health = now
                    dead = not await health_check()

                if dead:
                    if healthy:
                        _status(callback, "Связь потеряна. Переподключение...")
                    healthy = False
                    await _safe_disconnect(client)
                    try:
                        await client.connect()
                        if await health_check():
                            healthy = True
                            last_health = asyncio.get_running_loop().time()
                            _status(callback, f"Работает ({username})")
                    except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                        bot.log_error(f"[SUPERVISOR] Реконнект не удался: {e}")
                        await asyncio.sleep(5)
                elif not healthy:
                    healthy = True
                    _status(callback, f"Работает ({username})")
        finally:
            if broadcaster:
                broadcaster.stop()
            with _bridge_lock:
                if _broadcaster is broadcaster:
                    _broadcaster = None
            await _safe_disconnect(client)

    try:
        loop.run_until_complete(run())
        _status(callback, "Остановлен", False)
    except _LoginCancelled as e:
        _status(callback, f"Вход отменён: {e}", False)
        if callback:
            callback.onError(str(e))
    except _FATAL_AUTH_ERRORS as e:
        err = f"{type(e).__name__}: {e}"
        _status(callback, "Ошибка входа: " + err, False)
        if callback:
            callback.onError(err)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        traceback.print_exc()
        _status(callback, "Ошибка: " + err, False)
        if callback:
            callback.onError(err)
    finally:
        with _bridge_lock:
            _is_running = False
            if _client is client:
                _client = None
            if _loop is loop:
                _loop = None
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
        except Exception:
            pass
