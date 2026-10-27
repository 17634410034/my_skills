package <NAMESPACE>.service

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys
import org.json.JSONObject

/**
 * 三类存储分开（蓝图第 2.9 项）：普通配置走 prefs，设备令牌走 EncryptedSharedPreferences。
 * 任何回显一律走 RuntimeConfig.redactedView()，绝不返回令牌原文。
 * 加密存储不可用时拒绝保存令牌（宁可退回"仅回环可访问"，也不要明文落盘）。
 */
class PrefsConfigStore(context: Context) : ConfigStore {

    companion object {
        private const val TAG = "PrefsConfigStore"
        private const val PLAIN = "rpa_config"
        private const val SECURE = "rpa_secure"
        private const val KEY_ENABLED = "enabled"
        private const val KEY_BACKEND = "backendBaseUrl"
        private const val KEY_TOKEN = "deviceToken"
        private const val KEY_EXTRA = "extra"
    }

    private val appContext = context.applicationContext
    private val plain: SharedPreferences = appContext.getSharedPreferences(PLAIN, Context.MODE_PRIVATE)
    private val secure: SharedPreferences? by lazy { createSecurePrefs() }

    override fun getConfig(): RuntimeConfig = RuntimeConfig(
        enabled = plain.getBoolean(KEY_ENABLED, true),
        backendBaseUrl = plain.getString(KEY_BACKEND, "") ?: "",
        deviceToken = readToken(),
        extra = readExtra()
    )

    override fun update(patch: Map<String, Any?>): RuntimeConfig {
        val editor = plain.edit()
        pick(patch, "enabled")?.let { editor.putBoolean(KEY_ENABLED, asBool(it, default = true)) }
        pick(patch, "backendBaseUrl", "backend_base_url")
            ?.let { editor.putString(KEY_BACKEND, it.toString().trim()) }
        pick(patch, "extra")?.let { editor.putString(KEY_EXTRA, toFlatJson(it)) }
        editor.apply()
        pick(patch, "deviceToken", "device_token")?.let { writeToken(it.toString()) }
        return getConfig()
    }

    private fun pick(patch: Map<String, Any?>, vararg keys: String): Any? =
        keys.firstNotNullOfOrNull { patch[it] }

    private fun asBool(v: Any?, default: Boolean): Boolean = when (v) {
        is Boolean -> v
        is String -> v.equals("true", ignoreCase = true) || v == "1"
        is Number -> v.toInt() != 0
        else -> default
    }

    private fun toFlatJson(v: Any?): String {
        val obj = JSONObject()
        (v as? Map<*, *>)?.forEach { (k, value) ->
            if (k != null) obj.put(k.toString(), value?.toString() ?: JSONObject.NULL)
        }
        return obj.toString()
    }

    private fun readExtra(): Map<String, String> {
        val raw = plain.getString(KEY_EXTRA, null) ?: return emptyMap()
        return runCatching {
            val obj = JSONObject(raw)
            obj.keys().asSequence().associateWith { key -> obj.optString(key) }
        }.getOrDefault(emptyMap())
    }

    @Suppress("DEPRECATION")
    private fun createSecurePrefs(): SharedPreferences? = try {
        // security-crypto 1.0.0 的 API：MasterKeys + create(fileName, masterKeyAlias, context, ...)
        val masterKeyAlias = MasterKeys.getOrCreate(MasterKeys.AES256_GCM_SPEC)
        EncryptedSharedPreferences.create(
            SECURE,
            masterKeyAlias,
            appContext,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    } catch (e: Exception) {
        Log.e(TAG, "加密存储不可用，设备令牌不会被保存: ${e.message}")
        null
    }

    private fun readToken(): String = try {
        secure?.getString(KEY_TOKEN, "") ?: ""
    } catch (e: Exception) {
        Log.e(TAG, "读取设备令牌失败: ${e.message}")
        ""
    }

    private fun writeToken(token: String) {
        val prefs = secure
        if (prefs == null) {
            Log.e(TAG, "加密存储不可用，拒绝明文保存设备令牌")
            return
        }
        try {
            prefs.edit().putString(KEY_TOKEN, token).apply()
        } catch (e: Exception) {
            Log.e(TAG, "保存设备令牌失败: ${e.message}")
        }
    }
}
