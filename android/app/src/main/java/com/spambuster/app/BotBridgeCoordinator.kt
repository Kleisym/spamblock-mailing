package com.spambuster.app

import java.util.concurrent.CountDownLatch

object BotBridgeCoordinator {

    interface UiListener {
        fun onCodeRequested()
        fun onPasswordRequested()
        fun onStatusChanged(status: String, isRunning: Boolean)
        fun onLoggedIn(username: String, userId: Long)
        fun onError(error: String)
    }

    var uiListener: UiListener? = null
    var lastStatus: String = "Остановлен"
    var isBotRunning: Boolean = false
    var loggedInUser: String = ""

    @Volatile
    var isWaitingForCode: Boolean = false

    @Volatile
    var isWaitingForPassword: Boolean = false

    private var codeLatch: CountDownLatch? = null
    private var passwordLatch: CountDownLatch? = null

    @Volatile
    private var enteredCode: String = ""

    @Volatile
    private var enteredPassword: String = ""

    fun prepareForCode(): String {
        isWaitingForCode = true
        codeLatch = CountDownLatch(1)
        try {
            // Bounded wait. The Python interpreter is a single global lock: an
            // unbounded await here froze the whole bot if the user backgrounded
            // the app or missed the dialog, and nothing ever recovered.
            codeLatch?.await(AUTH_TIMEOUT_MINUTES, java.util.concurrent.TimeUnit.MINUTES)
        } catch (e: InterruptedException) {
            return ""
        } finally {
            isWaitingForCode = false
        }
        return enteredCode
    }

    fun submitCode(code: String) {
        enteredCode = code.trim()
        isWaitingForCode = false
        codeLatch?.countDown()
    }

    fun prepareForPassword(): String {
        isWaitingForPassword = true
        passwordLatch = CountDownLatch(1)
        try {
            passwordLatch?.await(AUTH_TIMEOUT_MINUTES, java.util.concurrent.TimeUnit.MINUTES)
        } catch (e: InterruptedException) {
            return ""
        } finally {
            isWaitingForPassword = false
        }
        return enteredPassword
    }

    fun submitPassword(password: String) {
        enteredPassword = password.trim()
        isWaitingForPassword = false
        passwordLatch?.countDown()
    }

    fun cancelWait() {
        enteredCode = ""
        enteredPassword = ""
        isWaitingForCode = false
        isWaitingForPassword = false
        codeLatch?.countDown()
        passwordLatch?.countDown()
    }

    private const val AUTH_TIMEOUT_MINUTES = 5L
}
