package com.spambuster.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlin.concurrent.thread

class SpambusterService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null
    private var pythonThread: Thread? = null
    private val mainHandler = Handler(Looper.getMainLooper())

    companion object {
        const val CHANNEL_ID = "spambuster_service_channel"
        const val NOTIFICATION_ID = 7771
        const val ACTION_STOP = "com.spambuster.app.ACTION_STOP"
        const val PREFS_NAME = "spambuster_prefs"
        const val KEY_SERVICE_ENABLED = "service_enabled"
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
        prefs.edit().putBoolean(KEY_SERVICE_ENABLED, true).apply()

        val notification = buildForegroundNotification("Инициализация юзербота...")
        startForeground(NOTIFICATION_ID, notification)

        startPythonBot()

        return START_STICKY
    }

    private fun startPythonBot() {
        if (pythonThread != null && pythonThread?.isAlive == true) {
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

        pythonThread = thread(name = "Spambuster-Python-Thread") {
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
            }
        }
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

        BotBridgeCoordinator.cancelWait()

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

        BotBridgeCoordinator.isBotRunning = false
        BotBridgeCoordinator.lastStatus = "Остановлен"
        mainHandler.post {
            BotBridgeCoordinator.uiListener?.onStatusChanged("Остановлен", false)
        }

        wakeLock?.let {
            if (it.isHeld) it.release()
        }
        stopForeground(true)
        stopSelf()
    }

    override fun onDestroy() {
        stopForegroundService()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
