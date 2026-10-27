package <NAMESPACE>.service

import android.util.Log
import <NAMESPACE>.RpaApplication
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 四道门禁顺序固定（第 4.1 节）：总闸 → 后端就绪 → 前台归属 → 抢锁。
 * 忙时策略固定为"跳过本轮"，不与 waitAndRun 混用，避免某轮询器饿死其他轮询器。
 */
abstract class PollerBase(
    private val lock: DeviceLock,
    private val config: () -> RuntimeConfig,
    private val name: String,
    private val intervalSeconds: Long,
    private val initialDelaySeconds: Long = intervalSeconds / 2   // 错峰
) {
    private val scheduler = Executors.newSingleThreadScheduledExecutor()
    private val started = AtomicBoolean(false)
    private val cycleRunning = AtomicBoolean(false)
    private var lastSkipReason = ""

    open fun endpointsReady(): Boolean = true

    fun start() {
        if (started.getAndSet(true)) return
        scheduler.scheduleWithFixedDelay({ tickSafely() }, initialDelaySeconds, intervalSeconds, TimeUnit.SECONDS)
        EventLogger.info(tag(), "轮询已启动", "interval=${intervalSeconds}s, initialDelay=${initialDelaySeconds}s")
    }

    fun stop() {
        if (!started.getAndSet(false)) return
        scheduler.shutdownNow()
        EventLogger.warn(tag(), "轮询已停止")
    }

    private fun tag() = "Poller:$name"

    private fun tickSafely() {
        if (cycleRunning.getAndSet(true)) return
        try {
            val cfg = config()
            if (!cfg.enabled) { skip("总闸关闭（人工接管）"); return }
            if (!endpointsReady()) { skip("后端地址未配置"); return }
            val svc = RpaAccessibilityService.instance
            if (svc == null) { skip(RpaAccessibilityService.NOT_CONNECTED); return }
            if (RpaAccessibilityService.rpaAppVisible || svc.currentPackage == RpaApplication.instance.packageName) {
                skip("RPA App 在前台"); return
            }
            val acquired = lock.tryRun("poller:$name") { runOneCycle() }
            if (!acquired) skip("设备被占用 busyOwner=${lock.busyOwner}")
        } catch (e: Exception) {
            Log.e(tag(), "poll exception: ${e.message}", e)
            EventLogger.error(tag(), "轮询异常: ${e.message}", Log.getStackTraceString(e))
        } finally {
            cycleRunning.set(false)
        }
    }

    /** 跳过原因去重，否则 10s 间隔会把日志刷屏。 */
    private fun skip(reason: String) {
        if (reason == lastSkipReason) return
        lastSkipReason = reason
        EventLogger.debug(tag(), "本轮跳过", reason)
    }

    protected abstract fun runOneCycle()
}
