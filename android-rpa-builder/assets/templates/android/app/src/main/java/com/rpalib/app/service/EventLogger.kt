package <NAMESPACE>.service

import android.content.Context
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/**
 * 结构化事件日志：内存环形 + 落盘 + HTTP 取回 + App 内展示。
 * 相对原项目的三处修正：时间戳含日期、写入时脱敏、按大小轮转（见第 2.6 / 7.2 节）。
 */
object EventLogger {

    private const val TAG = "EventLogger"
    private const val MAX_ENTRIES = 1500
    private const val MAX_FIELD_CHARS = 4_000          // 原文 80_000 过大，全文交给 logcat
    private const val LOG_FILE_NAME = "rpa_event_logs.json"
    private const val MAX_FILE_BYTES = 8L * 1024 * 1024 // 轮转阈值

    /** 需要脱敏的 JSON key / URL query / Header 形态（写入时替换，不依赖外部纪律）。 */
    private val SENSITIVE_JSON_KEY =
        Regex("""(?i)"([\w-]*(?:api[_-]?key|token|secret|password|authorization))"(\s*[:=]\s*")[^"]*(")""")
    private val SENSITIVE_QUERY =
        Regex("""(?i)([?&](?:key|api_key|api-key|token|access_token|refresh_token)=)[^&\s"\\]+""")
    private val SENSITIVE_HEADER =
        Regex("""(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;"}]+""")

    data class Entry(
        val id: Long,
        val time: String,
        val level: String,
        val tag: String,
        val message: String,
        val detail: String = "",
        val method: String = "",
        val url: String = "",
        val status: Int? = null,
        val requestBody: String = "",
        val responseBody: String = "",
        val durationMs: Long = -1L,
        val error: String = ""
    )

    @Volatile var entries: List<Entry> = emptyList(); private set

    private val timeFmt = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.CHINA)
    private var nextId = 1L
    private var initialized = false
    private var logFile: File? = null
    private val persistExecutor = Executors.newSingleThreadExecutor()
    private val persistLock = Any()

    @Volatile private var latestSnapshot: List<Entry> = emptyList()
    private var persistScheduled = false

    @Synchronized
    fun init(context: Context) {
        if (initialized) return
        logFile = File(context.applicationContext.filesDir, LOG_FILE_NAME)
        val loaded = loadFromDisk()
        entries = when {
            entries.isEmpty() -> loaded
            loaded.isNotEmpty() -> (entries + loaded).distinctBy { it.id }.sortedByDescending { it.id }.take(MAX_ENTRIES)
            else -> entries
        }
        nextId = (entries.maxOfOrNull { it.id } ?: 0L) + 1L
        initialized = true
        schedulePersist(entries)
    }

    fun debug(tag: String, message: String, detail: String = "") = append("DEBUG", tag, message, detail)
    fun info(tag: String, message: String, detail: String = "") = append("INFO", tag, message, detail)
    fun warn(tag: String, message: String, detail: String = "") = append("WARN", tag, message, detail)
    fun error(tag: String, message: String, detail: String = "") = append("ERROR", tag, message, detail)
    /** 出站/入站网络调用专用级别。 */
    fun net(tag: String, message: String, method: String, url: String, status: Int?,
            requestBody: String, responseBody: String, durationMs: Long, error: String = "") {
        appendRaw(
            level = "NET", tag = tag, message = message, detail = "",
            method = method, url = url, status = status,
            requestBody = requestBody, responseBody = responseBody,
            durationMs = durationMs, error = error
        )
    }

    private fun append(level: String, tag: String, message: String, detail: String) {
        Log.i(TAG, "[$level][$tag] $message ${if (detail.isBlank()) "" else "| $detail"}")
        appendRaw(level, tag, message, detail, "", "", null, "", "", -1L, "")
    }

    @Synchronized
    private fun appendRaw(
        level: String, tag: String, message: String, detail: String,
        method: String, url: String, status: Int?,
        requestBody: String, responseBody: String, durationMs: Long, error: String
    ) {
        val entry = Entry(
            id = nextId++,
            time = timeFmt.format(Date()),
            level = level,
            tag = tag,
            message = trim(redact(message)),
            detail = trim(redact(detail)),
            method = method,
            url = trim(redact(url)),
            status = status,
            requestBody = trim(redact(requestBody)),
            responseBody = trim(redact(responseBody)),
            durationMs = durationMs,
            error = trim(redact(error))
        )
        val list = entries.toMutableList()
        list.add(0, entry)                                  // 新在前
        entries = if (list.size > MAX_ENTRIES) list.subList(0, MAX_ENTRIES) else list
        schedulePersist(entries)
    }

    /** 写入时脱敏：宁可少记字段，也不要把令牌落到磁盘与 HTTP 响应里。 */
    internal fun redact(raw: String): String {
        if (raw.isEmpty()) return raw
        var out = SENSITIVE_JSON_KEY.replace(raw) { "${it.groupValues[1]}${it.groupValues[2]}***REDACTED***${it.groupValues[3]}" }
        out = SENSITIVE_QUERY.replace(out) { "${it.groupValues[1]}***REDACTED***" }
        out = SENSITIVE_HEADER.replace(out) { "${it.groupValues[1]}***REDACTED***" }
        return out
    }

    private fun trim(raw: String): String =
        if (raw.length <= MAX_FIELD_CHARS) raw
        else raw.take(MAX_FIELD_CHARS) + "\n... 已截断，完整内容见 adb logcat ..."

    /** 去抖合并：只写最新快照，落盘线程不阻塞业务线程。 */
    private fun schedulePersist(snapshot: List<Entry>) {
        if (!initialized) return
        latestSnapshot = snapshot.take(MAX_ENTRIES)
        synchronized(persistLock) {
            if (persistScheduled) return
            persistScheduled = true
        }
        persistExecutor.execute {
            while (true) {
                val toWrite = latestSnapshot
                writeToDisk(toWrite)
                synchronized(persistLock) {
                    if (latestSnapshot === toWrite) { persistScheduled = false; return@execute }
                }
            }
        }
    }

    private fun loadFromDisk(): List<Entry> {
        val file = logFile ?: return emptyList()
        if (!file.exists()) return emptyList()
        return try {
            val arr = JSONArray(file.readText())
            (0 until arr.length()).mapNotNull { i -> arr.optJSONObject(i) }.mapNotNull { o ->
                Entry(
                    id = o.optLong("id", 0L), time = o.optString("time"), level = o.optString("level"),
                    tag = o.optString("tag"), message = o.optString("message"), detail = o.optString("detail"),
                    method = o.optString("method"), url = o.optString("url"),
                    status = if (o.has("status") && !o.isNull("status")) o.optInt("status") else null,
                    requestBody = o.optString("requestBody"), responseBody = o.optString("responseBody"),
                    durationMs = o.optLong("durationMs", -1L), error = o.optString("error")
                )
            }.filter { it.id > 0L }.sortedByDescending { it.id }.take(MAX_ENTRIES)
        } catch (_: Exception) { emptyList() }
    }

    private fun writeToDisk(snapshot: List<Entry>) {
        val file = logFile ?: return
        try {
            if (file.exists() && file.length() > MAX_FILE_BYTES) {
                // 简单轮转：当前文件改名为 .1，保留一份历史
                val rotated = File(file.parentFile, file.name + ".1")
                if (rotated.exists()) rotated.delete()
                file.renameTo(rotated)
            }
            val arr = JSONArray()
            snapshot.forEach { e ->
                arr.put(JSONObject().apply {
                    put("id", e.id); put("time", e.time); put("level", e.level); put("tag", e.tag)
                    put("message", e.message); put("detail", e.detail); put("method", e.method); put("url", e.url)
                    if (e.status == null) put("status", JSONObject.NULL) else put("status", e.status)
                    put("requestBody", e.requestBody); put("responseBody", e.responseBody)
                    put("durationMs", e.durationMs); put("error", e.error)
                })
            }
            file.writeText(arr.toString())
        } catch (e: Exception) {
            // 日志持久化失败绝不能反向影响 RPA 主链路
            Log.w(TAG, "日志落盘失败: ${e.message}")
        }
    }

    fun newest(limit: Int): List<Entry> = entries.take(limit)
}
