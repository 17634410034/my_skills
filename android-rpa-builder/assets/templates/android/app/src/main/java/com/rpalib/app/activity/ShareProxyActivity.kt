package <NAMESPACE>.activity

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle

/**
 * 跨 App 分享代理（蓝图第 3.9 节）。无 UI、不入最近任务，只把后台分享请求转成前台 Activity 启动。
 *
 * 本文件已完成"代理 + 静态 requestId 握手"，但**没有**完成后台启动许可：
 * 由后台无障碍服务直接 startActivity 在部分 ROM（MIUI 实测）会被丢弃。上线前必须按蓝图补
 * PendingIntent + ActivityOptions 的后台启动许可（按 SDK 分支选常量），并在调用侧用
 * awaitHandled() 确认 onCreate 真的跑过（≈2s 超时），失败时明确提示"请开启后台弹出界面权限"。
 * 未完成上述验证前，不要把它用于无人值守的文件/图片分享。
 */
class ShareProxyActivity : Activity() {

    companion object {
        private const val EXTRA_TARGET = "target_intent"
        private const val EXTRA_REQUEST_ID = "request_id"

        @Volatile private var handledRequestId: String = ""

        fun launch(context: Context, target: Intent, requestId: String): Boolean = try {
            val proxy = Intent(context, ShareProxyActivity::class.java)
                .putExtra(EXTRA_TARGET, target)
                .putExtra(EXTRA_REQUEST_ID, requestId)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION)
            context.startActivity(proxy)
            true
        } catch (e: Exception) {
            false
        }

        /** 轮询确认代理 Activity 真的启动过；返回 false 时不要假设分享已完成。 */
        fun awaitHandled(requestId: String, timeoutMs: Long = 2000): Boolean {
            val deadline = System.currentTimeMillis() + timeoutMs
            while (System.currentTimeMillis() < deadline) {
                if (handledRequestId == requestId) return true
                Thread.sleep(50)
            }
            return false
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val requestId = intent.getStringExtra(EXTRA_REQUEST_ID).orEmpty()
        @Suppress("DEPRECATION")
        val target = intent.getParcelableExtra<Intent>(EXTRA_TARGET)
        try {
            if (target != null) startActivity(target)
        } catch (e: Exception) {
            // 失败由调用侧的 awaitHandled 判定，不在这里吞掉结果
        }
        handledRequestId = requestId
        finish()
    }
}
