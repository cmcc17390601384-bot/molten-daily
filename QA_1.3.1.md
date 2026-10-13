# 1.3.1 验收记录

- 68 项自动测试通过。
- 复现文件：13 页，214 个已缓存文本块，1226 条译文与批注。逐条嵌入字体的旧写法导出时进程超过 6 GB；即使关闭深度去重仍会长时间保存，因此同时修改了字体资源共享。
- 修复后的实际 GUI 续做测试：6.41 秒，112 页，1226 条，新增 Token 0。20 ms 定时器触发 314 次，最大间隔 0.032 秒。生成 PDF 约 10 MB，共 19 个字体资源。修复后独立导出进程观察值约 96 MB。
- 已检查首页、续页、中间图页和末页渲染，中央原图与边栏清晰。
- 独立进程暂停后译文缓存与先前完整 PDF 保留，再次生成成功。生成过程中有段落/页数/保存进度，长时间无进展可自动停止。
- 单篇与批量 RIS 导出和供应商隔离均通过界面测试。
- 已删除 Word COM、EndNote 启动、临时引用与对应自检参数，仅保留题录文件交换。
- 真实 DeepSeek /models 返回当前模型列表，未发送收费翻译请求。新模型 ID 接纳与去重通过测试；其他供应商需要用户配置相应密钥。

技术依据：
- https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html
- https://pymupdf.readthedocs.io/en/latest/document.html#Document.save
- https://api-docs.deepseek.com/api/list-models/
- https://siliconflow.readme.io/reference/retrieve-a-list-of-models
