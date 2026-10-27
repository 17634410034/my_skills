package <NAMESPACE>.service

/**
 * 任务类型注册表（蓝图第 4 章）。
 * 新增一种动作要同步三处：本枚举、DeviceHttpServer.actions、handlerRegistry()。
 * 名称按业务改；不要保留历史别名（历史只靠 Git）。
 */
enum class TaskType {
    SEND_MESSAGE,
    READ_MESSAGES,
    YOUR_ACTION
}
