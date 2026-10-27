package <NAMESPACE>.service

import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

/**
 * 6 段（秒 分 时 日 月 周）或 7 段（末段年份忽略，须为 *）Quartz 风格 next-fire 计算。
 * 日/周同时受限时取并集（Quartz 语义）。
 */
class CronSchedule(expression: String, private val zone: ZoneId = ZoneId.systemDefault()) {

    private val seconds: Set<Int>
    private val minutes: Set<Int>
    private val hours: Set<Int>
    private val daysOfMonth: Set<Int>
    private val months: Set<Int>
    private val daysOfWeek: Set<Int>          // 1=周一 .. 7=周日（ISO）
    private val domRestricted: Boolean
    private val dowRestricted: Boolean
    val valid: Boolean

    init {
        val fields = expression.trim().split(Regex("\\s+"))
        if (fields.size !in 6..7 || (fields.size == 7 && fields[6] != "*")) {
            valid = false; seconds = setOf(0); minutes = setOf(0); hours = setOf(0)
            daysOfMonth = setOf(1); months = setOf(1); daysOfWeek = setOf(1)
            domRestricted = false; dowRestricted = false
        } else {
            val s = expand(fields[0], 0, 59)
            val m = expand(fields[1], 0, 59)
            val h = expand(fields[2], 0, 23)
            val d = expand(fields[3], 1, 31)
            val mo = expand(fields[4], 1, 12)
            val wd = expand(fields[5], 1, 7)
            valid = s != null && m != null && h != null && d != null && mo != null && wd != null
            seconds = s ?: setOf(0); minutes = m ?: setOf(0); hours = h ?: setOf(0)
            daysOfMonth = d ?: setOf(1); months = mo ?: setOf(1); daysOfWeek = wd ?: setOf(1)
            domRestricted = fields[3] != "*"
            dowRestricted = fields[5] != "*"
        }
    }

    /** 返回严格晚于 baseMillis 的下一次触发时刻；无解返回 null。 */
    fun nextAfter(baseMillis: Long): Long? {
        if (!valid) return null
        var cursor = ZonedDateTime.ofInstant(Instant.ofEpochMilli(baseMillis), zone)
            .plusSeconds(1).withNano(0)
        val limit = cursor.plusYears(5)
        while (cursor.isBefore(limit)) {
            if (!months.contains(cursor.monthValue)) {
                cursor = cursor.plusMonths(1).withDayOfMonth(1).withHour(0).withMinute(0).withSecond(0).withNano(0); continue
            }
            if (!matchesDay(cursor)) {
                cursor = cursor.plusDays(1).withHour(0).withMinute(0).withSecond(0).withNano(0); continue
            }
            if (!hours.contains(cursor.hour)) { cursor = cursor.plusHours(1).withMinute(0).withSecond(0); continue }
            if (!minutes.contains(cursor.minute)) { cursor = cursor.plusMinutes(1).withSecond(0); continue }
            if (!seconds.contains(cursor.second)) { cursor = cursor.plusSeconds(1); continue }
            return cursor.toInstant().toEpochMilli()
        }
        return null
    }

    private fun matchesDay(t: ZonedDateTime): Boolean {
        val isoDow = t.dayOfWeek.value                      // 1..7
        val byDom = daysOfMonth.contains(t.dayOfMonth)
        val byDow = daysOfWeek.contains(isoDow)
        return when {
            domRestricted && dowRestricted -> byDom || byDow
            domRestricted -> byDom
            dowRestricted -> byDow
            else -> true
        }
    }

    /** 支持通配、单值 a、区间 a-b、步长（形如 a/n 或 a-b/n）、逗号列表。非法返回 null。 */
    private fun expand(field: String, min: Int, max: Int): Set<Int>? {
        val out = sortedSetOf<Int>()
        for (token in field.split(",")) {
            val parts = token.split("/")
            if (parts.size > 2) return null
            val step = if (parts.size == 2) parts[1].toIntOrNull() ?: return null else 1
            if (step <= 0) return null
            val range = when (val body = parts[0]) {
                "*" -> min to max
                else -> {
                    if (body.contains("-")) {
                        val ends = body.split("-")
                        if (ends.size != 2) return null
                        val a = ends[0].toIntOrNull() ?: return null
                        val b = ends[1].toIntOrNull() ?: return null
                        a to b
                    } else {
                        val single = body.toIntOrNull() ?: return null
                        if (parts.size == 2) single to max else single to single
                    }
                }
            }
            if (range.first < min || range.second > max || range.first > range.second) return null
            var v = range.first
            while (v <= range.second) { out.add(v); v += step }
        }
        return out.ifEmpty { null }
    }

    /** 宽松时间戳解析：epoch 毫秒 / ISO-8601 / 常见日期格式。解析失败返回 null。 */
    companion object {
        fun parseOptionalMillis(raw: String, zone: ZoneId = ZoneId.systemDefault()): Long? {
            val s = raw.trim()
            if (s.isEmpty()) return null
            s.toLongOrNull()?.let { return if (s.length >= 13) it else it * 1000L }
            runCatching { return Instant.parse(s).toEpochMilli() }
            runCatching { return ZonedDateTime.parse(s).toInstant().toEpochMilli() }
            for (pattern in listOf(
                "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd HH:mm", "yyyy-MM-dd"
            )) {
                runCatching {
                    val dt = DateTimeFormatter.ofPattern(pattern)
                    if (pattern.contains("HH"))
                        return java.time.LocalDateTime.parse(s, dt).atZone(zone).toInstant().toEpochMilli()
                    return java.time.LocalDate.parse(s, dt).atStartOfDay(zone).toInstant().toEpochMilli()
                }
            }
            return null
        }

        /** 循环任务自续期：下一次触发，且必须尊重 endAt。 */
        fun nextRecurringDueAt(
            expression: String,
            fromMillis: Long,
            endAtMillis: Long?,
            zone: ZoneId = ZoneId.systemDefault()
        ): Long? {
            val next = CronSchedule(expression, zone).nextAfter(fromMillis) ?: return null
            if (endAtMillis != null && next > endAtMillis) return null
            return next
        }
    }
}
