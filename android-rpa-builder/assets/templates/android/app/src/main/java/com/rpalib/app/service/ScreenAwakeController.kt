package <NAMESPACE>.service

import android.annotation.SuppressLint
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.PowerManager
import android.util.Log

/**
 * 前台服务存活期间保持屏幕不熄。
 *
 * 不用 FLAG_KEEP_SCREEN_ON：前台窗口是目标 App 而不是本 App 的 Activity。
 * 这里用（已废弃但仍在工作的）显示级 wake lock，并把它隔离在单个类内。
 * 它不会唤醒或解锁已息屏设备，用户按电源键息屏会被尊重；重新亮屏后锁自动恢复。
 */
class ScreenAwakeController(context: Context) {

    companion object {
        private const val TAG = "ScreenAwake"
        private const val WAKE_LOCK_TAG_SUFFIX = ":rpa-screen-awake"
    }

    private val appContext = context.applicationContext
    private val powerManager = appContext.getSystemService(PowerManager::class.java)
    private val wakeLock = createWakeLock()
    private var receiverRegistered = false

    private val screenStateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.action) {
                Intent.ACTION_SCREEN_ON -> acquire("screen_on")
                Intent.ACTION_SCREEN_OFF -> release("screen_off")
            }
        }
    }

    fun start() {
        registerScreenStateReceiver()
        if (powerManager?.isInteractive == true) acquire("service_started")
        else Log.i(TAG, "屏幕当前已关闭，等待用户亮屏后启用常亮")
    }

    fun stop() { unregisterScreenStateReceiver(); release("service_stopped") }

    @Suppress("DEPRECATION")
    private fun createWakeLock(): PowerManager.WakeLock? = try {
        powerManager?.newWakeLock(
            PowerManager.SCREEN_DIM_WAKE_LOCK,
            appContext.packageName + WAKE_LOCK_TAG_SUFFIX
        )?.apply { setReferenceCounted(false) }
    } catch (e: RuntimeException) {
        Log.w(TAG, "创建屏幕常亮锁失败，RPA 将继续运行: ${e.message}", e); null
    }

    /** 跟随前台服务生命周期，刻意不设超时。 */
    @SuppressLint("WakelockTimeout")
    private fun acquire(reason: String) {
        val wl = wakeLock ?: return
        try {
            if (!wl.isHeld) { wl.acquire(); Log.i(TAG, "屏幕常亮已启用: reason=$reason") }
        } catch (e: RuntimeException) {
            Log.w(TAG, "启用屏幕常亮失败，RPA 将继续运行: ${e.message}", e)   // 保活失败不得阻断主链路
        }
    }

    private fun release(reason: String) {
        val wl = wakeLock ?: return
        try {
            if (wl.isHeld) { wl.release(); Log.i(TAG, "屏幕常亮已释放: reason=$reason") }
        } catch (e: RuntimeException) { Log.w(TAG, "释放屏幕常亮锁失败: ${e.message}", e) }
    }

    private fun registerScreenStateReceiver() {
        if (receiverRegistered) return
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON); addAction(Intent.ACTION_SCREEN_OFF)
        }
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
                appContext.registerReceiver(screenStateReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
            else @Suppress("DEPRECATION") appContext.registerReceiver(screenStateReceiver, filter)
            receiverRegistered = true
        } catch (e: RuntimeException) { Log.w(TAG, "注册屏幕状态监听失败: ${e.message}", e) }
    }

    private fun unregisterScreenStateReceiver() {
        if (!receiverRegistered) return
        try { appContext.unregisterReceiver(screenStateReceiver) }
        catch (e: RuntimeException) { Log.w(TAG, "注销失败: ${e.message}", e) }
        finally { receiverRegistered = false }
    }
}
