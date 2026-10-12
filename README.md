# 熔盐日课 · MoltenDaily

面向熔盐电化学与核能方向的 Windows 桌面文献学习软件。原生 Qt 界面，独立 EXE，固定浅色 / 深绿色配色，不随系统主题切换。

![设置界面](assets/settings.png)

## 功能

- 从 Crossref、OpenAlex 获取真实题录；熔盐电化学为核心，核能背景与短篇优先。
- 按明确年份和来源的 JIF 筛选，支持导入指标表；每日定时检查并通过系统托盘提醒。
- 可配置 DeepSeek API 地址、密钥与模型，设置每日 Token 预算。
- 自行导入 PDF，生成研究问题、方法、结果、创新、局限与核能相关性分析；每条分析附可定位的原文证据。
- 原文与中文按页 / 文本块对应，保留原图和公式，中文新增页使用双栏排版。
- 额外生成原版居中、左右留白的精读 PDF：专业词、短语、长难句编号高亮，对应音标及译文；密集批注自动续页。
- 本地文献库、收藏、已读、生词本、缓存及中断后继续。

## 使用

1. 从本仓库 Releases 下载 Windows x64 便携包，解压并运行 `MoltenDaily.exe`，无需安装 Python。
2. 在“设置与指标”填写自己的 API Key，测试连接并选择接口返回的模型。发行版不包含任何密钥。
3. 使用“每日精选”查询；或者在“文献分析”选择“导入 PDF 并分析”。
4. 在“对照精读”试译、继续全文翻译、生成页边精读批注。

默认每日 09:00 检查，电脑需要开机联网、程序需要运行。关闭窗口后保留托盘，托盘菜单“退出”才完全退出。登录自启动为可选设置。

从旧版升级：先从托盘退出旧版。保留原有 `userdata` 文件夹，将新版 EXE 放到同一文件夹。不要把 `userdata` 上传到 GitHub。

## 范围与数据处理

- 自带 JIF 表仅覆盖 3 本期刊的 **2024 年**指标，不是完整 JCR，也不声称是最新年份。未知指标进入候选；可导入有年份和来源的 CSV 扩充。
- “1:1”表示页和文本块对应，不表示中英文行数相同。扫描件 OCR、图片内部文字翻译和复杂表格原样重建暂不支持。
- 分析依据提取的正文和图注，长文均衡节选并显示范围；不声称进行了图像视觉分析。
- AI 翻译、创新判断和音标可能存在错误。保护标记、证据定位及自动检查不能证明科学语义绝对正确。
- 文献和缓存保存在本机。题录分析向配置的 API 发送题目/摘要；翻译和精读分析发送提取文字，不上传整张原图。
- 密钥使用 Windows DPAPI 加密，优先当前用户绑定；受限环境会退回机器绑定。机器绑定不能隔离同机其他用户，因此不要共享 `userdata`。
- 默认预算为每天 80,000 Token，可自行修改；服务商账单为实际计费依据。软件本身不绕过付费墙。

## 从源码构建

Windows 10/11 x64、Python 3.12 x64：

```powershell
./build.ps1 -Test
```

或者手动构建：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
.venv/Scripts/python.exe -m pytest tests -q --basetemp work/test_tmp -o cache_dir=work/test_cache
.venv/Scripts/python.exe -m PyInstaller --noconfirm --onefile --windowed --name MoltenDaily --distpath dist --workpath work/build app.py
```

输出为 `dist/MoltenDaily.exe`。可通过 `--data-dir PATH` 指定资料目录。

## 验证

30 项本地自动测试通过，覆盖筛选边界、加密、去重、预算、缓存、文本保护、PDF 导出、导入分析、证据定位、每日调度及固定主题。主题测试主动注入深色调色板并切换色彩方案，再确认应用与弹窗恢复固定配色。

编译后的无网络自检：

```powershell
./dist/MoltenDaily.exe --smoke work/exe_check --data-dir work/isolated_data
```

检查七个界面、PDF 渲染、中文双栏、页边批注、导入分析和 DPAPI。输出 `exe_smoke.json`。自检使用本地模拟翻译响应，不调用付费模型；添加 `--check-api` 才会使用指定资料目录中的密钥检查真实模型列表。

## 代码结构

`app.py` 启动；`theme.py` 固定主题；`ui.py` 原生界面；`core.py` 本地状态与加密；`services.py` 检索和 API；`translation.py` 对照翻译；`margins.py` 页边批注；`analysis_report.py` 导入和证据分析。

## 许可证

应用代码采用 **GNU AGPL-3.0-or-later**，无担保。详见 [LICENSE.txt](LICENSE.txt) 和 [第三方声明](THIRD_PARTY_NOTICES.md)。论文版权不因使用本软件而改变。源码与依赖锁定表允许自行重建，包括替换 LGPL 覆盖的 Qt / PySide6 动态库。

接口资料：[Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)、[OpenAlex](https://docs.openalex.org/)、[DeepSeek](https://api-docs.deepseek.com/)。主题实现参考 [Qt QStyleHints](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QStyleHints.html) 与 [QPalette](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QPalette.html)。
