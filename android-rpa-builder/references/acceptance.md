# 验收、观测与交接

> 依据：`blueprint.md` 第 2.14 节（取证铁律）、第 7 章（反模式）、第 4.2 节（幂等铁律）。
> 原则：**每条"通过"都要有一条真跑过的命令或一份真机证据包**。没有证据的通过等于没测。

---

## A. 功能验收（逐条任务）

| 项目 | 验证方式 | 通过标准 |
|---|---|---|
| happy path | 真机执行一遍，存证据包 | 页面停在预期终态；结果表 `success=true` |
| 失败路径 | 人为制造失败（断网 / 目标 App 未装 / 停在错误页） | 返回可读 `message` + 取证载荷；不抛异常到上层 |
| 幂等 | 同一任务连下两次 | 无重复副作用（如不重复发送） |
| 结果可查 | `GET /api/task_result/<id>` | 终态可查；重启后不返"运行中" |
| 超时矩阵 | 记录设备侧最坏耗时 | 设备内 < SDK < 网关 < 调用方，逐层有余量 |

---

## B. 稳定性与自愈矩阵（无人值守的命门）

| 场景 | 操作 | 期望 |
|---|---|---|
| 进程被杀 | `adb shell am force-stop <APP_ID>` | 打开 App 后自动恢复；或收到"请手动打开"通知 |
| 无障碍被关 | 系统设置关闭再打开 | `accessibilityEnabled` 回到 true，服务可用 |
| 设备重启 | `adb reboot` | Android 14- 自动起；15+ 被拒时有通知兜底 |
| 断网/换网 | 关 WiFi 再开 / 切网 | 服务不掉；后端不可达时轮询跳过而非崩溃 |
| 亮灭屏 | 按电源键息屏再亮屏 | 息屏被尊重；亮屏后屏幕常亮恢复 |
| 长时间空转 | 见下 24h 观测 | 无 "服务消失" 窗口 |
| 队列积压 | 连续投递 50 个任务 | 串行执行、不并发抢屏；`busy_owner` 始终有值 |
| 低电量/省电模式 | 开启省电模式 | 已加电池白名单则不受影响 |

### 24 小时观测（照抄执行，改 IP/令牌）

```bash
OUT="soak-$(date +%Y%m%d-%H%M%S).csv"
echo "ts,success,http,a11y,busy,queue" > "$OUT"
for i in $(seq 1 288); do                       # 288 × 5min = 24h
  T="$(date '+%F %T')"
  R="$(curl -sS --noproxy '*' --max-time 8 \
        -H "Authorization: Bearer $DEVICE_TOKEN" \
        "http://127.0.0.1:19527/api/status" 2>/dev/null || true)"
  if [[ -n "$R" ]]; then
    echo "$T,$(printf '%s' "$R" | jq -r '[.success,(.data.httpServerRunning|tostring),(.data.accessibilityEnabled|tostring),(.busy|tostring),(.data.taskQueueSize|tostring)]|join(",")')" >> "$OUT"
  else
    echo "$T,UNREACHABLE,,,," >> "$OUT"
  fi
  sleep 300
done
# 判据：UNREACHABLE 连续出现 > 3 次（15 分钟）= 不合格，必须查到原因
```

---

## C. 安全验收（第 7.1 / 7.2 项）

| 检查 | 命令 | 通过标准 |
|---|---|---|
| 令牌必填 | 配好令牌后用 LAN IP 访问 | 无/错令牌 → `401` |
| 空令牌不裸奔 | 未配令牌时用 LAN IP 访问 | `401`（只有回环地址放行） |
| 状态不回显密钥 | `GET /api/status`、`POST /api/config` | 只见 `deviceTokenSet=true/false`，无令牌原文 |
| 日志脱敏 | 请求体里塞假 key → `GET /api/logs` | 显示 `***REDACTED***` |
| CORS | 带 `Origin` 头访问 | 默认无 `Access-Control-Allow-Origin`；若启用必须白名单且非 `*` |
| 密钥不入库 | `git status` / `git check-ignore keystore.properties` | keystore/口令/日志均被忽略 |
| 取证目录权限 | `ls -ld evidence/*` | `700`，且不在仓库内 |
| 剪贴板与界面 | 人工检查引导页 | 任何界面不显示令牌原文 |

---

## D. 可观测验收

- `GET /api/status` 字段完整：`accessibilityEnabled / httpServerRunning / currentApp / currentPage / taskQueueSize / mode / versionName / devicePort`，顶层 `busy / busy_owner`。
- `GET /api/logs` 级别含 `DEBUG/INFO/WARN/ERROR/NET`，时间戳带**日期**（`yyyy-MM-dd HH:mm:ss.SSS`），超长字段被截断并标注。
- 任务结果有状态机：`pending / running / done / lost`（`lost` 必须显式存在）。
- `tools/capture-context.sh` 一条命令可采齐三源（Activity + dump_ui + 截图）与状态、日志。

---

## E. 交接物（缺一不可）

| 产物 | 内容 |
|---|---|
| `README.md` | 立项决策 16 项、参数表（`APP_ID`/`NAMESPACE`/`TARGET_PKG`/端口/令牌下发）、**外部工具链只认 applicationId** 的显著提示、禁止区 |
| 运行手册（runbook） | 覆盖安装步骤、权限清单（含厂商 appops 校验命令）、令牌轮换、设备注册表维护、目标 App 升级后的适配流程、故障处置（服务没起 / 无障碍掉了 / 权限被回滚 / 设备失联） |
| 适配文档 | 现行方法论唯一版本 + `TargetIds` 分区说明 + 证据包索引 |
| 验收报告 | 上面各表的门禁输出（命令 + 真实输出），以及未通过/未验证项 |
| 已知限制 | 需要逻辑修改的适配项、P1/P2 待办、Android 15+ 前台服务策略结论、跨 App 分享是否可用 |

### 交付话术必须包含

1. **真机验证到什么程度**：哪些阶段在真机跑过、哪些只在本机编译通过（模板已验证的事实见 `build-phases.md`）。
2. **前台服务类型决策**：目标 Android 版本 + 是否受 dataSync 限时影响（见 `pitfalls.md`）。
3. **未完成项**：例如跨 App 分享的后台启动许可是否补齐、轮询器是否落地。
4. **下一步**：谁维护设备注册表、下线目标 App 升级后如何重跑适配。

---

## F. 最终自查（交付前逐条打勾）

- [ ] `scaffold.py check` PASS，无残留占位符
- [ ] `assembleDebug` + `testDebugUnitTest` 通过
- [ ] `/api/status` 在**免 USB** 条件下可访问
- [ ] 杀进程 / 关无障碍 / 重启 三类自愈各测过一次
- [ ] 24h 观测完成且无长时间失联
- [ ] 安全表逐项通过（尤其空令牌与日志脱敏）
- [ ] `ui_ids_guard.py check` PASS，历史后缀扫描为空，定义集 ≡ 引用集
- [ ] 每条任务的证据包已归档、不进仓库
- [ ] README / runbook / 已知限制三件套齐备