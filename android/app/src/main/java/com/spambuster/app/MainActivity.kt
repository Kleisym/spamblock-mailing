package com.spambuster.app

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.spambuster.app.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity(), BotBridgeCoordinator.UiListener {

    private lateinit var binding: ActivityMainBinding
    private lateinit var prefs: SharedPreferences

    private var codeDialog: AlertDialog? = null
    private var passwordDialog: AlertDialog? = null

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted ->
        if (!isGranted) {
            Toast.makeText(this, "Уведомления нужны для фоновой работы 24/7", Toast.LENGTH_LONG).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = getSharedPreferences(SpambusterService.PREFS_NAME, Context.MODE_PRIVATE)

        loadSavedData()
        requestNotificationPermission()
        setupListeners()
        updateStatusUi()
    }

    override fun onResume() {
        super.onResume()
        BotBridgeCoordinator.uiListener = this
        updateStatusUi()

        if (BotBridgeCoordinator.isWaitingForCode) {
            showCodeInputDialog()
        }
        if (BotBridgeCoordinator.isWaitingForPassword) {
            showPasswordInputDialog()
        }
    }

    override fun onPause() {
        super.onPause()
        if (BotBridgeCoordinator.uiListener === this) {
            BotBridgeCoordinator.uiListener = null
        }
    }

    private fun loadSavedData() {
        binding.etApiId.setText(prefs.getString("api_id", ""))
        binding.etApiHash.setText(prefs.getString("api_hash", ""))
        binding.etPhone.setText(prefs.getString("phone", ""))
        binding.etPassword2FA.setText(prefs.getString("password_2fa", ""))
    }

    private fun saveData() {
        prefs.edit()
            .putString("api_id", binding.etApiId.text?.toString()?.trim() ?: "")
            .putString("api_hash", binding.etApiHash.text?.toString()?.trim() ?: "")
            .putString("phone", binding.etPhone.text?.toString()?.trim() ?: "")
            .putString("password_2fa", binding.etPassword2FA.text?.toString()?.trim() ?: "")
            .apply()
    }

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }

    private fun setupListeners() {
        binding.btnStart.setOnClickListener {
            val apiId = binding.etApiId.text?.toString()?.trim() ?: ""
            val apiHash = binding.etApiHash.text?.toString()?.trim() ?: ""
            val phone = binding.etPhone.text?.toString()?.trim() ?: ""

            if (apiId.isEmpty() || apiHash.isEmpty()) {
                Toast.makeText(this, "Пожалуйста, введите API ID и API Hash", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            if (phone.isEmpty()) {
                Toast.makeText(this, "Пожалуйста, введите номер телефона", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            saveData()

            val serviceIntent = Intent(this, SpambusterService::class.java)
            ContextCompat.startForegroundService(this, serviceIntent)

            prefs.edit().putBoolean(SpambusterService.KEY_SERVICE_ENABLED, true).apply()
            BotBridgeCoordinator.lastStatus = "Запуск юзербота..."
            BotBridgeCoordinator.isBotRunning = true
            updateStatusUi()
            Toast.makeText(this, "🚀 Запуск Spambuster...", Toast.LENGTH_SHORT).show()
        }

        binding.btnStop.setOnClickListener {
            val stopIntent = Intent(this, SpambusterService::class.java).apply {
                action = SpambusterService.ACTION_STOP
            }
            startService(stopIntent)

            prefs.edit().putBoolean(SpambusterService.KEY_SERVICE_ENABLED, false).apply()
            BotBridgeCoordinator.lastStatus = "Остановлен"
            BotBridgeCoordinator.isBotRunning = false
            updateStatusUi()
            Toast.makeText(this, "⏹ Фоновая работа остановлена", Toast.LENGTH_SHORT).show()
        }

        binding.btnBattery.setOnClickListener {
            requestIgnoreBatteryOptimization()
        }
    }

    private fun requestIgnoreBatteryOptimization() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
            val packageName = packageName
            if (!powerManager.isIgnoringBatteryOptimizations(packageName)) {
                val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                    data = Uri.parse("package:$packageName")
                }
                try {
                    startActivity(intent)
                } catch (e: Exception) {
                    val fallbackIntent = Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
                    startActivity(fallbackIntent)
                }
            } else {
                Toast.makeText(this, "Оптимизация батареи уже отключена для Spambuster", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun updateStatusUi() {
        val isRunning = BotBridgeCoordinator.isBotRunning
        val statusText = BotBridgeCoordinator.lastStatus

        binding.tvStatus.text = "Статус: $statusText"
        if (isRunning) {
            binding.tvStatus.setTextColor(ContextCompat.getColor(this, R.color.accent_green))
            binding.viewStatusDot.setBackgroundResource(R.drawable.status_dot_green)
        } else {
            binding.tvStatus.setTextColor(ContextCompat.getColor(this, R.color.white))
            binding.viewStatusDot.setBackgroundResource(R.drawable.status_dot_red)
        }
    }

    private fun showCodeInputDialog() {
        if (isFinishing || isDestroyed) return
        if (codeDialog?.isShowing == true) return

        val input = EditText(this).apply {
            hint = "12345"
            inputType = InputType.TYPE_CLASS_NUMBER
            setSingleLine(true)
        }

        val container = FrameLayout(this).apply {
            val pad = (20 * resources.displayMetrics.density).toInt()
            setPadding(pad, pad / 2, pad, pad / 2)
            addView(input)
        }

        codeDialog = AlertDialog.Builder(this)
            .setTitle("Код подтверждения Telegram")
            .setMessage("Введите код, отправленный в официальный Telegram чат:")
            .setView(container)
            .setCancelable(false)
            .setPositiveButton("Подтвердить") { _, _ ->
                val code = input.text.toString().trim()
                BotBridgeCoordinator.submitCode(code)
            }
            .setNegativeButton("Отмена") { _, _ ->
                BotBridgeCoordinator.cancelWait()
            }
            .create()

        codeDialog?.show()
    }

    private fun showPasswordInputDialog() {
        if (isFinishing || isDestroyed) return
        if (passwordDialog?.isShowing == true) return

        val input = EditText(this).apply {
            hint = "Пароль 2FA"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setSingleLine(true)
        }

        val container = FrameLayout(this).apply {
            val pad = (20 * resources.displayMetrics.density).toInt()
            setPadding(pad, pad / 2, pad, pad / 2)
            addView(input)
        }

        passwordDialog = AlertDialog.Builder(this)
            .setTitle("Двухфакторная защита (2FA)")
            .setMessage("На аккаунте включен 2FA. Введите облачный пароль:")
            .setView(container)
            .setCancelable(false)
            .setPositiveButton("Войти") { _, _ ->
                val pwd = input.text.toString().trim()
                BotBridgeCoordinator.submitPassword(pwd)
            }
            .setNegativeButton("Отмена") { _, _ ->
                BotBridgeCoordinator.cancelWait()
            }
            .create()

        passwordDialog?.show()
    }

    // BotBridgeCoordinator.UiListener Callbacks
    override fun onCodeRequested() {
        runOnUiThread {
            showCodeInputDialog()
        }
    }

    override fun onPasswordRequested() {
        runOnUiThread {
            showPasswordInputDialog()
        }
    }

    override fun onStatusChanged(status: String, isRunning: Boolean) {
        runOnUiThread {
            updateStatusUi()
        }
    }

    override fun onLoggedIn(username: String, userId: Long) {
        runOnUiThread {
            Toast.makeText(this, "✅ Успешный вход в аккаунт $username (ID: $userId)", Toast.LENGTH_LONG).show()
            updateStatusUi()
        }
    }

    override fun onError(error: String) {
        runOnUiThread {
            Toast.makeText(this, "❌ Ошибка: $error", Toast.LENGTH_LONG).show()
            updateStatusUi()
        }
    }
}
