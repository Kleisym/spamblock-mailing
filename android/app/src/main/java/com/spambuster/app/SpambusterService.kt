package com.spambuster.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlin.concurrent.thread

class SpambusterService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private var heartbeat: Runnable? = null

    companion object {
        const val CHANNEL_ID = "spambuster_service_channel"
        const val NOTIFICATION_ID = 7771
        const val ACTION_STOP = "com.spambuster.app.ACTION_STOP"
        const val PREFS_NAME = "spambuster_prefs"
        const val KEY_SERVICE_ENABLED = "service_enabled"
        const val ACTION_HEARTBEAT = "com.spambuster.app.ACTION_HEARTBEAT"
        private const val HEARTBEAT_INTERVAL_MS = 60000L
        private const val WAKE_LOCK_TAG = "Spambuster::RuntimeWakeLock"
        private const val MIN_RUNTIME_FOR_RESTART_MS = 60_000L
        private const val RESTART_DELAY_MS = 10_000L

        @Volatile
        var activePythonThread: Thread? = null
        val threadLock = Any()

        @Volatile
        var runtimeWakeLock: PowerManager.WakeLock? = null
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopForegroundService()
            return START_NOT_STICKY
        }

        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        // A null intent means the system restarted us after killing the
        // process. Only honour that restart if the user had not asked us to
        // stop, otherwise the service would resurrect itself after "Стоп".
        val restartingFromOemKill = intent == null
        if (restartingFromOemKill && !prefs.getBoolean(KEY_SERVICE_ENABLED, false)) {
            stopSelf()
            return START_NOT_STICKY
        }

        if (!restartingFromOemKill) {
            prefs.edit().putBoolean(KEY_SERVICE_ENABLED, true).apply()
        }

        val notification = buildForegroundNotification("Инициализация юзербота...")
        try {
            val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            } else 0
            ServiceCompat.startForeground(this, NOTIFICATION_ID, notification, type)
        } catch (e: Exception) {
            // Android 12+ can refuse a foreground start from the background
            // (e.g. a sticky restart on some OEM firmwares). Crashing here used
            // to take the whole app down; retry on the next start instead.
            updateStatusAndNotification("Android не разрешил фоновый запуск. Откройте приложение.", false)
            stopSelf()
            return START_NOT_STICKY
        }

        acquireRuntimeWakeLock()
        startHeartbeat()
        startPythonBot()

        return START_STICKY
    }

    /**
     * Hold a PARTIAL_WAKE_LOCK for the lifetime of the service.
     *
     * The old code only had an unused 30 second temporary lock, so once the
     * screen went off the CPU was allowed to sleep, the asyncio loop stalled and
     * the account silently stopped answering.
     */
    private fun acquireRuntimeWakeLock() {
        if (runtimeWakeLock?.isHeld == true) return
        try {
            val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
            runtimeWakeLock = powerManager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                WAKE_LOCK_TAG
            ).apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (e: Exception) {
            // Wake lock unavailable: the foreground service still keeps the
            // process alive, only CPU-idle hibernation stays possible.
        }
    }

    private fun releaseRuntimeWakeLock() {
        try {
            runtimeWakeLock?.let { if (it.isHeld) it.release() }
        } catch (e: Exception) {
            // ignore
        }
        runtimeWakeLock = null
    }

    /**
     * Re-issue startForeground on a timer.
     *
     * MIUI/HyperOS/OneUI and stock Android all quietly demote or kill a
     * foreground service that has not been refreshed. A periodic startForeground
     * keeps the service pinned in the "running services" list so leaving the app
     * or locking the phone no longer stops the bot.
     */
    private fun startHeartbeat() {
        stopHeartbeat()
        val tick = object : Runnable {
            override fun run() {
                if (BotBridgeCoordinator.isBotRunning) {
                    try {
                        val manager = getSystemService(NotificationManager::class.java)
                        manager?.notify(
                            NOTIFICATION_ID,
                            buildForegroundNotification(BotBridgeCoordinator.lastStatus)
                        )
                    } catch (e: Exception) {
                        // notification refresh is best effort
                    }
                }
                mainHandler.postDelayed(this, HEARTBEAT_INTERVAL_MS)
            }
        }
        heartbeat = tick
        mainHandler.postDelayed(tick, HEARTBEAT_INTERVAL_MS)
    }

    private fun stopHeartbeat() {
        heartbeat?.let { mainHandler.removeCallbacks(it) }
        heartbeat = null
    }

    private fun startPythonBot() {
        synchronized(threadLock) {
            if (activePythonThread != null && activePythonThread?.isAlive == true) {
                return
            }

            val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            val apiId = prefs.getString("api_id", "") ?: ""
            val apiHash = prefs.getString("api_hash", "") ?: ""
            val phone = prefs.getString("phone", "") ?: ""
            val password2FA = prefs.getString("password_2fa", "") ?: ""

            if (apiId.isEmpty() || apiHash.isEmpty()) {
                updateStatusAndNotification("Ошибка: API ID или Hash не заполнены", false)
                return
            }

            val startedAt = System.currentTimeMillis()
            activePythonThread = thread(name = "Spambuster-Python-Thread") {
                try {
                    if (!Python.isStarted()) {
                        Python.start(AndroidPlatform(this@SpambusterService))
                    }
                    val py = Python.getInstance()
                    val bridge = py.getModule("bot_bridge")

                    val callback = object {
                        fun requestCode(): String {
                            updateStatusAndNotification("Требуется код подтверждения Telegram!", true)
                            mainHandler.post {
                                BotBridgeCoordinator.uiListener?.onCodeRequested()
                            }
                            return BotBridgeCoordinator.prepareForCode()
                        }

                        fun requestPassword(): String {
                            updateStatusAndNotification("Требуется пароль 2FA Telegram!", true)
                            mainHandler.post {
                                BotBridgeCoordinator.uiListener?.onPasswordRequested()
                            }
                            return BotBridgeCoordinator.prepareForPassword()
                        }

                        fun onStatusChange(status: String, isRunning: Boolean) {
                            updateStatusAndNotification(status, isRunning)
                        }

                        fun onLoggedIn(username: String, userId: Long) {
                            BotBridgeCoordinator.loggedInUser = username
                            mainHandler.post {
                                BotBridgeCoordinator.uiListener?.onLoggedIn(username, userId)
                            }
                        }

                        fun onError(error: String) {
                            mainHandler.post {
                                BotBridgeCoordinator.uiListener?.onError(error)
                            }
                        }
                    }

                    bridge.callAttr(
                        "start_bot",
                        apiId,
                        apiHash,
                        phone,
                        password2FA,
                        filesDir.absolutePath,
                        callback
                    )

                } catch (e: Throwable) {
                    updateStatusAndNotification("Ошибка: ${e.message}", false)
                    mainHandler.post {
                        BotBridgeCoordinator.uiListener?.onError(e.message ?: "Unknown error")
                    }
                } finally {
                    synchronized(threadLock) {
                        if (activePythonThread === Thread.currentThread()) {
                            activePythonThread = null
                        }
                    }
                    scheduleRestartIfNeeded(startedAt)
                }
            }
        }
    }

    /**
     * The Python side only returns when it was stopped or hit an error. If the
     * user did not press "Стоп" and the bot had been running for a while (so it
     * is not a login/config error that would just fail again), start it again.
     */
    private fun scheduleRestartIfNeeded(startedAt: Long) {
        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_SERVICE_ENABLED, false)) return
        val ranFor = System.currentTimeMillis() - startedAt
        if (ranFor < MIN_RUNTIME_FOR_RESTART_MS) return
        mainHandler.postDelayed({
            if (getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).getBoolean(KEY_SERVICE_ENABLED, false)) {
                updateStatusAndNotification("Перезапуск юзербота...", true)
                startPythonBot()
            }
        }, RESTART_DELAY_MS)
    }

    private fun updateStatusAndNotification(status: String, isRunning: Boolean) {
        BotBridgeCoordinator.lastStatus = status
        BotBridgeCoordinator.isBotRunning = isRunning
        mainHandler.post {
            val manager = getSystemService(NotificationManager::class.java)
            manager?.notify(NOTIFICATION_ID, buildForegroundNotification(status))
            BotBridgeCoordinator.uiListener?.onStatusChanged(status, isRunning)
        }
    }

    fun acquireTemporaryWakeLock(timeoutMs: Long = 30000L) {
        try {
            val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = powerManager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "Spambuster::TempWakeLock"
            ).apply {
                setReferenceCounted(false)
                acquire(timeoutMs)
            }
        } catch (e: Exception) {
            // ignore
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Spambuster 24/7 Service",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Фоновая служба авто-рассылки и антиспама"
                setShowBadge(false)
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager?.createNotificationChannel(channel)
        }
    }

    private fun buildForegroundNotification(statusText: String): Notification {
        val openAppIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pendingOpenApp = PendingIntent.getActivity(
            this, 0, openAppIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val stopIntent = Intent(this, SpambusterService::class.java).apply {
            action = ACTION_STOP
        }
        val pendingStop = PendingIntent.getService(
            this, 1, stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Spambuster (24/7)")
            .setContentText(statusText)
            .setSmallIcon(android.R.drawable.ic_popup_sync)
            .setOngoing(true)
            .setContentIntent(pendingOpenApp)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Остановить", pendingStop)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun stopForegroundService() {
        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().putBoolean(KEY_SERVICE_ENABLED, false).apply()

        stopHeartbeat()
        BotBridgeCoordinator.cancelWait()

        synchronized(threadLock) {
            thread {
                try {
                    if (Python.isStarted()) {
                        val py = Python.getInstance()
                        val bridge = py.getModule("bot_bridge")
                        bridge.callAttr("stop_bot")
                    }
                } catch (e: Throwable) {
                    // ignore shutdown exceptions
                }
            }
        }

        BotBridgeCoordinator.isBotRunning = false
        BotBridgeCoordinator.lastStatus = "Остановлен"
        mainHandler.post {
            BotBridgeCoordinator.uiListener?.onStatusChanged("Остановлен", false)
        }

        wakeLock?.let {
            if (it.isHeld) it.release()
        }
        releaseRuntimeWakeLock()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        // Deliberately not calling stopForegroundService() here.
        // onDestroy() also runs when the OEM or the system kills the process for
        // memory. Clearing KEY_SERVICE_ENABLED there meant the service could
        // never come back after the very kills we need it to survive.
        stopHeartbeat()
        releaseRuntimeWakeLock()
        wakeLock?.let { if (it.isHeld) it.release() }
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
