# 协议

## 输入

消费motion-detect/v1的source.json、全帧frames.jsonl、observations.jsonl、status.json。先验证complete和产物指纹，再校验唯一帧ID、严格递增实际时间及视频总时长。没有上游Python依赖。

## prepare产物

- manifest.json：协议/规则版本、源快照、配置、产物SHA和运行指纹。
- frames.jsonl：原始帧目录；`@end`为唯一视频结束边界，不能请求为图片。
- tasks.jsonl：task_id、editable/context原帧索引半开范围、policy_version。
- baseline.json：原始测量形成的provisional分段；不读取旧业务state为真值。
- locks.json：明确确认的action与范围。锁定文件须包含`policy_version`及segments，每项为start_frame_id、end_frame_id_exclusive、action、reason。

## ReviewResult

segments包含start_frame_id/end_frame_id_exclusive、activity(static/active/uncertain)、evidence_frame_ids和简短reason。引用必须属于实际送入模型的帧集合；细化边界也必须已看过。至少两个不同时间的证据。不允许gap/overlap或跨editable。

decisions.jsonl另附运行指纹、模型/指令/schema版本、状态、已看帧集合。改变模型/endpoint/规则需重新评估缓存，merge仅消费当前review-state版本。

## 时间线

timeline.json输出start_ms/end_ms/action/activity/source/task_id；完整覆盖`[0,total_ms)`。summary区分retain/accelerate/unresolved，hold不是审核通过。所有真实值均属于私有运行数据。
