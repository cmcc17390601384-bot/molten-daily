# 1.3 图书馆与引用衔接实施计划

1. 将英文检索词、时间范围、JIF 年份/阈值、篇幅限制移至找文献页，设置仅保留 API、额度等全局选项。
2. 增加独立图书馆：自建/重命名/删除分类，多篇归类、未分类筛选、收藏与已读；删除分类不删除论文。
3. 支持批量导入 PDF、将本地 PDF 关联到检索题录、DOI 补全和合并重复记录。合并保留分类、收藏、笔记、附件及精读缓存。
4. 保留完整作者、卷期、页码或文章编号，提供 RIS/EndNote Tagged 导出、EndNote XML/RIS 导入；不直接写入专有 .enl 数据库。
5. Word 桌面端通过 COM 插入原生 CITATION 字段，并可插入/更新 BIBLIOGRAPHY 字段。只操作当前文档光标位置，不自动保存或关闭用户文档。已有选中文本不被替换。
6. EndNote 可导入本应用导出题录，也可在 Word 的 EndNote 选项卡执行 Export Word Citations / Convert Word Citations to EndNote，接管引用格式。另支持复制基于 DOI 的 EndNote 临时引用。
7. 回归测试分类、去重合并、PDF 关联、交换格式、XML 转义和 UI；用独立 Word 测试文档验证真实引用字段。验证后构建 EXE、同步 GitHub。

依据：
- https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/09word/convrting_word_citestoen.htm
- https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/09word/components_ofatempcite.htm
- https://learn.microsoft.com/en-us/office/vba/api/word.sources.add

边界：Word 原生引用不冒充 EndNote CWYW 字段；自动获取仅使用公开 PDF 链接，付费文献可由用户合法下载后关联。缺失题录字段显示为缺失，不由模型编造。

## 实施结果

已实现并完成 62 项自动测试和 EXE 自检；增加自定义题录、无 PDF 置灰与导航状态测试。Word 实机 COM 联调受 Windows 登录会话限制阻断，详见 QA_1.3.md。
