package <NAMESPACE>.service

import android.content.Intent
import <NAMESPACE>.RpaApplication
import <NAMESPACE>.utils.NodeFinder

/**
 * L3 领域层：唯一允许出现目标 App 页面知识的地方（页面 ID、导航链、文案阶梯、状态机）。
 *
 * 约定：
 * - 每个公开入口先过 gate()；失败返回可读消息，不抛异常。
 * - 判定与执行分层：能抽成纯函数的判定放 companion object internal fun，入参只允许
 *   String/Int/Boolean/List 等 JVM 类型，绝不传 AccessibilityNodeInfo（蓝图第 3.8 节）。
 * - 不做坐标导航；坐标只作为 clickNode 的最后兜底。
 */
class TargetOperator(
    private val lock: DeviceLock,
    private val config: ConfigStore
) {

    companion object {
        private const val TAG = "TargetOperator"
        private const val MAX_BACK_STEPS = 6

        /** 纯判定示例：动作文案命中（可单测，不碰 Android 类）。 */
        internal fun isActionLabel(text: String?, candidates: List<String>): Boolean =
            text != null && candidates.any { text.trim() == it.trim() }
    }

    /** 所有入口统一先调它；返回非 null 表示当前不可执行，直接作为失败原因。 */
    private fun gate(): String? = NodeFinder.notConnectedMessage()
        ?: if (!config.getConfig().enabled) "人工接管模式已开启，RPA 已暂停" else null

    fun openTargetApp(): Boolean {
        if (gate() != null) return false
        val ctx = RpaApplication.instance
        val intent = ctx.packageManager.getLaunchIntentForPackage(<NAMESPACE>.BuildConfig.TARGET_PKG)
            ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
        return try {
            ctx.startActivity(intent)
            true
        } catch (e: Exception) {
            EventLogger.warn(TAG, "启动目标 App 失败: ${e.message}")
            false
        }
    }

    /** 有界返回：最多 6 次，每次按完验一次页面；禁止无限连按（蓝图第 3.6 节）。 */
    fun backToHome(): Boolean {
        if (gate() != null) return false
        repeat(MAX_BACK_STEPS) {
            if (isHome()) return true
            if (!NodeFinder.pressBack()) return false
            Thread.sleep(400)
        }
        return isHome()
    }

    /**
     * 主页判定的最小实现：只校验实时根节点属于目标 App。
     * 适配阶段必须替换成"元素集合存在性 + 精确标签集合 + Activity 类名"三信号判定。
     */
    fun isHome(): Boolean {
        val svc = RpaAccessibilityService.instance ?: return false
        return svc.isTargetForeground() && !svc.liveClassName().isNullOrBlank()
    }

    /** 失败取证载荷：可见控件摘要写进日志。没有取证载荷的失败等于无法复现的失败。 */
    fun evidence(): String {
        val svc = RpaAccessibilityService.instance ?: return RpaAccessibilityService.NOT_CONNECTED
        return svc.dumpUiTree(maxNodes = 300, maxDepth = 20)
    }

    /** 任务处理：按需求实现；未实现的必须明确失败，不许静默成功。 */
    fun readMessages(req: TaskRequest): TaskResult = notImplemented(req, "readMessages")

    fun sendMessage(req: TaskRequest): TaskResult = notImplemented(req, "sendMessage")

    private fun notImplemented(req: TaskRequest, name: String): TaskResult {
        EventLogger.warn(TAG, "任务未实现: $name")
        return TaskResult(
            taskId = req.taskId,
            success = false,
            message = "任务未实现：请在 TargetOperator.$name 中实现（当前为脚手架）",
            status = TaskStatus.DONE
        )
    }
}
