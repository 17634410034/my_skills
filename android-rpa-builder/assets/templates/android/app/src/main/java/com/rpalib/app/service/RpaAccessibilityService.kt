package <NAMESPACE>.service

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Path
import android.graphics.Rect
import android.hardware.HardwareBuffer
import android.os.Build
import android.os.Bundle
import java.util.concurrent.CountDownLatch
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * 目标 App 无关的动作原语层（第 2.3 节）。
 * 本文件内禁止出现任何目标 App 的 resource-id / Activity 类名 / 业务文案。
 */
class RpaAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "RpaA11y"

        /** 服务句柄交接：未连接时所有原语不可用，调用方必须显式检查（第 2.3 节约束 3）。 */
        @Volatile var instance: RpaAccessibilityService? = null
            private set

        /** RPA 自己的 App 是否在前台，由 Activity 的 onResume/onStop 设置，不依赖无障碍事件。 */
        @Volatile var rpaAppVisible: Boolean = false

        /**
         * 需在 onServiceConnected 里动态声明的窗口包名集合（第 2.3 节约束 1）。
         * 缺项表现为"同一函数同一机型时好时坏"。
         */
        private fun windowPackages(): Array<String> = arrayOf(
            "<TARGET_PKG>",                       // 目标 App
            "<SECONDARY_TARGET_PKG>",             // 同厂商的其他被控 App，可删
            "android",
            "com.android.intentresolver",         // 系统分享面板
            "com.android.systemui",
            "com.miui.securitycenter"             // 厂商安全中心（引导页跳设置时需要）
        )

        /** 纯判定门：追加文本后校验原内容与追加内容都还在（可单测，第 3.8 节）。 */
        internal fun isPreservedAppendVisible(before: String, appended: String, current: String): Boolean {
            if (before.isBlank() || appended.isEmpty()) return false
            return current.length >= before.length + appended.length &&
                current.startsWith(before) && current.endsWith(appended)
        }

        /** 统一的"句柄未就绪"错误消息。 */
        const val NOT_CONNECTED = "无障碍服务未连接，请在系统设置中开启后重试"
    }

    // ---------------- 生命周期 ----------------

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        serviceInfo?.packageNames = windowPackages()
        // 刻意不设置任何"隐藏软键盘/保持触摸模式"相关 flag：
        // 部分 ROM 会把 EditText 从控件树整体移除（非仅视觉隐藏），导致按 id 找不到输入框。
        Log.i(TAG, "无障碍服务已连接，窗口白名单=${windowPackages().joinToString(",")}")
    }

    /**
     * 最近一次 WINDOW_STATE_CHANGED 的包名/类名，**仅供日志与诊断**。
     * 服务重连后它可能长期停在旧值：拿它判断"谁在前台"会让轮询器空转、返回键失效
     * （真实项目各踩一次，见 references/pitfalls.md P6）。前台归属一律走 [isSelfForeground]。
     */
    @Volatile var lastEventPackage: String = "" ; private set
    @Volatile var lastEventClassName: String = "" ; private set

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        event ?: return
        if (event.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        (event.packageName?.toString() ?: return).let { lastEventPackage = it }
        event.className?.toString()?.takeIf { it.contains(".") }?.let { lastEventClassName = it }
    }

    override fun onInterrupt() { Log.w(TAG, "无障碍服务被中断") }

    override fun onDestroy() {
        super.onDestroy(); instance = null; Log.i(TAG, "无障碍服务已销毁")
    }

    // ---------------- 查找 ----------------

    fun getRootNode(): AccessibilityNodeInfo? = try { rootInActiveWindow } catch (e: Exception) {
        Log.e(TAG, "rootInActiveWindow 失败: ${e.message}"); null
    }

    // ---------------- 前台归属（实时，不看缓存） ----------------

    /** 实时前台包名，以活动窗口根节点为准；读不到返回 null。 */
    fun livePackageName(): String? = liveRootField { it.packageName?.toString() }

    fun liveClassName(): String? = liveRootField { it.className?.toString() }

    private fun liveRootField(read: (AccessibilityNodeInfo) -> String?): String? = try {
        getRootNode()?.let(read)
    } catch (e: Exception) {
        Log.w(TAG, "读取实时根节点失败: ${e.message}"); null
    }

    /** RPA 自己是否在前台：Activity 可见标志 + 实时根节点双信号。 */
    fun isSelfForeground(): Boolean =
        rpaAppVisible || livePackageName() == packageName

    /** 按 id 查找：活动窗口未命中则自动跨窗口兜底（第 3.3 节第 1 条）。 */
    fun findById(resourceId: String): List<AccessibilityNodeInfo> {
        val main = getRootNode()?.findAccessibilityNodeInfosByViewId(resourceId) ?: emptyList()
        if (main.isNotEmpty()) return main
        return findByIdInWindows(resourceId)
    }

    fun findByIdInWindows(resourceId: String): List<AccessibilityNodeInfo> {
        val out = mutableListOf<AccessibilityNodeInfo>()
        try {
            windows.orEmpty().forEach { w -> w.root?.findAccessibilityNodeInfosByViewId(resourceId)?.let { out.addAll(it) } }
        } catch (e: Exception) { Log.w(TAG, "跨窗口搜索 ID 失败: ${e.message}") }
        return out
    }

    fun findByText(text: String): List<AccessibilityNodeInfo> =
        getRootNode()?.findAccessibilityNodeInfosByText(text) ?: emptyList()

    /** findByText 是子串/label 匹配，精确匹配需后过滤。 */
    fun findByExactText(text: String): AccessibilityNodeInfo? =
        findByText(text).firstOrNull { it.text?.toString() == text }

    /**
     * 递归 DFS 查找。**必须带上界**：未知 view-id 的试探可能把 Binder 调用卡住数分钟（第 3.3 节第 3 条）。
     */
    fun findNode(
        root: AccessibilityNodeInfo? = getRootNode(),
        maxNodes: Int = 512,
        maxDepth: Int = 32,
        predicate: (AccessibilityNodeInfo) -> Boolean
    ): AccessibilityNodeInfo? {
        val node = root ?: return null
        var visited = 0
        fun dfs(cur: AccessibilityNodeInfo, depth: Int): AccessibilityNodeInfo? {
            if (visited++ > maxNodes || depth > maxDepth) return null
            if (predicate(cur)) return cur
            for (i in 0 until cur.childCount) {
                val child = cur.getChild(i) ?: continue
                val hit = dfs(child, depth + 1)
                if (hit != null) return hit
            }
            return null
        }
        return dfs(node, 0)
    }

    fun findAllNodes(
        root: AccessibilityNodeInfo? = getRootNode(),
        maxNodes: Int = 2048,
        maxDepth: Int = 32,
        predicate: (AccessibilityNodeInfo) -> Boolean
    ): List<AccessibilityNodeInfo> {
        val node = root ?: return emptyList()
        val out = mutableListOf<AccessibilityNodeInfo>()
        var visited = 0
        fun dfs(cur: AccessibilityNodeInfo, depth: Int) {
            if (visited++ > maxNodes || depth > maxDepth) return
            if (predicate(cur)) out.add(cur)
            for (i in 0 until cur.childCount) { val c = cur.getChild(i) ?: continue; dfs(c, depth + 1) }
        }
        dfs(node, 0); return out
    }

    /** 跨窗口版 findAllNodes；windows 为空时退回活动窗口根。 */
    fun findAllNodesInWindows(
        maxNodes: Int = 2048,
        maxDepth: Int = 32,
        predicate: (AccessibilityNodeInfo) -> Boolean
    ): List<AccessibilityNodeInfo> {
        val out = mutableListOf<AccessibilityNodeInfo>()
        try {
            val ws = windows.orEmpty()
            if (ws.isEmpty()) return findAllNodes(getRootNode(), maxNodes, maxDepth, predicate)
            ws.forEach { w -> out.addAll(findAllNodes(w.root, maxNodes, maxDepth, predicate)) }
        } catch (e: Exception) { Log.w(TAG, "跨窗口遍历失败: ${e.message}") }
        return out
    }

    fun boundsCenter(node: AccessibilityNodeInfo): Pair<Float, Float> {
        val rect = Rect(); node.getBoundsInScreen(rect); return rect.centerX().toFloat() to rect.centerY().toFloat()
    }

    // ---------------- 点击 / 手势（第 3.1 节阶梯） ----------------

    fun clickNode(node: AccessibilityNodeInfo, allowCoordinateFallback: Boolean = true): Boolean {
        if (node.isClickable) return node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        var parent = node.parent
        while (parent != null) {
            if (parent.isClickable) return parent.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            parent = parent.parent
        }
        if (!allowCoordinateFallback) return false
        val (x, y) = boundsCenter(node)
        return clickByCoordinate(x, y)
    }

    fun clickByCoordinate(x: Float, y: Float, duration: Long = 100): Boolean {
        val path = Path().apply { moveTo(x, y) }
        return dispatchGesture(
            GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(path, 0, duration)).build(),
            null, null
        )
    }

    fun longClickNode(node: AccessibilityNodeInfo): Boolean {
        if (node.isLongClickable) return node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
        val (x, y) = boundsCenter(node); return longPressByCoordinate(x, y)
    }

    fun longPressByCoordinate(x: Float, y: Float, duration: Long = 1000): Boolean {
        val path = Path().apply { moveTo(x, y) }
        return dispatchGesture(
            GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(path, 0, duration)).build(),
            null, null
        )
    }

    fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, duration: Long = 500): Boolean {
        val path = Path().apply { moveTo(x1, y1); lineTo(x2, y2) }
        return dispatchGesture(
            GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(path, 0, duration)).build(),
            null, null
        )
    }

    fun scrollForward(node: AccessibilityNodeInfo): Boolean =
        node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)

    fun scrollBackward(node: AccessibilityNodeInfo): Boolean =
        node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)

    // ---------------- 文本输入（中文必须走这里，禁用 adb input text） ----------------

    /** 首选 ACTION_SET_TEXT（UTF-8 直传）；失败退回剪贴板全选粘贴。 */
    fun inputText(node: AccessibilityNodeInfo, text: String): Boolean {
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        if (node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) return true
        return try {
            clip(text, "rpa_input")
            node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            node.performAction(0x20000)                          // ACTION_SELECT_ALL
            node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
        } catch (e: Exception) { Log.e(TAG, "inputText 失败: ${e.message}"); false }
    }

    /** 点击后只出现键盘、无障碍树里没有 EditText 的场景（如某些搜索框）。 */
    fun pasteText(text: String): Boolean {
        return try {
            clip(text, "rpa_paste")
            Thread.sleep(200)
            val root = rootInActiveWindow ?: return false
            val target = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
                ?: root.findFocus(AccessibilityNodeInfo.FOCUS_ACCESSIBILITY) ?: root
            target.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            Thread.sleep(100)
            target.performAction(0x20000)
            Thread.sleep(50)
            if (!target.performAction(AccessibilityNodeInfo.ACTION_PASTE)) {
                val args = Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
                }
                target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            }
            true
        } catch (e: Exception) {
            Log.e(TAG, "pasteText 失败: ${e.message}")
            false
        }
    }

    private fun clip(text: String, label: String) {
        (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
            .setPrimaryClip(ClipData.newPlainText(label, text))
    }

    /**
     * 保留原内容与原 span 的追加写入。
     * 直接把旧文本当 String 拼会丢掉目标 App 的内部 span（提及/表情/富文本），
     * 所以要把原 CharSequence 拷进 SpannableStringBuilder 再一次 ACTION_SET_TEXT。
     */
    fun appendTextPreserving(node: AccessibilityNodeInfo, append: String, timeoutMs: Long = 1200): Boolean {
        val before = node.text ?: android.text.SpannableString("")
        if (before.isBlank() || append.isEmpty()) return false
        val builder = android.text.SpannableStringBuilder(before)
        builder.append(append)
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, builder)
        }
        if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) return false
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            Thread.sleep(40)
            node.refresh()
            val current = node.text?.toString() ?: ""
            if (isPreservedAppendVisible(before.toString(), append, current)) return true
        }
        return false
    }

    // ---------------- 全局键与返回防护（第 3.6 节） ----------------

    fun pressBackRaw(): Boolean = performGlobalAction(GLOBAL_ACTION_BACK)
    fun pressHome(): Boolean = performGlobalAction(GLOBAL_ACTION_HOME)
    fun pressRecents(): Boolean = performGlobalAction(GLOBAL_ACTION_RECENTS)
    fun openNotifications(): Boolean = performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)

    /**
     * RPA 自己在前台时拒绝按返回（Activity 可见标志 + 实时根节点）。
     * 没有这层保护，自动化会把手机按回桌面。
     */
    fun safePressBack(): Boolean {
        if (isSelfForeground()) { Log.w(TAG, "RPA App 在前台，拒绝返回键"); return false }
        return performGlobalAction(GLOBAL_ACTION_BACK)
    }

    /** 目标 App 是否在前台（实时根节点；读不到即按"否"处理，失败要闭不回放）。 */
    fun isTargetForeground(): Boolean = livePackageName() == "<TARGET_PKG>"

    /** 实时根包名校验，防止把别的 App 的文本当目标 App 命中。 */
    fun hasTargetAccessibilityRoot(): Boolean = isTargetForeground()

    // ---------------- 等待（第 3.2 节） ----------------

    fun waitForNode(timeoutMs: Long = 10_000, intervalMs: Long = 500, predicate: (AccessibilityNodeInfo) -> Boolean): AccessibilityNodeInfo? {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            findNode(predicate = predicate)?.let { return it }
            Thread.sleep(intervalMs)
        }
        Log.w(TAG, "等待控件超时 ${timeoutMs}ms")
        return null
    }

    fun waitAndClickText(text: String, timeoutMs: Long = 10_000): Boolean =
        waitForNode(timeoutMs) { it.text?.toString() == text }?.let { clickNode(it) } ?: false

    fun waitAndClickId(resourceId: String, timeoutMs: Long = 10_000): Boolean =
        waitForNode(timeoutMs) { it.viewIdResourceName == resourceId }?.let { clickNode(it) } ?: false

    fun waitUntil(timeoutMs: Long, intervalMs: Long = 250, predicate: () -> Boolean): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (predicate()) return true
            Thread.sleep(intervalMs)
        }
        return false
    }

    // ---------------- 截图（第 2.3 节，Android 11+） ----------------

    fun captureScreenBitmap(timeoutMs: Long = 1200): android.graphics.Bitmap? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        val latch = CountDownLatch(1)
        val executor = Executors.newSingleThreadExecutor()
        var bitmap: android.graphics.Bitmap? = null
        try {
            takeScreenshot(Display.DEFAULT_DISPLAY, executor, object : TakeScreenshotCallback {
                override fun onSuccess(screenshot: ScreenshotResult) {
                    try {
                        val hw = android.graphics.Bitmap.wrapHardwareBuffer(screenshot.hardwareBuffer, screenshot.colorSpace)
                        bitmap = hw?.copy(android.graphics.Bitmap.Config.ARGB_8888, false)
                        hw?.recycle(); screenshot.hardwareBuffer.close()
                    } catch (e: Exception) { Log.w(TAG, "截图转 Bitmap 失败: ${e.message}") }
                    finally { latch.countDown() }
                }
                override fun onFailure(errorCode: Int) {
                    Log.w(TAG, "截图失败 errorCode=$errorCode"); latch.countDown()
                }
            })
            latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        } catch (e: Exception) { Log.w(TAG, "截图异常: ${e.message}") }
        finally { executor.shutdown() }
        return bitmap
    }

    // ---------------- 控件树导出（/api/dump_ui 的实现） ----------------

    /**
     * 输出文本树：depth + class + resource-id + text + contentDescription +
     * clickable/enabled/visibleToUser + bounds。第 3.7 节的 ID 采集与第 5 章的适配流程都吃这个格式。
     */
    fun dumpUiTree(maxNodes: Int = 8000, maxDepth: Int = 40): String {
        val sb = StringBuilder()
        val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.CHINA)
        sb.append("capturedAt=").append(fmt.format(System.currentTimeMillis())).append('\n')
        sb.append("pkg=").append(livePackageName() ?: "-")
            .append(" lastEventPkg=").append(lastEventPackage)
            .append(" activity=").append(liveClassName() ?: lastEventClassName).append('\n')
        var visited = 0
        fun walk(node: AccessibilityNodeInfo?, depth: Int) {
            if (node == null || visited++ > maxNodes || depth > maxDepth) return
            val rect = Rect(); node.getBoundsInScreen(rect)
            sb.append("  ".repeat(depth))
                .append(node.className).append(" | id=").append(node.viewIdResourceName ?: "-")
                .append(" | text=").append(node.text?.toString()?.replace("\n", "\\n") ?: "-")
                .append(" | desc=").append(node.contentDescription?.toString()?.replace("\n", "\\n") ?: "-")
                .append(" | clickable=").append(node.isClickable)
                .append(" enabled=").append(node.isEnabled)
                .append(" visible=").append(node.isVisibleToUser)
                .append(" bounds=").append(rect.toShortString()).append('\n')
            for (i in 0 until node.childCount) walk(node.getChild(i), depth + 1)
        }
        walk(getRootNode(), 0)
        return sb.toString()
    }
}
