package <NAMESPACE>.activity

import android.Manifest
import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.app.NotificationManagerCompat
import <NAMESPACE>.BuildConfig
import <NAMESPACE>.service.PrefsConfigStore
import <NAMESPACE>.service.RpaAccessibilityService
import <NAMESPACE>.service.RuntimeService

/**
 * 引导与体检页。它不是业务界面，只负责三件事：
 * 1) 拉起运行时（自愈三源之一：onCreate/onResume 检测未运行即拉起）；
 * 2) 逐项显示权限状态（能检测的检测，检测不了的跳系统页）；
 * 3) 提供取证入口（导出当前控件树）。
 *
 * 安全：不要把设备令牌回显到任何 TextView/EditText，屏幕分享即泄密。
 */
class MainActivity : Activity() {

    private val handler = Handler(Looper.getMainLooper())
    private lateinit var statusView: TextView
    private lateinit var permissionView: TextView
    private val guide by lazy { PermissionGuide(this) }
    private val configStore by lazy { PrefsConfigStore(this) }

    private val refresh = object : Runnable {
        override fun run() {
            render()
            handler.postDelayed(this, 2000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildRoot())
        requestNotificationPermissionIfNeeded()
        RuntimeService.start(this, "main_oncreate")
    }

    override fun onResume() {
        super.onResume()
        RpaAccessibilityService.rpaAppVisible = true
        if (!RuntimeService.isRunning) RuntimeService.start(this, "main_onresume")
        render()
        handler.post(refresh)
    }

    override fun onStop() {
        RpaAccessibilityService.rpaAppVisible = false
        handler.removeCallbacks(refresh)
        super.onStop()
    }

    override fun onDestroy() {
        handler.removeCallbacks(refresh)
        super.onDestroy()
    }

    private fun buildRoot(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(28), dp(20), dp(20))
        }
        root.addView(TextView(this).apply {
            text = "RPA Runner"
            textSize = 22f
        })
        statusView = TextView(this).apply { textSize = 14f; setPadding(0, dp(16), 0, dp(8)) }
        permissionView = TextView(this).apply { textSize = 14f; setPadding(0, 0, 0, dp(12)) }
        root.addView(statusView)
        root.addView(permissionView)
        root.addView(button("开启无障碍服务") { guide.openAccessibilitySettings() })
        root.addView(button("悬浮窗权限") { guide.openOverlaySettings() })
        root.addView(button("电池优化白名单") { guide.openBatterySettings() })
        root.addView(button("厂商自启动 / 后台弹出界面") { guide.openVendorPermissionPage() })
        root.addView(button("导出当前控件树到剪贴板") { dumpToClipboard() })
        root.addView(button("打开应用详情") {
            startActivity(
                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package=${BuildConfig.APPLICATION_ID}"))
            )
        })
        return ScrollView(this).apply { addView(root) }
    }

    private fun button(label: String, onClick: () -> Unit): Button =
        Button(this).apply { text = label; setOnClickListener { onClick() } }

    private fun render() {
        val svc = RpaAccessibilityService.instance
        val cfg = configStore.getConfig()
        statusView.text = buildString {
            appendLine("运行时: ${if (RuntimeService.isRunning) "运行中" else "未运行"}")
            appendLine("无障碍: ${if (svc != null) "已连接" else "未连接"}")
            appendLine("端口: ${RuntimeService.PORT}")
            appendLine("模式: ${if (cfg.enabled) "AI 自动" else "人工接管"}")
            appendLine("当前页面: ${svc?.currentPackage.orEmpty()} / ${svc?.currentClassName.orEmpty()}")
            append("设备令牌: ${if (cfg.deviceToken.isNotBlank()) "已配置" else "未配置（仅回环可访问）"}")
        }
        permissionView.text = buildString {
            appendLine("悬浮窗: ${if (guide.overlayReady()) "OK" else "缺少"}")
            appendLine("电池白名单: ${if (guide.batteryIgnored()) "OK" else "缺少"}")
            val notifyOk = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                NotificationManagerCompat.from(this@MainActivity).areNotificationsEnabled()
            append("通知: ${if (notifyOk) "OK" else "缺少"}")
        }
    }

    private fun dumpToClipboard() {
        if (RuntimeService.instance?.isBusy() == true) {
            statusView.text = "设备忙，拒绝 dump（铁律：busy 期间不取证）"
            return
        }
        val text = RpaAccessibilityService.instance?.dumpUiTree(maxNodes = 400, maxDepth = 24)
            ?: RpaAccessibilityService.NOT_CONNECTED
        val cm = getSystemService(ClipboardManager::class.java)
        cm.setPrimaryClip(ClipData.newPlainText("rpa_dump_ui", text))
        statusView.text = "控件树已复制到剪贴板（${text.length} 字符）"
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFICATION)
        }
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    companion object { private const val REQ_NOTIFICATION = 101 }
}
