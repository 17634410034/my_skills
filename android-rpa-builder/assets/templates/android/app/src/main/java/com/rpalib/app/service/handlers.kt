package <NAMESPACE>.service

/**
 * 把 L3/L4 接起来的工厂。
 * 新增任务类型只改三处：TaskType、DeviceHttpServer.actions、本文件的 handlerRegistry()。
 * 注意：handler 运行在唯一 TaskExecutor 线程上，且已持有 DeviceLock，内部不要再抢锁。
 */
fun handlerRegistry(
    operator: TargetOperator,
    lock: DeviceLock,
    config: ConfigStore
): Map<TaskType, (TaskRequest) -> TaskResult> = mapOf(
    TaskType.READ_MESSAGES to { req -> operator.readMessages(req) },
    TaskType.SEND_MESSAGE to { req -> operator.sendMessage(req) },
    TaskType.YOUR_ACTION to { req ->
        TaskResult(
            taskId = req.taskId,
            success = false,
            message = "任务未实现：请在 TargetOperator 中实现 YOUR_ACTION",
            status = TaskStatus.DONE
        )
    }
)

/**
 * 业务轮询器在这里注册（蓝图第 4.1 节）。
 * 没有定时/轮询需求就保持空列表；有就继承 PollerBase 并注入同一把 DeviceLock。
 */
fun buildPollers(
    lock: DeviceLock,
    config: ConfigStore,
    operator: TargetOperator,
    controller: TaskController
): List<PollerBase> = emptyList()
