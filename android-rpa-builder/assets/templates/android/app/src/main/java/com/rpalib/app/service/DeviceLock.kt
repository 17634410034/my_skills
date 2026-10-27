package <NAMESPACE>.service

import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * 设备独占锁（第 2.5 节）。一台手机同一时刻只能有一条 UI 操作流。
 * owner 命名规范：<来源>:<标识>，如 task:<id>:<type> / poller:<name> / sync:<action>
 */
class DeviceLock {
    private val locked = AtomicBoolean(false)
    private val owner = AtomicReference("")

    val isBusy: Boolean get() = locked.get()
    val busyOwner: String get() = owner.get()

    /** 非阻塞：抢不到立即 false。轮询器与短动作专用。 */
    fun tryRun(owner: String, block: () -> Unit): Boolean {
        if (!locked.compareAndSet(false, true)) return false
        this.owner.set(owner)
        return try { block(); true } finally { this.owner.set(""); locked.set(false) }
    }

    /** 带等待：HTTP 任务队列专用。超时返回 false，并把当前 owner 交给调用方做错误消息。 */
    fun waitFor(owner: String, timeoutMs: Long = 60_000, pollMs: Long = 200, block: () -> Unit): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (locked.compareAndSet(false, true)) {
                this.owner.set(owner)
                return try { block(); true } finally { this.owner.set(""); locked.set(false) }
            }
            try { Thread.sleep(pollMs) } catch (e: InterruptedException) {
                Thread.currentThread().interrupt(); return false
            }
        }
        return false
    }
}
