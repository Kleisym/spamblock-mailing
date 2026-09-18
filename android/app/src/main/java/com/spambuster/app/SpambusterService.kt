package com.spambuster.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.*

class SpambusterService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null
    private val serviceScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var isRunning = false

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
        acquireWakeLock()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopForegroundService()
            return START_NOT_STICKY
        }

        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().putBoolean(KEY_SERVICE_ENABLED, true).apply()

        val notification = buildForegroundNotification()
        startForeground(NOTIFICATION_ID, notification)

        if (!isRunning) {
            isRunning = true
            startWorkerLoop()
        }

        return START_STICKY
    }

    private fun startWorkerLoop() {
        serviceScope.launch {
            while (isActive && isRunning) {
                try {
                    // Periodic worker tick / connection heartbeat
                    delay(10000)
                } catch (e: CancellationException) {
                    break
                } catch (e: Exception) {
                    delay(5000)
                }
            }
        }
    }

    private fun acquireWakeLock() {
        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "Spambuster::ForegroundWakeLock"
        ).apply {
            setReferenceCounted(false)
            acquire()
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

    private fun buildForegroundNotification(): Notification {
        val openAppIntent = Intent(this, MainActivity::class.java)
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
            .setContentTitle("Spambuster активен (24/7)")
            .setContentText("Рассылка и защита от спама работают в фоне")
            .setSmallIcon(android.R.drawable.ic_popup_sync)
            .setOngoing(true)
            .setContentIntent(pendingOpenApp)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Остановить", pendingStop)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun stopForegroundService() {
        isRunning = false
        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().putBoolean(KEY_SERVICE_ENABLED, false).apply()

        serviceScope.cancel()
        wakeLock?.let {
            if (it.isHeld) it.release()
        }
        stopForeground(true)
        stopSelf()
    }

    override fun onDestroy() {
        isRunning = false
        serviceScope.cancel()
        wakeLock?.let {
            if (it.isHeld) it.release()
        }
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
