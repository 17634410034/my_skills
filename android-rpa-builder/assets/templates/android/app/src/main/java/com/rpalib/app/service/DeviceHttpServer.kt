package <NAMESPACE>.service

import android.graphics.Bitmap
import android.util.Base64
import android.util.Log
import fi.iki.elonen.NanoHTTPD
import fi.iki.elonen.NanoHTTPD.IHTTPSession
import fi.iki.elonen.NanoHTTPD.Method
import fi.iki.elonen.NanoHTTPD.Response
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.UUID

/**
 * 顺序固定为：鉴权 → 总闸 → 路由（第 2.4 节）。
 * 先鉴权可避免未授权方通过 200/409 差异探测开关状态。
 */
class DeviceHttpServer(
    port: Int,
    private val lock: DeviceLock,
    private val configStore: ConfigStore,
    private val taskController: TaskController,
    private val operator: TargetOperator,
    private val pollers: List<PollerBase>,
    private val runtime: RuntimeService
) : NanoHTTPD(port) {

    companion object {
        private const val TAG = "DeviceHttp"
        /** 允许的浏览器来源；生产改为白名单，不要用 "*"（第 7.1 项）。 */
        private val ALLOWED_ORIGINS = setOf("http://127.0.0.1:5173")
    }

    /** 写动作注册表：新增端点只加一行，不碰 serve()。 */
    private val actions: Map<String, TaskType> = mapOf(
        "/api/send_message" to TaskType.SEND_MESSAGE,
        "/api/read_messages" to TaskType.READ_MESSAGES,
        "/api/your_action" to TaskType.YOUR_ACTION
    )

    override fun serve(session: IHTTPSession): Response {
        return try {
            val uri = session.uri
            val method = session.method
            val startedAt = System.currentTimeMillis()

            val cfg = configStore.getConfig()
            if (!authorized(session, cfg)) {
                EventLogger.net(TAG, "鉴权失败", method.name, uri, 401, "", "", System.currentTimeMillis() - startedAt, "")
                return json(401, mapOf("success" to false, "message" to "缺少或错误的设备令牌"))
            }

            val body = if (method == Method.POST) {
                readJsonBody(session) ?: return json(400, mapOf("success" to false, "message" to "请求体不是合法 JSON"))
            } else emptyMap()

            val isWrite = method == Method.POST && (uri in actions || uri == "/api/config")
            if (isWrite && uri in actions && !cfg.enabled) {
            return json(409, mapOf("success" to false, "message" to "人工接管模式已开启，RPA 操作已暂停", "mode" to "manual"))
        }

        // 只读端点
        if (method == Method.GET) when {
            uri == "/api/status" -> return json(200, mapOf(
                "success" to true, "data" to runtime.statusSnapshot(),
                "busy" to lock.isBusy, "busy_owner" to lock.busyOwner
            ))
            uri == "/api/dump_ui" -> {
                if (lock.isBusy) return json(409, mapOf(
                    "success" to false, "message" to "设备忙，拒绝 dump_ui（铁律：busy 期间不取证）",
                    "busy_owner" to lock.busyOwner
                ))
                return json(200, mapOf(
                    "success" to true, "data" to (RpaAccessibilityService.instance?.dumpUiTree() ?: RpaAccessibilityService.NOT_CONNECTED)
                ))
            }
            uri == "/api/screenshot" -> {
                if (lock.isBusy) return json(409, mapOf(
                    "success" to false, "message" to "设备忙，拒绝截图",
                    "busy_owner" to lock.busyOwner
                ))
                val bmp = RpaAccessibilityService.instance?.captureScreenBitmap()
                    ?: return json(200, mapOf("success" to false, "message" to "截图不可用：需 Android 11+ 且无障碍已连接"))
                val png = try {
                    ByteArrayOutputStream().use { out -> bmp.compress(Bitmap.CompressFormat.PNG, 100, out); out.toByteArray() }
                } finally {
                    bmp.recycle()
                }
                return json(200, mapOf(
                    "success" to true,
                    "data" to mapOf("format" to "png", "base64" to Base64.encodeToString(png, Base64.NO_WRAP))
                ))
            }
            uri == "/api/logs" -> {
                val limit = (session.parameters["limit"]?.firstOrNull()?.toIntOrNull() ?: 120).coerceIn(1, 1500)
                return json(200, mapOf("success" to true, "data" to EventLogger.newest(limit).map { it.toMap() }))
            }
            uri == "/api/task_queue_size" -> return json(200, mapOf(
                "success" to true, "data" to mapOf("size" to taskController.queueSize()),
                "busy" to lock.isBusy
            ))
            uri.startsWith("/api/task_result/") -> {
                val id = uri.removePrefix("/api/task_result/")
                val r = taskController.result(id)
                // 运行中：data.success == null，客户端据此继续轮询（第 2.12 节）
                if (r == null) return json(200, mapOf("success" to true, "data" to mapOf("unknown" to true, "message" to "无此任务或结果已过期")))
                return json(200, mapOf("success" to true, "data" to mapOf(
                    "task_id" to r.taskId, "success" to r.success, "message" to r.message,
                    "status" to r.status.name, "data" to r.data
                )))
            }
        }

        // 写端点
        if (method == Method.POST) {
            actions[uri]?.let { taskType ->
                val taskId = UUID.randomUUID().toString().take(8)
                taskController.submit(TaskRequest(taskId, taskType, body))
                return json(200, mapOf("success" to true, "data" to mapOf("task_id" to taskId)))
            }
            if (uri == "/api/config") {
                val updated = configStore.update(body)
                runtime.applyTakeoverMode(updated.enabled)
                return json(200, mapOf("success" to true, "data" to updated.redactedView()))   // 不回显密钥
            }
            if (uri.startsWith("/api/")) return json(404, mapOf("success" to false, "message" to "未知写端点: $uri"))
        }

        json(404, mapOf("success" to false, "message" to "未知端点: $method $uri"))
        } catch (e: Exception) {
            Log.e(TAG, "serve 异常: ${e.message}", e)
            json(500, mapOf("success" to false, "message" to "内部错误: ${e.message}"))
        }
    }

    /**
     * 令牌校验：所有端点（含只读）都要求 Authorization: Bearer <配置里的令牌>。
     * 未配置令牌时只放行回环地址（adb forward 场景），避免"绑全网卡 + 无鉴权"（第 7.1 项）。
     */
    private fun authorized(session: IHTTPSession, cfg: RuntimeConfig): Boolean {
        val token = cfg.deviceToken
        if (token.isBlank()) return isLoopback(session.remoteIpAddress)
        val header = session.headers["authorization"] ?: ""   // NanoHTTPD 头部 key 已小写
        return header.removePrefix("Bearer").trim() == token
    }

    private fun isLoopback(ip: String?): Boolean {
        val v = ip ?: return false
        return v == "127.0.0.1" || v == "::1" || v == "0:0:0:0:0:0:0:1" ||
            v.startsWith("127.") || v.equals("localhost", ignoreCase = true)
    }

    /**
     * NanoHTTPD 2.3.1 的 parseBody 不解析 application/json，
     * 必须按 content-length 从 inputStream 原样读 UTF-8（中文经 url-decode 会被破坏）。
     * 解析失败返回 null，由 serve() 统一回 400（不要静默当成空参数）。
     */
    private fun readJsonBody(session: IHTTPSession): Map<String, Any?>? = try {
        // NanoHTTPD 2.3.1 没有 getContentLength()，长度从 header 里取
        val len = session.headers["content-length"]?.toIntOrNull() ?: 0
        if (len <= 0) emptyMap() else {
            val buf = ByteArray(len)
            var read = 0
            while (read < len) {
                val n = session.inputStream.read(buf, read, len - read)
                if (n < 0) break
                read += n
            }
            val text = String(buf, 0, read, Charsets.UTF_8)
            if (text.isBlank()) emptyMap() else JSONObject(text).toMap()
        }
    } catch (e: Exception) {
        Log.w(TAG, "请求体解析失败: ${e.message}"); null
    }

    private fun json(status: Int, payload: Map<String, Any?>): Response {
        val body = JSONObject(payload.deepJsonable()).toString()
        // newFixedLengthResponse 是 NanoHTTPD 的静态方法，不是 Response 的
        val resp = newFixedLengthResponse(Response.Status.lookup(status), "application/json", body)
        // CORS 白名单化，不用 "*"
        resp.addHeader("Vary", "Origin")
        return resp
    }

    private fun JSONObject.toMap(): Map<String, Any?> = keys().asSequence().associateWith { k ->
        when (val v = opt(k)) {
            is JSONObject -> v.toMap()
            is org.json.JSONArray -> v.toList()
            JSONObject.NULL -> null
            else -> v
        }
    }

    private fun org.json.JSONArray.toList(): List<Any?> = (0 until length()).map { i ->
        when (val v = opt(i)) { is JSONObject -> v.toMap(); JSONObject.NULL -> null; else -> v }
    }

    /** Map → JSON 可序列化：把 Any? 收敛到 JSONObject/JSONArray/原始类型。 */
    private fun Map<String, Any?>.deepJsonable(): Map<String, Any?> =
        mapValues { (_, v) -> v?.let { if (it is Map<*, *>) (it as Map<String, Any?>).deepJsonable() else it } }

    private fun EventLogger.Entry.toMap(): Map<String, Any?> = mapOf(
        "id" to id, "time" to time, "level" to level, "tag" to tag,
        "message" to message, "detail" to detail, "method" to method, "url" to url,
        "status" to status, "requestBody" to requestBody, "responseBody" to responseBody,
        "durationMs" to durationMs, "error" to error
    )
}
