package <NAMESPACE>.service

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 判定层单测（蓝图第 3.8 节）：纯 JUnit4，不实例化 Operator，不碰 Android 类。
 * 写入真实设备上的文案/数值当夹具；执行层靠真机回归清单，不要在这里 mock。
 */
class TargetOperatorTest {

    @Test
    fun `action label matches trimmed exact candidate`() {
        assertTrue(TargetOperator.isActionLabel(" 发送 ", listOf("发送")))
    }

    @Test
    fun `action label rejects partial match`() {
        assertFalse(TargetOperator.isActionLabel("发送给张三", listOf("发送")))
    }

    @Test
    fun `null text never matches`() {
        assertFalse(TargetOperator.isActionLabel(null, listOf("发送")))
    }
}
