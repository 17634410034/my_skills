package <NAMESPACE>.activity

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings

/** 权限引导：能检测的自己检测，检测不了的跳到系统页并让运营点"我已开启"。 */
class PermissionGuide(private val context: Context) {

    companion object {
        val APP_ID: String get() = <NAMESPACE>.BuildConfig.APPLICATION_ID

        /** 品牌判定只用于决定是否弹厂商提示；注意 Redmi/PoCo 也是小米系。 */
        fun isMiuiLikeDevice(): Boolean {
            val marks = listOf("xiaomi", "redmi", "poco")
            val brand = Build.BRAND.lowercase()
            val maker = Build.MANUFACTURER.lowercase()
            return marks.any { brand.contains(it) || maker.contains(it) }
        }

        private val DEEP_LINKS: List<Pair<String, Intent.() -> Unit>> = listOf(
            "自启动管理" to {
                component = ComponentName("com.miui.securitycenter", "com.miui.permcenter.autostart.AutoStartManagementActivity")
            },
            "后台弹出界面" to {
                action = "miui.intent.action.APP_PERM_EDITOR"
                putExtra("extra_pkgname", APP_ID)
            },
            "应用权限" to {
                component = ComponentName("com.miui.securitycenter", "com.miui.permcenter.permissions.PermissionsEditorActivity")
            }
        )
    }

    fun accessibilityReady(): Boolean =
        <NAMESPACE>.service.RpaAccessibilityService.instance != null    // 唯一可靠检测方式

    fun openAccessibilitySettings() =
        context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))

    fun overlayReady(): Boolean = Settings.canDrawOverlays(context)

    fun openOverlaySettings() {
        val primary = Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package=$APP_ID"))
        if (!launch(primary)) launchAppDetails()
    }

    fun batteryIgnored(): Boolean {
        val pm = context.getSystemService(Context.POWER_SERVICE) as android.os.PowerManager
        return pm.isIgnoringBatteryOptimizations(APP_ID)
    }

    fun openBatterySettings() {
        val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package=$APP_ID"))
        if (!launch(intent)) launch(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
    }

    /** 逐个尝试深链；App 无法读取厂商开关状态，所以返回值只表示"跳成功"。 */
    fun openVendorPermissionPage(preferTitle: String? = null): Boolean {
        val ordered = if (preferTitle != null) DEEP_LINKS.sortedBy { if (it.first == preferTitle) 0 else 1 } else DEEP_LINKS
        for ((_, builder) in ordered) if (launch(Intent().apply(builder))) return true
        return launchAppDetails()
    }

    private fun launchAppDetails(): Boolean = launch(
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package=$APP_ID"))
    )

    private fun launch(intent: Intent): Boolean = try {
        context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); true
    } catch (e: Exception) { false }
}
