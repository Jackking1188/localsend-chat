# LocalSend Chat

一个套在 LocalSend Protocol v2 之上的局域网聊天窗口。核心协议未作改动，
因此官方 LocalSend 客户端能发现它、给它发消息和文件，它也能反向发送。

当前版本：**v2.5.0**

> **非官方项目**：本项目不是 LocalSend 官方衍生版本，与上游无隶属关系。
> LocalSend 名称与图形标识归其各自所有者；LocalSend 本体采用 Apache-2.0 协议。

## 特性

- 纯局域网，无服务器、无账号、数据不上传云端
- 实现 LocalSend Protocol v2 的 `/info`、`/register`、`/prepare-upload`、`/upload`、`/cancel`
- 聊天气泡界面，6 套配色：深空蓝 / 墨玉青 / 极光紫 / 暖砂棕 / 月白 / 樱雾粉
- 图片实时缩略图，一对多群发
- 链接分享：手机浏览器直接下载文件、复制文字
- 设备发现支持 UDP 组播 + 本机网段扫描，可手动「添加设备」「删除设备」
- 内置「自动修复 / 修复防火墙权限」，排查连不上设备时不用手敲命令
- 单文件绿色版：内置 Python 运行时，无需安装 Python 或任何依赖

## 快速开始

1. 到 [Releases](../../releases/latest) 下载 `LocalSendChat.exe`
2. 双击运行；首次弹出 Windows 防火墙提示时勾选「专用网络」并允许
3. 手机安装官方 [LocalSend](https://localsend.org)，与电脑连到同一 Wi-Fi
4. 点「刷新设备」等待手机出现；或点「添加设备」手动填入手机 IP 与端口
5. 选中设备，输入文字后回车发送，或点「＋ 发送文件」选择文件

聊天记录、配置与日志存放于 `%APPDATA%\LocalSendChat\`
（可用环境变量 `LSC_HOME` 改位置）。若启动失败，请查看同目录下的 `startup.log`。

## 端口

| 端口 | 协议 | 用途 |
| --- | --- | --- |
| 53317 | TCP / UDP | LocalSend v2 收发与组播发现（固定，不可改） |
| 53319 | TCP | 链接分享网页，与协议层完全隔离 |

## 链接分享

点「⤴ 链接分享」→「开启分享」，添加要分享的文件或填写文字，
再点「复制链接」；同一 Wi-Fi 下的手机用浏览器打开该链接，即可下载文件或复制文字。
不需要在手机上安装任何额外应用。

## 打包形态

发布包为 7-Zip 自解压格式（32 位 SFX），运行时把内置 Python 运行时与程序目录
释放到临时位置；不写注册表、不安装依赖，卸载时直接删除 exe 并清理
`%APPDATA%\LocalSendChat\` 即可。随包分发的第三方组件见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

## 从源码运行

需要 Python 3.11+ 与 Pillow（仓库根目录已提供 `requirements.txt`）：

```bash
pip install -r requirements.txt
python run.py
```

仓库中的 `app/` 即发布包内的程序目录，`run.py` 为启动入口，
`app/assets/` 存放窗口图标与各尺寸 logo。
发布用的 7-Zip 自解压包由本地工具链生成，构建脚本不在本仓库内。

## 已知限制

- 不提供「对方正在输入」与离线消息队列，LocalSend 会话为一次性设计
- 超大文件受局域网带宽限制
- 路由器开启 AP 隔离或组播过滤时可能无法自动发现设备，需用「添加设备」手动填写 IP
- 目前仅提供 Windows 单文件版本

## 说明与授权

本项目非 LocalSend 官方衍生版本，与上游无隶属关系。
本项目自身采用的许可证见 [LICENSE](LICENSE)；
其对接的 LocalSend 为 Apache-2.0 协议的开源项目。
