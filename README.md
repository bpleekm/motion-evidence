# motion-evidence

面向争议视频区间的OpenAI视觉取证harness，与 [motion-detect](https://github.com/bpleekm/motion-detect) 通过`motion-detect/v1`文件协作。

交付阶段：离线任务、精确取帧、OpenAI工具循环与时间线合并主流程；真实模型质量验收待本地配置和新口径样本补齐。

**规则：静坐、睡觉、静止站立均允许加速；真实活动保留；证据不足先hold并原速保留。** 人物存在或姿态未知本身不自动触发送审。

## 四步主流程

```sh
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$EVIDENCE_REPO/src"
"$PYTHON" -B -m motion_evidence.cli prepare --detector "$RUN_ROOT/detect" --out "$RUN_ROOT/review"
"$PYTHON" -B -m motion_evidence.cli review --run "$RUN_ROOT/review" --env-file .env --max-api-calls 10
"$PYTHON" -B -m motion_evidence.cli merge --run "$RUN_ROOT/review"
"$PYTHON" -B -m motion_evidence.cli report --run "$RUN_ROOT/review"
```

`.env.example`可复制为本地`.env`，填写模型和密钥；`.env`忽略且密钥不写日志。缺失配置时review会hold，merge仍能生成带未决标记的安全时间线。`--max-api-calls`是运行累计请求尝试上限，恢复不会清零；SDK自动重试关闭。

无配置也可先验证prepare→hold→未决保留merge→report。模拟API测试验证工具/图片/分段协议，不能作为真实AI识别成绩。

输出、证据PNG、API元数据和缓存全部放外部私有`RUN_ROOT`，不进入仓库。AI调用使用Base64原图，无需公开图片服务器。

## 文档

[产品](docs/product.md) · [架构](docs/architecture.md) · [核心方案](docs/core-design.md) · [协议](docs/contracts.md) · [验证](docs/validation.md) · [运行](docs/operations.md) · [路线](docs/roadmap.md)

当前是可验证的首版harness，不把离线模拟、hold合并或格式合法宣称为真实AI视觉效果验证。

已完成21项单元/合同测试，CPU reference和原生GPU两条跨仓库CLI链路均已通过合成烟测。复现入口为`tools/integration_smoke.py`，参见[验证说明](docs/validation.md)；实际OpenAI请求和语义质量仍单独hold验收。
