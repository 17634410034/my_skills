package <NAMESPACE>.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject
import java.util.UUID
import fi.iki.elonen.NanoHTTPD.Method
import <NAMESPACE>.BuildConfig
import <NAMESPACE>.RpaApplication
import <NAMESPACE>.activity.MainActivity

/** 配置快照：总闸 enabled 是唯一的紧急停止按钮，优先级高于任何业务逻辑。 */
data class RuntimeConfig(
    val enabled: Boolean = true,
    val backendBaseUrl: String = "",
    val deviceToken: String = "",
    val extra: Map<String, String> = emptyMap()
) {
    /** 任何回显配置的 HTTP 响应/日志都必须走这个视图，密钥字段永不出网。 */
    fun redactedView(): Map<String, Any?> = mapOf(
        "enabled" to enabled,
        "backendBaseUrl" to backendBaseUrl,
        "deviceTokenSet" to deviceToken.isNotEmpty(),
        "extra" to extra
    )
}

/** 配置存储需自行实现（明文走 prefs、密钥走 EncryptedSharedPreferences，第 2.9 / 7.2 节）。 */
interface ConfigStore {
    fun getConfig(): RuntimeConfig
    fun update(patch: Map<String, Any?>): RuntimeConfig
}

class RuntimeService : Service() {

    companion object {
        private const val TAG = "RuntimeService"
        const val PORT: Int = BuildConfig.DEVICE_PORT
        private const val CHANNEL_ID = "rpa_runtime_server"
        private const val NOTIFICATION_ID = 9527

        @Volatile var isRunning: Boolean = false; private set
        @Volatile var instance: RuntimeService? = null; private set

        fun start(context: Context, reason: String) {
            val intent = Intent(context, RuntimeService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent)
            else context.startService(intent)
            Log.i(TAG, "请求启动运行时: reason=$reason")
        }

        /**
         * 开机自启失败时的兜底：发高优先级通知让人手动打开。
         * Android 15+ 对部分前台服务类型（含 dataSync）限制从 BOOT_COMPLETED 拉起；
         * 模板默认用 specialUse 规避 6 小时/日上限，但开机拉起仍可能被拒（见 references/pitfalls.md P3）。
         */
        fun notifyManualStartRequired(context: Context, reason: String) {
            try {
                val nm = context.getSystemService(NotificationManager::class.java) ?: return
                val channelId = "rpa_manual_start"
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    nm.createNotificationChannel(
                        NotificationChannel(channelId, "RPA 需要人工启动", NotificationManager.IMPORTANCE_HIGH)
                    )
                }
                val pi = PendingIntent.getActivity(
                    context, 1, Intent(context, MainActivity::class.java),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                )
                val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                    Notification.Builder(context, channelId)
                else @Suppress("DEPRECATION") Notification.Builder(context)
                nm.notify(
                    9528, builder.setContentTitle("RPA 未能自动启动")
                        .setContentText(reason)
                        .setSmallIcon(android.R.drawable.stat_notify_error)
                        .setContentIntent(pi)
                        .setAutoCancel(true)
                        .build()
                )
            } catch (e: Exception) {
                Log.w(TAG, "人工启动通知发送失败: ${e.message}")
            }
        }
    }

    private val lock = DeviceLock()
    private lateinit var configStore: ConfigStore
    private lateinit var targetOperator: TargetOperator          // L3，见第 6.2 节
    private lateinit var taskController: TaskController
    private lateinit var pollers: List<PollerBase>
    private lateinit var screenAwake: ScreenAwakeController
    private var httpServer: DeviceHttpServer? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        // 装配顺序固定（第 2.2 节）
        EventLogger.init(applicationContext)
        // 重启归因的最小证据：进程号 + 版本号，配合心跳日志才能判断"是不是被系统杀了"
        EventLogger.info(TAG, "进程启动", "pid=${android.os.Process.myPid()} version=${BuildConfig.VERSION_NAME}")
        configStore = PrefsConfigStore(applicationContext)
        if (configStore.getConfig().deviceToken.isBlank()) {
            EventLogger.warn(TAG, "设备令牌未配置：HTTP 将只允许 127.0.0.1 访问（仅本地调试）")
        }
        targetOperator = TargetOperator(lock, configStore)
        // 第二参是 TaskResultStore（不是 ConfigStore）：任务结果必须落盘，见蓝图第 7.4 项
        taskController = TaskController(lock, FileTaskResultStore(applicationContext), handlerRegistry(targetOperator, lock, configStore))
        pollers = buildPollers(lock, configStore, targetOperator, taskController)   // 你的业务轮询器
        createNotificationChannel()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, buildNotification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, buildNotification())
        }
        screenAwake = ScreenAwakeController(applicationContext)
        screenAwake.start()
        startRuntimeIfNeeded()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startRuntimeIfNeeded(); return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        if (::screenAwake.isInitialized) screenAwake.stop()
        try { stopRuntime() } finally { instance = null; super.onDestroy() }
    }

    /** 幂等：以 httpServer 引用非空为"已启动"标记；失败必须回到未启动态。 */
    @Synchronized
    private fun startRuntimeIfNeeded() {
        if (httpServer != null) return
        try {
            taskController.start()
            pollers.forEach { it.start() }
            httpServer = DeviceHttpServer(PORT, lock, configStore, taskController, targetOperator, pollers, this)
                .apply { start(NanoHTTPD.SOCKET_READ_TIMEOUT, false) }
            isRunning = true
            Log.i(TAG, "运行时已启动，端口=$PORT")
        } catch (e: Exception) {
            Log.e(TAG, "启动运行时失败: ${e.message}", e)
            isRunning = false
            stopSelf()
        }
    }

    @Synchronized
    private fun stopRuntime() {
        httpServer?.stop(); httpServer = null
        pollers.forEach { it.stop() }
        if (::taskController.isInitialized) taskController.stop()
        isRunning = false
    }

    /** 总闸切换：清待发队列，但保留业务现场供人工查看（第 4.4 节）。 */
    fun applyTakeoverMode(aiMode: Boolean) {
        if (aiMode) {
            EventLogger.info(TAG, "AI 接管模式已开启")
        } else {
            val cleared = taskController.clearPending()
            EventLogger.warn(TAG, "人工接管模式已开启", "清空待执行任务=$cleared，业务现场状态保留")
        }
    }

    fun isBusy(): Boolean = lock.isBusy

    fun statusSnapshot(): Map<String, Any?> {
        val svc = RpaAccessibilityService.instance
        return mapOf(
            "accessibilityEnabled" to (svc != null),
            "httpServerRunning" to isRunning,
            "currentApp" to (svc?.livePackageName() ?: svc?.lastEventPackage ?: ""),
            "currentPage" to (svc?.liveClassName() ?: svc?.lastEventClassName ?: ""),
            "taskQueueSize" to (if (::taskController.isInitialized) taskController.queueSize() else 0),
            "mode" to if (configStore.getConfig().enabled) "ai" else "manual",
            "versionName" to BuildConfig.VERSION_NAME,
            "devicePort" to PORT
        )
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "RPA 运行时", NotificationManager.IMPORTANCE_LOW)
        )
    }

    private fun buildNotification(): Notification {
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M)
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            else PendingIntent.FLAG_UPDATE_CURRENT
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            Notification.Builder(this, CHANNEL_ID) else @Suppress("DEPRECATION") Notification.Builder(this)
        return builder.setContentTitle("RPA 运行中")
            .setContentText("端口 $PORT")
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }
}
