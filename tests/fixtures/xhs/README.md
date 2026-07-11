# XHS 脱敏 fixture

`real_capture_sanitized_contents.jsonl` 和 `real_capture_sanitized_comments.jsonl` 来自本地 XHS 搜索采集 JSONL 中一对实际关联的帖子/一级评论，经 `sanitize_local_xhs_fixture.py` 生成。脚本先用评论原始 `note_id` 找到对应帖子；无匹配时会报错，绝不改写 ID 伪造关系。

- 删除昵称、头像、位置、cookie、token、设备标识及所有 `xsec_*` 字段。
- note/comment/user 标识替换为稳定 fixture 伪标识；递归替换 URL、图片、视频和封面字段，绝不保留 CDN、签名参数或媒体地址。
- 标题、正文、评论和标签替换为合成文本；保留字段类型、互动数字格式（例如 `10万+`、`1.1万`）、毫秒时间戳和一级评论结构。
- 当前本地采集文件只有一级评论，没有安全可提交的真实回复响应。因此真实结构 fixture 不伪造回复；回复关系继续由单元边界样本覆盖，并在测试中明确标注。

不要把完整本地 JSONL 或任何真实身份字段提交到仓库。
