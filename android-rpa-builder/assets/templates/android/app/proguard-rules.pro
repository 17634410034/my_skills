# NanoHTTPD 内部有反射式访问；打开 R8 前先验证全部设备 HTTP 端点。
-keep class fi.iki.elonen.** { *; }

# 系统通过清单实例化这些入口，类名不能被混淆。
-keep class <NAMESPACE>.RpaApplication { *; }
-keep class <NAMESPACE>.BootReceiver { *; }
-keep class <NAMESPACE>.service.RpaAccessibilityService { *; }
-keep class <NAMESPACE>.service.RuntimeService { *; }
-keep class <NAMESPACE>.activity.ShareProxyActivity { *; }
