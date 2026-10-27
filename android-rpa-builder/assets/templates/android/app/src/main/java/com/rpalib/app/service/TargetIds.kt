package <NAMESPACE>.service

/**
 * 目标 App 元素 ID 登记表（蓝图第 3.7 节）。本对象是唯一允许出现目标 App resource-id 的地方。
 *
 * 规则（违反即评审打回）：
 * 1. 按页面分区；一个语义只留一个当前 ID，禁止 _PREVIOUS/_OLD/_LEGACY/_CURRENT/_LATEST/_NEW。
 * 2. 每条 const val 尾注释：目标 App 版本+构建号 / 页面 / 用途 / 测试日期 / 设备序列号。
 * 3. 原始 ID 允许跨语义重复（目标 App 常复用布局），本表按语义组织，不做值去重。
 * 4. 只凭证据改 ID：Activity + dump_ui + 截图 + 日志齐了才动；否则报"需要逻辑修改"，不许猜。
 * 5. 历史值只靠 Git，不要在注释里留旧值。
 *
 * guard 脚本按 --object TargetIds 定位本对象；常量值必须以 "<TARGET_PKG>:id/" 开头。
 */
object TargetIds {
    // ===== 主页 =====
    // const val HOME_ENTRY = "<TARGET_PKG>:id/xxx"   // <目标版本> / 主页 / 主入口 / <测试日期> / <设备序列号>

    // ===== 任务页 =====
    // const val PRIMARY_INPUT = "<TARGET_PKG>:id/xxx" // <目标版本> / 任务页 / 主输入框 / <测试日期> / <设备序列号>
}
