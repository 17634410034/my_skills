package <NAMESPACE>.service

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * 任务结果落盘（蓝图第 7.4 项）：进程重启后 SDK 仍能取到终态，不会永远"运行中"。
 * data 只允许 JSON 可序列化类型；复杂业务态请另建 Store，不要塞进结果表。
 * TTL 由业务层按 taskId 的创建时间另行清理，这里只做容量上限。
 */
class FileTaskResultStore(
    context: Context,
    fileName: String = "rpa_task_results.json"
) : TaskResultStore {

    companion object { private const val MAX_ENTRIES = 1000 }

    private val file = File(context.applicationContext.filesDir, fileName)
    private val guard = Any()
    private val cache = LinkedHashMap<String, TaskResult>()

    init {
        synchronized(guard) { cache.putAll(readFromDisk()) }
    }

    override fun save(result: TaskResult) = synchronized(guard) {
        cache[result.taskId] = result
        while (cache.size > MAX_ENTRIES) {
            val oldest = cache.keys.firstOrNull() ?: break
            cache.remove(oldest)
        }
        writeToDisk()
    }

    override fun load(): Map<String, TaskResult> = synchronized(guard) { HashMap(cache) }

    private fun readFromDisk(): Map<String, TaskResult> {
        if (!file.exists()) return emptyMap()
        return try {
            val arr = JSONArray(file.readText())
            val out = LinkedHashMap<String, TaskResult>()
            for (i in 0 until arr.length()) {
                val o = arr.optJSONObject(i) ?: continue
                val id = o.optString("taskId").takeIf { it.isNotBlank() } ?: continue
                val status = runCatching { TaskStatus.valueOf(o.optString("status", TaskStatus.LOST.name)) }
                    .getOrDefault(TaskStatus.LOST)
                out[id] = TaskResult(
                    taskId = id,
                    success = o.optBoolean("success", false),
                    message = o.optString("message"),
                    data = o.opt("data").takeUnless { it == JSONObject.NULL },
                    status = status
                )
            }
            out
        } catch (e: Exception) {
            EventLogger.warn("TaskResultStore", "任务结果读取失败，按空表继续: ${e.message}")
            emptyMap()
        }
    }

    private fun writeToDisk() {
        try {
            val arr = JSONArray()
            cache.values.forEach { r ->
                arr.put(JSONObject().apply {
                    put("taskId", r.taskId)
                    put("success", r.success)
                    put("message", r.message)
                    put("status", r.status.name)
                    put("data", r.data?.let { JSONObject.wrap(it) } ?: JSONObject.NULL)
                })
            }
            val tmp = File(file.parentFile, file.name + ".tmp")
            tmp.writeText(arr.toString())
            if (!tmp.renameTo(file)) {
                file.writeText(arr.toString())
                tmp.delete()
            }
        } catch (e: Exception) {
            // 落盘失败不能阻断执行链路，但必须留痕（结果仍在内存表里）
            EventLogger.warn("TaskResultStore", "任务结果落盘失败: ${e.message}")
        }
    }
}
