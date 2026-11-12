# 核心方案

## 争议筛选

首版从非warming观测筛选：MOG低档与严格Motion通过结果异或，或有低档候选但普通分支未通过。人物检测阳性不是单独条件。可显式加入已知误判时间范围；不能把模型一致误写为语义证明。

相邻核心区间合并，扣除按同一规则版本确认的locks，按配置的最大任务时长切分。每个任务只有一个可编辑范围及可扩展的只读上下文。当前上下文可以重叠，PNG缓存复用；不越过锁定区间修改。上下文长度不是时间保留保护参数。

## 原帧证据

frame_id与stream/PTS/time_base绑定。使用原分辨率RGB PNG，不使用检测器的灰度/模糊/清水印图。seek到前关键帧后连续解码，必须匹配指定PTS；禁止用最近帧冒充。ROI使用原图坐标，返回原图尺寸和裁剪信息。

模型通过get_frames/get_sequence补图。所有返回图片都实际作为Responses的input_image传入，文本元数据明确给出帧ID/时间；文件名和EXIF不是时间合同。图片PNG在私有目录缓存，图像摘要写入证据索引。

## OpenAI循环

严格函数schema限定工具参数。保留response.output和call_id，function_call_output返回图片/文本内容，再继续调用。最终使用严格JSON Schema，仍需要本地检查引用、覆盖、scope和证据时间。

初始使用任务内稀疏全景，AI按需补原帧。每任务图片/轮数受限，运行API尝试总数必须显式配置；预算用日志计数，恢复不清零。响应usage用于实测成本，不用估算代替账单。拒绝/未完成/错误均hold，日志只写清洗后的类型和元数据。

## 精确输出与合并

AI输出static/active/uncertain分段和证据帧ID。程序映射为accelerate/retain/保留未决。所有细化边界必须已查看；每段至少引用两个实际送达的不同时间。半开区间必须完整覆盖editable，不能进入context-only区域。

当前采样只能给出粗边界；细化通过原始连续帧，仍有歧义则明确uncertain。程序以实际PTS映射时间，不让AI编造毫秒。

merge优先级：locks > 本规则下已校验AI或hold覆盖 > detector provisional。旧人物存在保护和静止扩张不自动叠加。缺失裁决保留并标hold，源/任务产物SHA变化拒绝。
