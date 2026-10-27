package <NAMESPACE>.utils

import android.view.accessibility.AccessibilityNodeInfo
import <NAMESPACE>.service.RpaAccessibilityService

/**
 * L3 领域层访问 L2 的唯一入口。
 * 服务未连接时 notConnectedMessage() 返回可读原因，Operator 必须在入口检查它。
 */
object NodeFinder {
    private val svc: RpaAccessibilityService? get() = RpaAccessibilityService.instance
    fun notConnectedMessage(): String? = if (svc == null) RpaAccessibilityService.NOT_CONNECTED else null

    fun byId(resourceId: String): List<AccessibilityNodeInfo> = svc?.findById(resourceId) ?: emptyList()
    fun firstById(resourceId: String): AccessibilityNodeInfo? = byId(resourceId).firstOrNull()
    fun byText(text: String): List<AccessibilityNodeInfo> = svc?.findByText(text) ?: emptyList()
    fun byExactText(text: String): AccessibilityNodeInfo? = svc?.findByExactText(text)
    fun click(node: AccessibilityNodeInfo, allowCoordinateFallback: Boolean = true): Boolean =
        svc?.clickNode(node, allowCoordinateFallback) ?: false
    fun input(node: AccessibilityNodeInfo, text: String): Boolean = svc?.inputText(node, text) ?: false
    fun pressBack(): Boolean = svc?.safePressBack() ?: false

    /** 标签阶梯：目标 App 各版本用词不同，按顺序找第一个命中的可点击祖先。 */
    fun clickFirstLabel(labels: List<String>, timeoutMsPerLabel: Long = 300): String? {
        for (label in labels) {
            val node = svc?.findByExactText(label) ?: continue
            val clickable = findClickableAncestor(node) ?: continue
            if (click(clickable, allowCoordinateFallback = false)) return label
        }
        return null
    }

    fun findClickableAncestor(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        var cur: AccessibilityNodeInfo? = node
        while (cur != null) { if (cur.isClickable) return cur; cur = cur.parent }
        return null
    }

    /** 候选校验：可见 + 可用，避免命中回收单元格或缓存节点（第 3.3 节第 4 条）。 */
    fun isUsable(node: AccessibilityNodeInfo): Boolean = node.isEnabled && node.isVisibleToUser
}
