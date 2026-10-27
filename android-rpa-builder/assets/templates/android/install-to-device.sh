#!/usr/bin/env bash
# 编译并【覆盖安装】Debug 包到已连接真机。禁止先卸载：卸载会丢无障碍授权与持久化状态。
set -e
cd "$(dirname "$0")"

APP_ID="<APP_ID>"
# MIUI/HyperOS 的"后台弹出界面"appop op 码；非小米系机型此段自动跳过
BACKGROUND_POPUP_OP="10021"

# 1) JDK 兜底：宿主 Java 版本过新会打挂 Gradle 8.4
if [ -z "$JAVA_HOME" ] || [[ "$(java -version 2>&1)" == *"24"* ]]; then
  if [ -d "/Applications/Android Studio.app/Contents/jbr/Contents/Home" ]; then
    export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
    echo "Using Android Studio JBR: $JAVA_HOME"
  elif command -v /usr/libexec/java_home &>/dev/null; then
    for v in 17 21 11; do
      if /usr/libexec/java_home -v "$v" 2>/dev/null; then
        export JAVA_HOME=$(/usr/libexec/java_home -v "$v"); echo "Using JDK $v: $JAVA_HOME"; break
      fi
    done
  fi
fi
if [ -z "$JAVA_HOME" ]; then
  echo "未检测到 JDK 17/21。请装 JDK 17 后执行: JAVA_HOME=\$(/usr/libexec/java_home -v 17) ./gradlew installDebug"
  exit 1
fi

# 2) 编译 + 覆盖安装
./gradlew installDebug

# 3) adb 发现：PATH → $ANDROID_HOME → 默认 SDK 目录
ADB_BIN=""
if command -v adb >/dev/null 2>&1; then ADB_BIN="$(command -v adb)"
elif [ -n "$ANDROID_HOME" ] && [ -x "$ANDROID_HOME/platform-tools/adb" ]; then ADB_BIN="$ANDROID_HOME/platform-tools/adb"
elif [ -x "$HOME/Library/Android/sdk/platform-tools/adb" ]; then ADB_BIN="$HOME/Library/Android/sdk/platform-tools/adb"
fi
if [ -z "$ADB_BIN" ]; then echo "警告：未找到 adb，跳过厂商权限校验。"; exit 0; fi

# 4) MIUI 会在覆盖安装后【异步】用自己的权限库回滚该 appop，
#    因此 set → 等 5s → 再 set → 两次 get 校验，任一失败即退出。
MANUFACTURER="$("$ADB_BIN" shell getprop ro.product.manufacturer 2>/dev/null | tr '[:upper:]' '[:lower:]' | tr -d '\r')"
if [[ "$MANUFACTURER" == *"xiaomi"* || "$MANUFACTURER" == *"redmi"* || "$MANUFACTURER" == *"poco"* ]]; then
  echo "检测到 MIUI/HyperOS，配置后台弹出界面权限..."
  "$ADB_BIN" shell appops set "$APP_ID" "$BACKGROUND_POPUP_OP" allow; sleep 5
  "$ADB_BIN" shell appops set "$APP_ID" "$BACKGROUND_POPUP_OP" allow; sleep 2
  for check in 1 2; do
    STATE="$("$ADB_BIN" shell appops get "$APP_ID" "$BACKGROUND_POPUP_OP" 2>/dev/null | tr -d '\r')"
    if [[ "$STATE" != *"allow"* ]]; then
      echo "错误：第 $check 次校验未通过：$STATE"; echo "请在系统设置手动允许"后台弹出界面"后重试。"; exit 1
    fi
    sleep 2
  done
  echo "后台弹出界面权限已稳定为 allow。"
fi

# 5) 装后自检
"$ADB_BIN" shell am force-stop "$APP_ID"
"$ADB_BIN" shell am start -n "$APP_ID/<NAMESPACE>.activity.MainActivity"
sleep 3
"$ADB_BIN" shell pidof "$APP_ID"
