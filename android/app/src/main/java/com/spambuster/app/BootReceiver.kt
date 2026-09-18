package com.spambuster.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat

class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (action == Intent.ACTION_BOOT_COMPLETED ||
            action == "android.intent.action.QUICKBOOT_POWERON" ||
            action == "com.htc.intent.action.QUICKBOOT_POWERON" ||
            action == Intent.ACTION_MY_PACKAGE_REPLACED
        ) {
            val prefs = context.getSharedPreferences(SpambusterService.PREFS_NAME, Context.MODE_PRIVATE)
            val isEnabled = prefs.getBoolean(SpambusterService.KEY_SERVICE_ENABLED, false)

            if (isEnabled) {
                val serviceIntent = Intent(context, SpambusterService::class.java)
                ContextCompat.startForegroundService(context, serviceIntent)
            }
        }
    }
}
