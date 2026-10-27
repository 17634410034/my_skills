package <NAMESPACE>.service

import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

enum class TaskStatus { PENDING, RUNNING, DONE, LOST }        // 第 7.4 项：lost 必须显式存在

data class TaskRequest(
    val taskId: String,
    val taskType: TaskType,
    val params: Map<String, Any?> = emptyMap()
)

data class TaskResult(
    val taskId: String,
    val success: Boolean,
    val message: String = "",
    val data: Any? = null,
    val status: TaskStatus = TaskStatus.DONE
)

/** 结果需要落盘；接口便于替换成 Room。 */
interface TaskResultStore {
    fun save(result: TaskResult)
    fun load(): Map<String, TaskResult>
}

/**
 * 任务控制器：单条 daemon 线程串行消费队列，执行权由 DeviceLock 统一仲裁。
 * handler 用注册表而不是 when(taskType) 硬编码，新增任务类型不改控制器（第 4 章）。
 */
class TaskController(
    private val lock: DeviceLock,
    private val resultStore: TaskResultStore? = null,
    private val handlers: Map<TaskType, (TaskRequest) -> TaskResult>
) {
    companion object { private const val RESULT_LIMIT = 1000; private const val WAIT_TIMEOUT_MS = 60_000L }

    private val queue = ConcurrentLinkedQueue<TaskRequest>()
    private val running = AtomicBoolean(false)
    private val results = ConcurrentHashMap<String, TaskResult>()
    private val order = ConcurrentLinkedQueue<String>()

    init {
        // 重启后：把上次遗留的 PENDING/RUNNING 一律标 LOST（UI 动作不可回滚，绝不自动重放，第 4.2 节铁律）
        resultStore?.load()?.forEach { (id, r) ->
            val normalized = if (r.status == TaskStatus.PENDING || r.status == TaskStatus.RUNNING) {
                r.copy(success = false, message = "进程重启，任务状态未知", status = TaskStatus.LOST)
            } else r
            results[id] = normalized
            order.offer(id)
            if (normalized !== r) resultStore.save(normalized)
        }
    }

    fun start() {
        if (running.getAndSet(true)) return
        thread(name = "TaskExecutor", isDaemon = true) {
            while (running.get()) {
                val task = queue.poll()
                if (task != null) execute(task) else Thread.sleep(200)
            }
        }
    }

    fun stop() { running.set(false) }
    fun submit(task: TaskRequest): String { save(TaskResult(task.taskId, false, "已入队", null, TaskStatus.PENDING)); queue.offer(task); return task.taskId }
    fun result(taskId: String): TaskResult? = results[taskId]
    fun queueSize(): Int = queue.size
    fun clearPending(): Int { var n = 0; while (queue.poll() != null) n++; return n }

    private fun execute(task: TaskRequest) {
        val handler = handlers[task.taskType]
        if (handler == null) {
            save(TaskResult(task.taskId, false, "不支持的任务类型: ${task.taskType}"))
            return
        }
        save(TaskResult(task.taskId, false, "执行中", null, TaskStatus.RUNNING))
        var outcome: TaskResult? = null
        val acquired = lock.waitFor("task:${task.taskId}:${task.taskType}", WAIT_TIMEOUT_MS) {
            outcome = runCatching { handler(task) }
                .getOrElse { e -> TaskResult(task.taskId, false, "执行异常: ${e.message}") }
        }
        val result = when {
            !acquired -> TaskResult(task.taskId, false, "等待设备空闲超时，当前占用: ${lock.busyOwner}")
            else -> outcome ?: TaskResult(task.taskId, false, "未知执行结果")
        }.copy(taskId = task.taskId, status = TaskStatus.DONE)
        save(result)
    }

    private fun save(result: TaskResult) {
        // 同一 task 会经历 pending/running/done 多次保存；只在首次登记时入队，
        // 否则 order 里满是重复 id，淘汰时会误删仍在使用的结果。
        val isNew = results.put(result.taskId, result) == null
        if (isNew) order.offer(result.taskId)
        while (order.size > RESULT_LIMIT) { val oldest = order.poll() ?: break; results.remove(oldest) }
        resultStore?.save(result)
    }
}
