package <NAMESPACE>

import android.app.Application
import android.content.Context
import android.util.Log
import <NAMESPACE>.service.EventLogger
import <NAMESPACE>.service.RuntimeService

class RpaApplication : Application() {
    companion object { @Volatile lateinit var instance: RpaApplication private set }
    override fun onCreate() {
        super.onCreate()
        instance = this
        EventLogger.init(applicationContext)   // 必须最先，后续失败要可查
    }
}

class BootReceiver : android.content.BroadcastReceiver() {
    override fun onReceive(context: Context, intent: android.content.Intent?) {
        if (intent?.action != android.content.Intent.ACTION_BOOT_COMPLETED) return
        // 仍需无障碍被人工开启过才能真正工作。
        // Android 15+ 可能禁止从 BOOT_COMPLETED 拉起部分类型的前台服务，失败必须可见。
        runCatching { RuntimeService.start(context, "boot") }.onFailure { e ->
            Log.w("BootReceiver", "开机自启失败: ${e.message}")
            RuntimeService.notifyManualStartRequired(context, "系统限制了开机自启，请手动打开 App")
        }
    }
}
