# 第三方组件声明

作者原创代码按仓库中的 MIT License 提供。以下组件用于源码运行或 Windows 二进制构建，分别遵守各自上游许可证：

| 组件 | 用途 | 许可证依据 |
| --- | --- | --- |
| Python | 运行时 | Python 官方许可证文件 |
| pywebview | Windows 图形界面和 JavaScript 桥 | 上游项目许可证 |
| py7zr 及其依赖 | 7Z 读取和输出 | 各依赖包随附许可证 |
| WinRAR `Rar.exe` | 可选的 RAR 读取和输出 | WinRAR 软件许可；本项目不捆绑 WinRAR |
| Microsoft Edge WebView2 Runtime | 界面渲染 | Microsoft 分发条款 |
| PyInstaller、pythonnet、clr_loader 等 | 目录版构建组件 | 各依赖包随附许可证 |

源码仓库不提交第三方虚拟环境、PyInstaller 缓存或 WinRAR 文件。制作二进制发行包时应保留依赖包和运行库自带的许可证文件，并遵守其上游条款。
