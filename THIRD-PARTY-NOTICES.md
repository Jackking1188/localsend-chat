# 第三方组件声明

`LocalSendChat.exe` 是一个 7-Zip 自解压绿色包，内部随附以下第三方组件。
本文件仅作来源与许可说明，各组件权利归其原作者所有。

| 组件 | 用途 | 许可 | 出处 |
| --- | --- | --- | --- |
| Python（CPython，含 tkinter、Tcl/Tk） | 程序运行运行时 | PSF License / Python Software Foundation License | https://www.python.org/ |
| Pillow | 图片缩略图处理 | HPND 历史许可 | https://python-pillow.org/ |
| 7-Zip SFX（9.20） | 单文件自解压外壳 | LGPL-2.1-or-later（含 unRAR 限制） | https://www.7-zip.org/ |
| tkinterdnd2 0.7.0 | Tkinter 拖放封装 | MIT（Copyright (c) 2020 Philippe Gagné） | https://github.com/Eliav2/tkinterdnd2 |
| tkdnd 2.10.3（win-x64 DLL + Tcl 脚本） | Windows OLE2 原生拖放（CF_HDROP / CF_UNICODETEXT） | Tcl/Tk（BSD 风格）许可 | https://github.com/petasis/tkdnd |
| LocalSend Protocol v2 | 局域网收发协议（仅实现，未包含其代码） | 上游项目 Apache-2.0 | https://localsend.org/ |

- 自解压包内的 `LICENSE.txt` 为 CPython 自带许可全文，随发行包原样保留。
- `tkinterdnd2` 的 MIT 许可全文随包放在 `tkinterdnd2/LICENSE-MIT.txt`；
  tkdnd 二进制仅随 exe 分发，不进源码版本库。
- 拖放组件为**可选能力**：组件缺失时程序自动降级，聊天与收发不受影响。
- 本项目不是 LocalSend 官方衍生版本，与上游无隶属关系；
  LocalSend 名称及图形标识归其各自所有者。
- 本项目自身的许可证见仓库根目录 `LICENSE`。
